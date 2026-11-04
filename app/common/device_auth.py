"""Device authentication (v1): Authorization: Bearer <deviceSecret>.

The secret is checked against the scrypt hash stored on the Devices row named by
the request's deviceId. v2 (HMAC of {timestamp}.{method}.{path}.{body} with
X-Device-Timestamp / X-Device-Nonce) can be added here without touching handlers.
"""

from app.common.aws import devices_table
from app.common.crypto import verify_secret
from app.common.http import ApiError, bearer_token


def authenticate_device(event, device_id):
    secret = bearer_token(event)
    device = devices_table.get_item(Key={"deviceId": device_id}, ConsistentRead=True).get("Item")
    if not device:
        raise ApiError(401, "UNAUTHORIZED", "Invalid device credentials")
    if device.get("status") == "revoked":
        # Tells firmware to wipe its credentials and return to pairing mode
        raise ApiError(401, "DEVICE_REVOKED", "Device has been unpaired")
    if not verify_secret(secret, device.get("deviceSecretHash", "")):
        raise ApiError(401, "UNAUTHORIZED", "Invalid device credentials")
    return device
