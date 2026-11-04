"""UTC timestamp helpers. All stored/returned timestamps are ISO-8601 UTC with a Z suffix."""

from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_iso():
    return iso(utcnow())


def parse_iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def epoch(dt):
    return int(dt.timestamp())
