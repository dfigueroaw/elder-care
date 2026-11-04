"""POST /v1/provisioning/sessions and GET /v1/provisioning/sessions/{pairingToken}.

Only the SHA-256 of the pairing token is stored; the app keeps the plaintext in
memory and hands it to the device over the local (BLE / SoftAP) pairing channel.
"""

from datetime import timedelta

from app.common.aws import provisioning_table
from app.common.crypto import new_id, sha256_hex
from app.common.http import ApiError, caller_id, handler, path_param, response
from app.common.timeutil import epoch, iso, utcnow
from app.common.validate import require_uuid

SESSION_TTL = timedelta(minutes=5)


def session_status(item, now=None):
    # DynamoDB TTL deletion is lazy (up to ~48h), so expiry is enforced on read
    if item["status"] == "pending" and item["expiresAtEpoch"] <= epoch(now or utcnow()):
        return "expired"
    return item["status"]


@handler
def create_session(event, context):
    user_id = caller_id(event)
    token = new_id()
    now = utcnow()
    expires = now + SESSION_TTL
    provisioning_table.put_item(
        Item={
            "pairingToken": sha256_hex(token),
            "userId": user_id,
            "status": "pending",
            "createdAt": iso(now),
            "expiresAt": iso(expires),
            "expiresAtEpoch": epoch(expires),  # native TTL attribute (must be a Number)
        }
    )
    return response(201, {"pairingToken": token, "expiresAt": iso(expires)})


@handler
def get_session(event, context):
    user_id = caller_id(event)
    token = require_uuid(path_param(event, "pairingToken"), "pairingToken")
    item = provisioning_table.get_item(Key={"pairingToken": sha256_hex(token)}, ConsistentRead=True).get("Item")
    if not item or item["userId"] != user_id:
        # Also returned once TTL has purged an expired session; apps should treat 404 as expired
        raise ApiError(404, "NOT_FOUND", "Pairing session not found")
    body = {"status": session_status(item)}
    if item.get("deviceId"):
        body["deviceId"] = item["deviceId"]
    return response(200, body)
