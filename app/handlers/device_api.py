"""Device (ESP32) endpoints: POST /v1/devices/register, /v1/alerts, /v1/devices/telemetry."""

import logging
import os
from datetime import timedelta

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from app.common.alerts import enqueue_alert
from app.common.aws import (
    DEVICES_TABLE,
    PROVISIONING_TABLE,
    devices_table,
    dynamodb,
    error_code,
    is_conditional_failure,
    provisioning_table,
    update_args,
)
from app.common.crypto import alert_id_for, hash_secret, new_device_secret, new_id, sha256_hex
from app.common.device_auth import authenticate_device
from app.common.http import ApiError, handler, json_body, response
from app.common.profiles import DEFAULT_LOW_BATTERY_THRESHOLD, get_profile
from app.common.timeutil import epoch, iso, now_iso, utcnow
from app.common.validate import optional_number, parse_timestamp, require_int, require_str, require_uuid
from app.handlers.provisioning import session_status

logger = logging.getLogger()

HARDWARE_ID = r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}"
VERSION = r"[0-9A-Za-z.+_-]{1,32}"
DEDUPE_KEY = r"[A-Za-z0-9_-]{1,64}"
DEFAULT_CHECKIN_SEC = int(os.environ.get("DEFAULT_CHECKIN_SEC", "3600"))
LOW_BATTERY_COOLDOWN = timedelta(hours=24)


def _hardware_registered(hardware_id):
    page = devices_table.query(
        IndexName="hardwareId-index",
        KeyConditionExpression=Key("hardwareId").eq(hardware_id),
        FilterExpression=Attr("status").eq("registered"),
    )
    return bool(page["Items"])


@handler
def register(event, context):
    body = json_body(event)
    token = require_uuid(body.get("pairingToken"), "pairingToken")
    hardware_id = require_str(body.get("hardwareId"), "hardwareId", max_len=128, pattern=HARDWARE_ID)
    firmware = require_str(body.get("firmwareVersion"), "firmwareVersion", max_len=32, pattern=VERSION)

    token_hash = sha256_hex(token)
    session = provisioning_table.get_item(Key={"pairingToken": token_hash}, ConsistentRead=True).get("Item")
    if not session:
        raise ApiError(400, "PAIRING_TOKEN_INVALID", "Pairing token is invalid")
    status = session_status(session)
    if status == "consumed":
        raise ApiError(400, "PAIRING_TOKEN_CONSUMED", "Pairing token was already used")
    if status == "expired":
        raise ApiError(400, "PAIRING_TOKEN_EXPIRED", "Pairing token has expired")
    if _hardware_registered(hardware_id):
        raise ApiError(409, "HARDWARE_ALREADY_REGISTERED", "This device is already paired; unpair it first")

    now = utcnow()
    device_id = new_id()
    secret = new_device_secret()
    device = {
        "deviceId": device_id,
        "ownerUserId": session["userId"],
        "status": "registered",
        "hardwareId": hardware_id,
        "firmwareVersion": firmware,
        "deviceSecretHash": hash_secret(secret),
        "nickname": "Alert Device",
        "pairedAt": iso(now),
        "lastSeenAt": iso(now),
    }
    try:
        # Consuming the token and creating the device happen atomically: a token pairs exactly one device
        dynamodb.meta.client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": PROVISIONING_TABLE,
                        "Key": {"pairingToken": token_hash},
                        "UpdateExpression": "SET #st = :consumed, deviceId = :d, consumedAt = :now",
                        "ConditionExpression": "#st = :pending AND expiresAtEpoch > :epoch",
                        "ExpressionAttributeNames": {"#st": "status"},
                        "ExpressionAttributeValues": {
                            ":consumed": "consumed",
                            ":pending": "pending",
                            ":d": device_id,
                            ":now": iso(now),
                            ":epoch": epoch(now),
                        },
                    }
                },
                {
                    "Put": {
                        "TableName": DEVICES_TABLE,
                        "Item": device,
                        "ConditionExpression": "attribute_not_exists(deviceId)",
                    }
                },
            ]
        )
    except ClientError as e:
        if error_code(e) == "TransactionCanceledException":
            raise ApiError(400, "PAIRING_TOKEN_CONSUMED", "Pairing token was already used")
        raise
    logger.info("Registered device %s for user %s", device_id, session["userId"])
    return response(201, {"deviceId": device_id, "deviceSecret": secret})


@handler
def post_alert(event, context):
    body = json_body(event)
    device_id = require_uuid(body.get("deviceId"), "deviceId")
    device = authenticate_device(event, device_id)
    # pressedAt is optional so a device without a synced clock can still raise an alert
    pressed_at = parse_timestamp(body.get("pressedAt"), "pressedAt", required=False) or now_iso()
    dedupe_key = require_str(body.get("dedupeKey"), "dedupeKey", max_len=64, pattern=DEDUPE_KEY, required=False)

    alert_id = enqueue_alert(
        alert_id=alert_id_for(device_id, dedupe_key) if dedupe_key else new_id(),
        alert_type="button_press",
        user_id=device["ownerUserId"],
        device_id=device_id,
        triggered_at=pressed_at,
        dedupe_key=dedupe_key,
    )
    devices_table.update_item(
        Key={"deviceId": device_id},
        UpdateExpression="SET lastSeenAt = :now",
        ExpressionAttributeValues={":now": now_iso()},
    )
    return response(202, {"alertId": alert_id})


def _effective_threshold(device):
    if device.get("lowBatteryThreshold") is not None:
        return device["lowBatteryThreshold"]
    profile = get_profile(device["ownerUserId"]) or {}
    return profile.get("lowBatteryThreshold", DEFAULT_LOW_BATTERY_THRESHOLD)


def _maybe_alert_low_battery(device, level, reported_at):
    now = utcnow()
    try:
        # Claim the 24h notification slot atomically so concurrent reports alert once
        devices_table.update_item(
            Key={"deviceId": device["deviceId"]},
            UpdateExpression="SET lowBatteryNotifiedAt = :now",
            ConditionExpression="attribute_not_exists(lowBatteryNotifiedAt) OR lowBatteryNotifiedAt < :cutoff",
            ExpressionAttributeValues={":now": iso(now), ":cutoff": iso(now - LOW_BATTERY_COOLDOWN)},
        )
    except ClientError as e:
        if is_conditional_failure(e):
            return
        raise
    enqueue_alert(
        alert_id=new_id(),
        alert_type="low_battery",
        user_id=device["ownerUserId"],
        device_id=device["deviceId"],
        triggered_at=reported_at,
        extra={"batteryLevel": level},
    )


@handler
def telemetry(event, context):
    body = json_body(event)
    device_id = require_uuid(body.get("deviceId"), "deviceId")
    device = authenticate_device(event, device_id)
    level = require_int(body.get("batteryLevel"), "batteryLevel", 0, 100, required=False)
    voltage = optional_number(body.get("batteryVoltage"), "batteryVoltage", 0, 10)
    rssi = require_int(body.get("rssi"), "rssi", -150, 0, required=False)
    reported_at = parse_timestamp(body.get("ts"), "ts", required=False) or now_iso()
    firmware = require_str(body.get("firmwareVersion"), "firmwareVersion", max_len=32, pattern=VERSION, required=False)
    # dedupeKey is accepted but not needed: telemetry updates are idempotent and
    # low-battery alerts are rate limited by lowBatteryNotifiedAt.

    updates = {"lastSeenAt": now_iso()}
    if level is not None:
        updates["batteryLevel"] = level
    if voltage is not None:
        updates["batteryVoltage"] = voltage
    if level is not None or voltage is not None:
        updates["batteryReportedAt"] = reported_at
    if rssi is not None:
        updates["rssi"] = rssi
    if firmware:
        updates["firmwareVersion"] = firmware

    # Operators can queue commands on the device row, e.g. [{"type": "set_checkin_interval", "sec": 1800}]
    commands = device.get("pendingCommands") or []
    args = update_args(updates, remove=["pendingCommands"] if commands else [])
    try:
        devices_table.update_item(
            Key={"deviceId": device_id},
            ConditionExpression="attribute_exists(deviceSecretHash)",  # unpaired mid-request
            **args,
        )
    except ClientError as e:
        if is_conditional_failure(e):
            raise ApiError(401, "DEVICE_REVOKED", "Device has been unpaired")
        raise

    if level is not None and level < _effective_threshold(device):
        _maybe_alert_low_battery(device, level, reported_at)

    return response(
        200,
        {
            "ack": True,
            "nextCheckInSec": int(device.get("checkinIntervalSec", DEFAULT_CHECKIN_SEC)),
            "commands": commands,
        },
    )
