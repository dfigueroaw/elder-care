"""POST /v1/users/me/push-tokens and DELETE /v1/users/me/push-tokens/{endpointId}."""

import os

from app.common.aws import push_table, sns
from app.common.crypto import new_id, sha256_hex
from app.common.http import ApiError, caller_id, handler, json_body, jwt_claims, path_param, response
from app.common.profiles import channels_of, get_or_create_profile
from app.common.push import endpoints_by_arn, ensure_user_topic, remove_endpoint, safe_unsubscribe, subscribe_endpoint
from app.common.timeutil import now_iso
from app.common.validate import invalid, require_str, require_uuid

PLATFORM_APP_ENV = {"ios": "APNS_PLATFORM_APP_ARN", "android": "GCM_PLATFORM_APP_ARN"}


@handler
def register_token(event, context):
    user_id = caller_id(event)
    body = json_body(event)
    platform = body.get("platform")
    if platform not in PLATFORM_APP_ENV:
        raise invalid("platform", "must be 'ios' or 'android'")
    token = require_str(body.get("token"), "token", max_len=4096)
    app_version = require_str(body.get("appVersion"), "appVersion", max_len=32, required=False)

    app_arn = os.environ.get(PLATFORM_APP_ENV[platform], "")
    if not app_arn:
        raise ApiError(503, "PUSH_NOT_CONFIGURED", f"Push notifications are not configured for {platform}")

    # Returns the existing endpoint when this token is already registered
    endpoint_arn = sns.create_platform_endpoint(PlatformApplicationArn=app_arn, Token=token)["EndpointArn"]
    sns.set_endpoint_attributes(EndpointArn=endpoint_arn, Attributes={"Token": token, "Enabled": "true"})

    profile = get_or_create_profile(user_id, jwt_claims(event))
    now = now_iso()
    endpoint_id, subscription_arn, created_at = None, None, now
    for record in endpoints_by_arn(endpoint_arn):
        if record["userId"] == user_id and endpoint_id is None:
            endpoint_id = record["endpointId"]
            subscription_arn = record.get("subscriptionArn")
            created_at = record.get("createdAt", now)
        else:
            # Same phone now signed in to another account: detach it from the old user
            if record.get("subscriptionArn"):
                safe_unsubscribe(record["subscriptionArn"])
            push_table.delete_item(Key={"endpointId": record["endpointId"]})

    if channels_of(profile)["push"] and not subscription_arn:
        subscription_arn = subscribe_endpoint(ensure_user_topic(user_id), endpoint_arn)

    item = {
        "endpointId": endpoint_id or new_id(),
        "userId": user_id,
        "platform": platform,
        "tokenHash": sha256_hex(token),
        "endpointArn": endpoint_arn,
        "createdAt": created_at,
        "updatedAt": now,
    }
    if app_version:
        item["appVersion"] = app_version
    if subscription_arn:
        item["subscriptionArn"] = subscription_arn
    push_table.put_item(Item=item)
    return response(201, {"endpointId": item["endpointId"], "endpointArn": endpoint_arn})


@handler
def delete_token(event, context):
    user_id = caller_id(event)
    endpoint_id = require_uuid(path_param(event, "endpointId"), "endpointId")
    record = push_table.get_item(Key={"endpointId": endpoint_id}).get("Item")
    if not record or record["userId"] != user_id:
        raise ApiError(404, "NOT_FOUND", "Push token not found")
    remove_endpoint(record)
    return response(204)
