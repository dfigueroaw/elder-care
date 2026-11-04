"""GET /v1/users/me and PATCH /v1/users/me/notifications."""

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from app.common.aws import profiles_table, update_args
from app.common.http import ApiError, caller_id, handler, json_body, jwt_claims, response
from app.common.profiles import DEFAULT_CHANNELS, channels_of, get_or_create_profile, public_profile
from app.common.push import sync_push_subscriptions
from app.common.timeutil import now_iso
from app.common.validate import invalid, require_int, require_str

E164 = r"\+[1-9]\d{6,14}"
# phoneE164 is accepted here too (not in the original spec) since SMS needs a number
ALLOWED_FIELDS = {"channels", "lowBatteryThreshold", "timezone", "phoneE164"}


def validate_timezone(value):
    value = require_str(value, "timezone", max_len=64)
    if available_timezones():
        try:
            ZoneInfo(value)
            return value
        except (ZoneInfoNotFoundError, ValueError):
            pass
    elif value == "UTC" or re.fullmatch(r"[A-Za-z_]+(/[A-Za-z0-9_+-]+)+", value):
        # No tz database on this runtime; fall back to a shape check
        return value
    raise invalid("timezone", "must be an IANA timezone such as America/Lima")


def validate_channels(value, current):
    if (
        not isinstance(value, dict)
        or set(value) - set(DEFAULT_CHANNELS)
        or any(not isinstance(v, bool) for v in value.values())
    ):
        raise invalid("channels", "must be an object with boolean push/email/sms flags")
    return {**current, **value}


@handler
def get_me(event, context):
    profile = get_or_create_profile(caller_id(event), jwt_claims(event))
    return response(200, public_profile(profile))


@handler
def update_notifications(event, context):
    user_id = caller_id(event)
    body = json_body(event)
    unknown = set(body) - ALLOWED_FIELDS
    if unknown:
        raise ApiError(400, "VALIDATION_ERROR", f"Unsupported fields: {', '.join(sorted(unknown))}")

    profile = get_or_create_profile(user_id, jwt_claims(event))
    old_channels = channels_of(profile)
    channels = validate_channels(body["channels"], old_channels) if "channels" in body else old_channels

    updates = {"channels": channels, "updatedAt": now_iso()}
    remove = []
    if "lowBatteryThreshold" in body:
        updates["lowBatteryThreshold"] = require_int(body["lowBatteryThreshold"], "lowBatteryThreshold", 1, 100)
    if "timezone" in body:
        updates["timezone"] = validate_timezone(body["timezone"])
    if "phoneE164" in body:
        if body["phoneE164"] is None:
            remove.append("phoneE164")
        else:
            updates["phoneE164"] = require_str(body["phoneE164"], "phoneE164", max_len=16, pattern=E164)

    phone = None if remove else updates.get("phoneE164", profile.get("phoneE164"))
    if channels["sms"] and not phone:
        raise ApiError(400, "PHONE_REQUIRED", "Set phoneE164 before enabling SMS notifications")

    result = profiles_table.update_item(
        Key={"userId": user_id}, ReturnValues="ALL_NEW", **update_args(updates, remove)
    )
    if channels["push"] != old_channels["push"]:
        sync_push_subscriptions(user_id, channels["push"])
    return response(200, public_profile(result["Attributes"]))
