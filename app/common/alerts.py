"""Alert creation + enqueueing to the alerts FIFO queue."""

from datetime import timedelta

from botocore.exceptions import ClientError

from app.common.aws import ALERTS_QUEUE_URL, alerts_table, is_conditional_failure, sqs
from app.common.http import to_json
from app.common.timeutil import epoch, iso, utcnow

RETENTION = timedelta(days=90)


def enqueue_alert(*, alert_id, alert_type, user_id, device_id, triggered_at, dedupe_key=None, extra=None):
    """Store the alert and enqueue it. Idempotent per alert_id; returns the alertId."""
    now = utcnow()
    item = {
        "alertId": alert_id,
        "type": alert_type,
        "userId": user_id,
        "deviceId": device_id,
        "triggeredAt": triggered_at,
        "receivedAt": iso(now),
        "status": "queued",
        "delivery": {},
        "ttl": epoch(now + RETENTION),
    }
    if alert_type == "button_press":
        item["pressedAt"] = triggered_at
    if dedupe_key:
        item["dedupeKey"] = dedupe_key
    if extra:
        item.update(extra)

    try:
        alerts_table.put_item(Item=item, ConditionExpression="attribute_not_exists(alertId)")
    except ClientError as e:
        if not is_conditional_failure(e):
            raise
        item = alerts_table.get_item(Key={"alertId": alert_id}, ConsistentRead=True)["Item"]
        if item["status"] != "queued":
            return alert_id
        # A previous attempt may have stored the row but failed before reaching SQS:
        # re-send. FIFO deduplication (5 min) and the worker's status check absorb duplicates.

    message = {
        "alertId": alert_id,
        "type": alert_type,
        "userId": user_id,
        "deviceId": device_id,
        "triggeredAt": item["triggeredAt"],
    }
    if alert_type == "button_press":
        message["pressedAt"] = item["triggeredAt"]
    if item.get("dedupeKey"):
        message["dedupeKey"] = item["dedupeKey"]
    sqs.send_message(
        QueueUrl=ALERTS_QUEUE_URL,
        MessageBody=to_json(message),
        MessageGroupId=device_id,
        MessageDeduplicationId=alert_id,
    )
    return alert_id
