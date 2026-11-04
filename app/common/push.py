"""Per-user SNS topics and platform endpoint bookkeeping."""

import logging

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from app.common.aws import USER_TOPIC_PREFIX, error_code, push_table, query_all, sns

logger = logging.getLogger()


def ensure_user_topic(user_id):
    """CreateTopic is idempotent, so this both creates and looks up the topic ARN."""
    return sns.create_topic(Name=f"{USER_TOPIC_PREFIX}-{user_id}")["TopicArn"]


def subscribe_endpoint(topic_arn, endpoint_arn):
    return sns.subscribe(
        TopicArn=topic_arn,
        Protocol="application",
        Endpoint=endpoint_arn,
        ReturnSubscriptionArn=True,
    )["SubscriptionArn"]


def safe_unsubscribe(subscription_arn):
    try:
        sns.unsubscribe(SubscriptionArn=subscription_arn)
    except ClientError as e:
        if error_code(e) != "NotFound":
            raise


def user_endpoints(user_id):
    return query_all(push_table, IndexName="userId-index", KeyConditionExpression=Key("userId").eq(user_id))


def endpoints_by_arn(endpoint_arn):
    return query_all(
        push_table, IndexName="endpointArn-index", KeyConditionExpression=Key("endpointArn").eq(endpoint_arn)
    )


def remove_endpoint(record):
    """Unsubscribe, delete the SNS platform endpoint and drop the PushEndpoints row."""
    if record.get("subscriptionArn"):
        safe_unsubscribe(record["subscriptionArn"])
    sns.delete_endpoint(EndpointArn=record["endpointArn"])  # idempotent
    push_table.delete_item(Key={"endpointId": record["endpointId"]})


def sync_push_subscriptions(user_id, enabled):
    """Subscribe/unsubscribe all of the user's endpoints when the push channel is toggled."""
    endpoints = user_endpoints(user_id)
    topic_arn = ensure_user_topic(user_id) if enabled and endpoints else None
    for record in endpoints:
        key = {"endpointId": record["endpointId"]}
        if enabled and not record.get("subscriptionArn"):
            subscription_arn = subscribe_endpoint(topic_arn, record["endpointArn"])
            push_table.update_item(
                Key=key,
                UpdateExpression="SET subscriptionArn = :s",
                ExpressionAttributeValues={":s": subscription_arn},
            )
        elif not enabled and record.get("subscriptionArn"):
            safe_unsubscribe(record["subscriptionArn"])
            push_table.update_item(Key=key, UpdateExpression="REMOVE subscriptionArn")
