"""UserProfiles access with spec defaults."""

from botocore.exceptions import ClientError

from app.common.aws import is_conditional_failure, profiles_table
from app.common.timeutil import now_iso

DEFAULT_CHANNELS = {"push": True, "email": True, "sms": False}
DEFAULT_LOW_BATTERY_THRESHOLD = 20


def get_profile(user_id):
    return profiles_table.get_item(Key={"userId": user_id}, ConsistentRead=True).get("Item")


def get_or_create_profile(user_id, claims):
    """Profiles are created lazily on first authenticated call, seeded from the ID token."""
    item = get_profile(user_id)
    if item:
        return item
    now = now_iso()
    item = {
        "userId": user_id,
        "email": claims.get("email", ""),
        "timezone": "UTC",
        "channels": dict(DEFAULT_CHANNELS),
        "lowBatteryThreshold": DEFAULT_LOW_BATTERY_THRESHOLD,
        "createdAt": now,
        "updatedAt": now,
    }
    if claims.get("phone_number") and str(claims.get("phone_number_verified")).lower() == "true":
        item["phoneE164"] = claims["phone_number"]
    try:
        profiles_table.put_item(Item=item, ConditionExpression="attribute_not_exists(userId)")
    except ClientError as e:
        if not is_conditional_failure(e):
            raise
        return get_profile(user_id)
    return item


def channels_of(profile):
    return {**DEFAULT_CHANNELS, **((profile or {}).get("channels") or {})}


def public_profile(item):
    out = {
        "userId": item["userId"],
        "email": item.get("email", ""),
        "timezone": item.get("timezone", "UTC"),
        "channels": channels_of(item),
        "lowBatteryThreshold": item.get("lowBatteryThreshold", DEFAULT_LOW_BATTERY_THRESHOLD),
    }
    if item.get("phoneE164"):
        out["phoneE164"] = item["phoneE164"]
    return out
