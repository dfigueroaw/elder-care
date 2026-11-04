"""SQS FIFO consumer: fans an alert out to push (SNS topic), email (SES) and SMS (SNS)."""

import json
import logging
import os
from datetime import timezone
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

from app.common.aws import alerts_table, devices_table, error_code, is_conditional_failure, ses, sns
from app.common.profiles import channels_of, get_profile
from app.common.push import ensure_user_topic, user_endpoints
from app.common.timeutil import now_iso, parse_iso

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MAX_ATTEMPTS = 3  # keep in sync with the queue's maxReceiveCount
SES_SENDER = os.environ.get("SES_SENDER", "")
SES_TEMPLATE = os.environ.get("SES_TEMPLATE", "")


class SkipChannel(Exception):
    """The channel is enabled but cannot be used (no endpoint, no address, not configured)."""


class TransientDeliveryError(Exception):
    pass


def local_time(timestamp, tz_name):
    dt = parse_iso(timestamp)
    try:
        dt = dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M %Z")


def render(alert, device, profile):
    name = device.get("nickname") or "Alert Device"
    when = local_time(alert["triggeredAt"], profile.get("timezone", "UTC"))
    if alert["type"] == "low_battery":
        title = f"{name}: low battery"
        body = f"Battery is at {alert.get('batteryLevel', '?')}%. Please recharge it soon."
    else:
        title = f"{name}: alert button pressed"
        body = f"The alert button was pressed at {when}."
    return {"subject": title, "title": title, "body": body, "deviceName": name, "time": when}


def send_push(alert, content, profile):
    if not any(e.get("subscriptionArn") for e in user_endpoints(alert["userId"])):
        raise SkipChannel("no subscribed push endpoints")
    data = {"alertId": alert["alertId"], "type": alert["type"], "deviceId": alert["deviceId"]}
    apns = json.dumps(
        {"aps": {"alert": {"title": content["title"], "body": content["body"]}, "sound": "default"}, **data}
    )
    gcm = json.dumps(
        {"notification": {"title": content["title"], "body": content["body"]}, "data": data, "priority": "high"}
    )
    message = {"default": content["body"], "APNS": apns, "APNS_SANDBOX": apns, "GCM": gcm}
    return sns.publish(
        TopicArn=ensure_user_topic(alert["userId"]), Message=json.dumps(message), MessageStructure="json"
    )["MessageId"]


def send_email(alert, content, profile):
    if not profile.get("email"):
        raise SkipChannel("no email address")
    if not SES_SENDER:
        raise SkipChannel("email sender not configured")
    return ses.send_templated_email(
        Source=SES_SENDER,
        Destination={"ToAddresses": [profile["email"]]},
        Template=SES_TEMPLATE,
        TemplateData=json.dumps(content),
    )["MessageId"]


def send_sms(alert, content, profile):
    if not profile.get("phoneE164"):
        raise SkipChannel("no phone number")
    return sns.publish(
        PhoneNumber=profile["phoneE164"],
        Message=f"{content['title']}: {content['body']}",
        MessageAttributes={"AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"}},
    )["MessageId"]


SENDERS = {"push": send_push, "email": send_email, "sms": send_sms}


def attempt(sender, alert, content, profile):
    try:
        return {"status": "sent", "messageId": sender(alert, content, profile)}
    except SkipChannel as e:
        return {"status": "skipped", "error": str(e)}
    except ClientError as e:
        logger.warning("Delivery via %s failed for alert %s: %s", sender.__name__, alert["alertId"], e)
        return {"status": "failed", "error": f"{error_code(e)}: {e}"[:300]}


def finish(alert, status, delivery, reason=None):
    values = {":st": status, ":d": delivery, ":now": now_iso(), ":queued": "queued"}
    expression = "SET #st = :st, delivery = :d, processedAt = :now"
    if reason:
        expression += ", suppressedReason = :r"
        values[":r"] = reason
    try:
        alerts_table.update_item(
            Key={"alertId": alert["alertId"]},
            UpdateExpression=expression,
            ConditionExpression="#st = :queued",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues=values,
        )
    except ClientError as e:
        if not is_conditional_failure(e):
            raise


def process(record):
    message = json.loads(record["body"])
    alert = alerts_table.get_item(Key={"alertId": message["alertId"]}, ConsistentRead=True).get("Item")
    if not alert:
        logger.warning("Alert %s not found; dropping message", message["alertId"])
        return
    if alert["status"] != "queued":
        return  # duplicate delivery of an already-processed alert

    device = devices_table.get_item(Key={"deviceId": alert["deviceId"]}).get("Item")
    if not device or device.get("status") != "registered" or device.get("ownerUserId") != alert["userId"]:
        return finish(alert, "suppressed", {}, reason="device_unpaired")
    profile = get_profile(alert["userId"]) or {}
    channels = channels_of(profile)
    if not any(channels.values()):
        return finish(alert, "suppressed", {}, reason="all_channels_disabled")

    content = render(alert, device, profile)
    delivery = {
        channel: attempt(sender, alert, content, profile)
        for channel, sender in SENDERS.items()
        if channels[channel]
    }
    sent = [d for d in delivery.values() if d["status"] == "sent"]
    failed = [d for d in delivery.values() if d["status"] == "failed"]

    final_attempt = int(record.get("attributes", {}).get("ApproximateReceiveCount", "1")) >= MAX_ATTEMPTS
    if failed and not sent and not final_attempt:
        # Nothing went out: let SQS retry rather than re-sending channels that succeeded
        raise TransientDeliveryError(f"All channels failed for alert {alert['alertId']}")

    status = ("sent" if not failed else "partial") if sent else "failed"
    finish(alert, status, delivery)
    logger.info("Alert %s -> %s %s", alert["alertId"], status, json.dumps(delivery))


def handle(event, context):
    failures = []
    for record in event["Records"]:
        if failures:
            # FIFO: once a message fails, the rest of the batch must be retried after it, in order
            failures.append({"itemIdentifier": record["messageId"]})
            continue
        try:
            process(record)
        except Exception:
            logger.exception("Failed to process message %s", record["messageId"])
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
