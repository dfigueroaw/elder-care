"""GET /v1/alerts?limit=20&cursor=... (newest first)."""

import base64
import binascii
import json

from boto3.dynamodb.conditions import Key

from app.common.aws import alerts_table
from app.common.http import ApiError, caller_id, handler, query_param, response

CURSOR_KEYS = {"alertId", "userId", "receivedAt"}


def encode_cursor(last_key):
    if not last_key:
        return None
    return base64.urlsafe_b64encode(json.dumps(last_key).encode()).decode().rstrip("=")


def decode_cursor(cursor, user_id):
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        key = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, binascii.Error):
        key = None
    if not isinstance(key, dict) or set(key) != CURSOR_KEYS or key["userId"] != user_id:
        raise ApiError(400, "INVALID_CURSOR", "cursor is invalid")
    return key


def public_alert(item):
    return {
        "alertId": item["alertId"],
        "type": item["type"],
        "deviceId": item["deviceId"],
        "triggeredAt": item.get("triggeredAt") or item.get("pressedAt"),
        "status": item["status"],
        "delivery": item.get("delivery", {}),
    }


@handler
def list_alerts(event, context):
    user_id = caller_id(event)
    try:
        limit = int(query_param(event, "limit", "20"))
    except ValueError:
        limit = 0
    if not 1 <= limit <= 100:
        raise ApiError(400, "VALIDATION_ERROR", "limit must be an integer between 1 and 100")

    kwargs = {
        "IndexName": "userId-receivedAt-index",
        "KeyConditionExpression": Key("userId").eq(user_id),
        "ScanIndexForward": False,
        "Limit": limit,
    }
    cursor = query_param(event, "cursor")
    if cursor:
        kwargs["ExclusiveStartKey"] = decode_cursor(cursor, user_id)
    page = alerts_table.query(**kwargs)
    return response(
        200,
        {
            "items": [public_alert(i) for i in page["Items"]],
            "nextCursor": encode_cursor(page.get("LastEvaluatedKey")),
        },
    )
