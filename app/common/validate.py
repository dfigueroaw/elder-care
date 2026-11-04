"""Input validation helpers. Each raises a 400 ApiError on bad input."""

import re
import uuid
from decimal import Decimal

from app.common.http import ApiError
from app.common.timeutil import iso, parse_iso


def invalid(field, message):
    return ApiError(400, "VALIDATION_ERROR", f"{field} {message}")


def require_uuid(value, field):
    if not isinstance(value, str):
        raise invalid(field, "must be a UUID")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise invalid(field, "must be a UUID")


def require_str(value, field, max_len=256, pattern=None, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise invalid(field, "is required")
    value = value.strip()
    if len(value) > max_len:
        raise invalid(field, f"must be at most {max_len} characters")
    if pattern and not re.fullmatch(pattern, value):
        raise invalid(field, "has an invalid format")
    return value


def require_int(value, field, lo, hi, required=True):
    if value is None and not required:
        return None
    if isinstance(value, Decimal) and value == value.to_integral_value():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise invalid(field, f"must be an integer between {lo} and {hi}")
    return value


def optional_number(value, field, lo, hi):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)) or not lo <= value <= hi:
        raise invalid(field, f"must be a number between {lo} and {hi}")
    return Decimal(str(value))


def parse_timestamp(value, field, required=True):
    """Validate an ISO-8601 timestamp with timezone and normalize it to UTC."""
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise invalid(field, "must be an ISO-8601 timestamp")
    try:
        dt = parse_iso(value)
    except ValueError:
        raise invalid(field, "must be an ISO-8601 timestamp")
    if dt.tzinfo is None:
        raise invalid(field, "must include a timezone, e.g. 2026-01-01T00:00:00Z")
    return iso(dt)
