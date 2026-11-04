"""Cleans up push endpoints that APNs/FCM have disabled (uninstalled app, expired token).

The SNS platform applications publish EventEndpointUpdated / EventEndpointDeleted /
EventDeliveryFailure notifications to the push-events topic, which triggers this function.
"""

import json
import logging

from botocore.exceptions import ClientError

from app.common.aws import error_code, sns
from app.common.push import endpoints_by_arn, remove_endpoint

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle(event, context):
    for record in event["Records"]:
        try:
            message = json.loads(record["Sns"]["Message"])
        except (KeyError, ValueError):
            continue
        endpoint_arn = message.get("EndpointArn") if isinstance(message, dict) else None
        if not endpoint_arn:
            continue
        try:
            attributes = sns.get_endpoint_attributes(EndpointArn=endpoint_arn)["Attributes"]
            enabled = attributes.get("Enabled") == "true"
        except ClientError as e:
            if error_code(e) != "NotFound":
                raise
            enabled = False
        if enabled:
            continue
        for push_record in endpoints_by_arn(endpoint_arn):
            remove_endpoint(push_record)
            logger.info("Removed disabled push endpoint %s (user %s)", endpoint_arn, push_record["userId"])
