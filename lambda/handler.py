"""Basic healthcheck Lambda.

Returns 200 with service status. If TARGET_URL is set, it also checks that
URL and reports 503 when the target is unreachable or returns a non-2xx code.
"""

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SERVICE_NAME = os.environ.get("SERVICE_NAME", "healthcheck")
TARGET_URL = os.environ.get("TARGET_URL", "")
TIMEOUT_SECONDS = float(os.environ.get("TIMEOUT_SECONDS", "5"))


def check_target(url):
    start = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as resp:
            status_code = resp.status
    except urllib.error.HTTPError as e:
        status_code = e.code
    except Exception as e:
        return {
            "url": url,
            "healthy": False,
            "error": str(e),
            "latency_ms": round((time.monotonic() - start) * 1000),
        }

    return {
        "url": url,
        "healthy": 200 <= status_code < 300,
        "status_code": status_code,
        "latency_ms": round((time.monotonic() - start) * 1000),
    }


def lambda_handler(event, context):
    body = {
        "service": SERVICE_NAME,
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if TARGET_URL:
        target = check_target(TARGET_URL)
        body["target"] = target
        if not target["healthy"]:
            body["status"] = "degraded"

    print(json.dumps(body))

    return {
        "statusCode": 200 if body["status"] == "ok" else 503,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
