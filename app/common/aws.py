"""AWS clients and DynamoDB tables, created once per Lambda container."""

import os

import boto3
from botocore.exceptions import ClientError

DEVICES_TABLE = os.environ.get("DEVICES_TABLE", "")
USER_PROFILES_TABLE = os.environ.get("USER_PROFILES_TABLE", "")
PUSH_ENDPOINTS_TABLE = os.environ.get("PUSH_ENDPOINTS_TABLE", "")
ALERTS_TABLE = os.environ.get("ALERTS_TABLE", "")
PROVISIONING_TABLE = os.environ.get("PROVISIONING_TABLE", "")
ALERTS_QUEUE_URL = os.environ.get("ALERTS_QUEUE_URL", "")
USER_TOPIC_PREFIX = os.environ.get("USER_TOPIC_PREFIX", "")

dynamodb = boto3.resource("dynamodb")
devices_table = dynamodb.Table(DEVICES_TABLE)
profiles_table = dynamodb.Table(USER_PROFILES_TABLE)
push_table = dynamodb.Table(PUSH_ENDPOINTS_TABLE)
alerts_table = dynamodb.Table(ALERTS_TABLE)
provisioning_table = dynamodb.Table(PROVISIONING_TABLE)

sns = boto3.client("sns")
sqs = boto3.client("sqs")
ses = boto3.client("ses")


def error_code(error):
    return error.response.get("Error", {}).get("Code", "")


def is_conditional_failure(error):
    return isinstance(error, ClientError) and error_code(error) == "ConditionalCheckFailedException"


def query_all(table, **kwargs):
    items = []
    while True:
        page = table.query(**kwargs)
        items.extend(page["Items"])
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def update_args(set_values, remove=()):
    """Build UpdateExpression kwargs from {attr: value} plus attributes to REMOVE."""
    names, values, set_parts, remove_parts = {}, {}, [], []
    for i, (attr, value) in enumerate(set_values.items()):
        names[f"#s{i}"] = attr
        values[f":s{i}"] = value
        set_parts.append(f"#s{i} = :s{i}")
    for i, attr in enumerate(remove):
        names[f"#r{i}"] = attr
        remove_parts.append(f"#r{i}")
    expression = ""
    if set_parts:
        expression += "SET " + ", ".join(set_parts)
    if remove_parts:
        expression += " REMOVE " + ", ".join(remove_parts)
    args = {"UpdateExpression": expression.strip(), "ExpressionAttributeNames": names}
    if values:
        args["ExpressionAttributeValues"] = values
    return args
