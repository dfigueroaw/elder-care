#!/usr/bin/env bash
# One-time setup for the parts CloudFormation cannot create:
#   - SNS platform applications (APNs / FCM) wired to the stack's push-events topic
#   - Account-level SMS settings (spend limit, transactional type)
#   - SES sender identity verification
#
# Run after the first `serverless deploy`, then redeploy with the printed --param flags.
#
# Usage (all inputs via env; anything unset is skipped):
#   STAGE=dev REGION=us-east-1 \
#   APNS_KEY_FILE=AuthKey_ABC123.p8 APNS_KEY_ID=ABC123 APNS_TEAM_ID=TEAM123 APNS_BUNDLE_ID=com.example.alert \
#   FCM_SERVICE_ACCOUNT_FILE=firebase-sa.json \
#   SES_SENDER=alerts@example.com SMS_MONTHLY_SPEND_LIMIT=1 \
#   ./scripts/setup-notifications.sh

set -euo pipefail

STAGE="${STAGE:-dev}"
REGION="${REGION:-us-east-1}"
SERVICE="alert-button"
PREFIX="$SERVICE-$STAGE"
STACK="$SERVICE-$STAGE"
# Production stage talks to the production APNs gateway, every other stage to the sandbox
APNS_PLATFORM="${APNS_PLATFORM:-$([ "$STAGE" = prod ] && echo APNS || echo APNS_SANDBOX)}"

command -v aws >/dev/null || { echo "error: aws CLI is not installed" >&2; exit 1; }
command -v python3 >/dev/null || { echo "error: python3 is required" >&2; exit 1; }
aws sts get-caller-identity >/dev/null || { echo "error: AWS credentials not configured" >&2; exit 1; }

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
EVENTS_TOPIC="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='PushEventsTopicArn'].OutputValue" --output text)"
[ -n "$EVENTS_TOPIC" ] && [ "$EVENTS_TOPIC" != None ] || { echo "error: deploy the stack first" >&2; exit 1; }

PARAMS=()

# Writes the platform-application attributes as a JSON file (credentials are multi-line)
attributes_file() {
  local out="$1"; shift
  python3 - "$out" "$@" <<'EOF'
import json, sys
out, *pairs = sys.argv[1:]
attrs = {}
for pair in pairs:
    key, _, value = pair.partition("=")
    if value.startswith("@"):
        with open(value[1:]) as f:
            value = f.read()
    attrs[key] = value
with open(out, "w") as f:
    json.dump(attrs, f)
EOF
}

# Creates the platform application, or updates its attributes when it already exists
upsert_platform_app() {
  local name="$1" platform="$2" attrs="$3"
  local arn="arn:aws:sns:$REGION:$ACCOUNT:app/$platform/$name"
  if aws sns get-platform-application-attributes --region "$REGION" --platform-application-arn "$arn" >/dev/null 2>&1; then
    aws sns set-platform-application-attributes --region "$REGION" --platform-application-arn "$arn" \
      --attributes "file://$attrs" >/dev/null
  else
    arn="$(aws sns create-platform-application --region "$REGION" --name "$name" --platform "$platform" \
      --attributes "file://$attrs" --query PlatformApplicationArn --output text)"
  fi
  echo "$arn"
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
EVENT_ATTRS=("EventEndpointUpdated=$EVENTS_TOPIC" "EventEndpointDeleted=$EVENTS_TOPIC" "EventDeliveryFailure=$EVENTS_TOPIC")

if [ -n "${APNS_KEY_FILE:-}" ]; then
  : "${APNS_KEY_ID:?APNS_KEY_ID is required}" "${APNS_TEAM_ID:?APNS_TEAM_ID is required}" "${APNS_BUNDLE_ID:?APNS_BUNDLE_ID is required}"
  attributes_file "$TMP/apns.json" \
    "PlatformCredential=@$APNS_KEY_FILE" "PlatformPrincipal=$APNS_KEY_ID" \
    "ApplePlatformTeamID=$APNS_TEAM_ID" "ApplePlatformBundleID=$APNS_BUNDLE_ID" "${EVENT_ATTRS[@]}"
  APNS_ARN="$(upsert_platform_app "$PREFIX-ios" "$APNS_PLATFORM" "$TMP/apns.json")"
  echo "APNs ($APNS_PLATFORM): $APNS_ARN"
  PARAMS+=("--param=apnsPlatformAppArn=$APNS_ARN")
fi

if [ -n "${FCM_SERVICE_ACCOUNT_FILE:-}" ]; then
  # FCM HTTP v1: the credential is the Firebase service-account JSON
  attributes_file "$TMP/fcm.json" "PlatformCredential=@$FCM_SERVICE_ACCOUNT_FILE" "${EVENT_ATTRS[@]}"
  GCM_ARN="$(upsert_platform_app "$PREFIX-android" GCM "$TMP/fcm.json")"
  echo "FCM: $GCM_ARN"
  PARAMS+=("--param=gcmPlatformAppArn=$GCM_ARN")
fi

if [ -n "${SMS_MONTHLY_SPEND_LIMIT:-}" ]; then
  # Account-wide setting; limits above $1 need an AWS support request first
  aws sns set-sms-attributes --region "$REGION" \
    --attributes "MonthlySpendLimit=$SMS_MONTHLY_SPEND_LIMIT,DefaultSMSType=Transactional" >/dev/null
  echo "SMS: monthly spend limit set to \$$SMS_MONTHLY_SPEND_LIMIT"
fi

if [ -n "${SES_SENDER:-}" ]; then
  aws ses verify-email-identity --region "$REGION" --email-address "$SES_SENDER"
  echo "SES: verification email sent to $SES_SENDER (click the link before alerts can be emailed)"
  PARAMS+=("--param=sesSender=$SES_SENDER")
fi

echo
if [ "${#PARAMS[@]}" -gt 0 ]; then
  echo "Now redeploy with:"
  echo "  npx serverless deploy --stage $STAGE --region $REGION ${PARAMS[*]}"
else
  echo "Nothing configured. Set APNS_*, FCM_SERVICE_ACCOUNT_FILE, SES_SENDER or SMS_MONTHLY_SPEND_LIMIT."
fi
