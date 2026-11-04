"""Request/response helpers for API Gateway HTTP API (payload format 2.0)."""

import base64
import functools
import json
import logging
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class ApiError(Exception):
    """Raised by handlers to return a spec-shaped error response."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def to_json(value):
    return json.dumps(value, default=_json_default)


def response(status, body=None):
    resp = {"statusCode": status, "headers": {"Content-Type": "application/json"}}
    if body is not None:
        resp["body"] = to_json(body)
    return resp


def error_response(status, code, message):
    return response(status, {"error": {"code": code, "message": message}})


def handler(fn):
    """Wrap a Lambda handler so ApiError and unexpected exceptions become JSON errors."""

    @functools.wraps(fn)
    def wrapper(event, context):
        try:
            return fn(event, context)
        except ApiError as e:
            return error_response(e.status, e.code, e.message)
        except Exception:
            logger.exception("Unhandled error")
            return error_response(500, "INTERNAL_ERROR", "Internal server error")

    return wrapper


def json_body(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    if not raw.strip():
        return {}
    try:
        # Decimal keeps floats DynamoDB-compatible
        body = json.loads(raw, parse_float=Decimal)
    except ValueError:
        raise ApiError(400, "INVALID_JSON", "Request body must be valid JSON")
    if not isinstance(body, dict):
        raise ApiError(400, "INVALID_JSON", "Request body must be a JSON object")
    return body


def path_param(event, name):
    return (event.get("pathParameters") or {}).get(name)


def query_param(event, name, default=None):
    return (event.get("queryStringParameters") or {}).get(name, default)


def jwt_claims(event):
    try:
        return event["requestContext"]["authorizer"]["jwt"]["claims"]
    except (KeyError, TypeError):
        raise ApiError(401, "UNAUTHORIZED", "Missing or invalid token")


def caller_id(event):
    """Cognito sub of the authenticated mobile user."""
    return jwt_claims(event)["sub"]


def bearer_token(event):
    # HTTP API lowercases header names
    auth = (event.get("headers") or {}).get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "UNAUTHORIZED", "Missing bearer credentials")
    return token.strip()
