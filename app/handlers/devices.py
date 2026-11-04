"""Mobile device management: GET /v1/devices, PATCH and DELETE /v1/devices/{deviceId}."""

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from app.common.aws import devices_table, is_conditional_failure, query_all
from app.common.http import ApiError, caller_id, handler, json_body, path_param, query_param, response
from app.common.timeutil import now_iso
from app.common.validate import require_str, require_uuid

PUBLIC_FIELDS = (
    "deviceId",
    "nickname",
    "status",
    "pairedAt",
    "lastSeenAt",
    "batteryLevel",
    "batteryReportedAt",
    "firmwareVersion",
)
PATCHABLE_FIELDS = {"nickname"}


def public_device(item):
    return {field: item[field] for field in PUBLIC_FIELDS if field in item}


def _not_found():
    return ApiError(404, "NOT_FOUND", "Device not found")


@handler
def list_devices(event, context):
    user_id = caller_id(event)
    include_revoked = query_param(event, "includeRevoked") == "true"
    items = query_all(
        devices_table, IndexName="ownerUserId-index", KeyConditionExpression=Key("ownerUserId").eq(user_id)
    )
    devices = [public_device(i) for i in items if include_revoked or i.get("status") != "revoked"]
    return response(200, {"devices": devices})


@handler
def update_device(event, context):
    user_id = caller_id(event)
    device_id = require_uuid(path_param(event, "deviceId"), "deviceId")
    body = json_body(event)
    unknown = set(body) - PATCHABLE_FIELDS
    if unknown:
        raise ApiError(400, "VALIDATION_ERROR", f"Unsupported fields: {', '.join(sorted(unknown))}")
    nickname = require_str(body.get("nickname"), "nickname", max_len=64)
    try:
        result = devices_table.update_item(
            Key={"deviceId": device_id},
            UpdateExpression="SET nickname = :n",
            ConditionExpression="ownerUserId = :u AND #st = :registered",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":n": nickname, ":u": user_id, ":registered": "registered"},
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if is_conditional_failure(e):
            raise _not_found()
        raise
    return response(200, public_device(result["Attributes"]))


@handler
def delete_device(event, context):
    """Unpair. Notification subscriptions are per user, not per device, so nothing else to
    clear; the alerts worker suppresses any still-queued alerts from a revoked device."""
    user_id = caller_id(event)
    device_id = require_uuid(path_param(event, "deviceId"), "deviceId")
    try:
        devices_table.update_item(
            Key={"deviceId": device_id},
            UpdateExpression="SET #st = :revoked, revokedAt = :now REMOVE deviceSecretHash",
            ConditionExpression="ownerUserId = :u",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":revoked": "revoked", ":now": now_iso(), ":u": user_id},
        )
    except ClientError as e:
        if is_conditional_failure(e):
            raise _not_found()
        raise
    return response(204)
