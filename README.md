# Alert Button backend (Serverless)

API Gateway HTTP API + Lambda (Python 3.12, boto3 only) + DynamoDB (KMS) + SQS FIFO + SNS + SES + Cognito,
all defined in `serverless.yml`. The older Terraform healthcheck (`terraform/`, `deploy.sh`) is untouched;
its handler is reused as `GET /v1/health`.

## Deploy

```bash
npm install                                   # Serverless Framework v3 (no account/login needed)
npx serverless deploy --stage dev             # creates everything; prints the API URL

# Push / email / SMS credentials that CloudFormation can't manage:
STAGE=dev APNS_KEY_FILE=AuthKey.p8 APNS_KEY_ID=... APNS_TEAM_ID=... APNS_BUNDLE_ID=... \
FCM_SERVICE_ACCOUNT_FILE=firebase-sa.json SES_SENDER=alerts@example.com SMS_MONTHLY_SPEND_LIMIT=1 \
  ./scripts/setup-notifications.sh
# ...then redeploy with the --param flags it prints.

npx serverless remove --stage dev             # tear down (prod tables/pool/key are retained)
```

Stack outputs give the Cognito `UserPoolId` / `UserPoolClientId` for the mobile app.

## Layout

| Path | What |
|---|---|
| `app/handlers/provisioning.py` | `POST/GET /v1/provisioning/sessions` |
| `app/handlers/devices.py` | `GET /v1/devices`, `PATCH/DELETE /v1/devices/{deviceId}` |
| `app/handlers/users.py` | `GET /v1/users/me`, `PATCH /v1/users/me/notifications` |
| `app/handlers/push_tokens.py` | `POST/DELETE /v1/users/me/push-tokens` |
| `app/handlers/alerts.py` | `GET /v1/alerts` (cursor pagination, newest first) |
| `app/handlers/device_api.py` | Device routes: `register`, `POST /v1/alerts`, `telemetry` |
| `app/handlers/worker.py` | SQS consumer: push (user topic), email (SES template), SMS |
| `app/handlers/push_events.py` | Removes endpoints APNs/FCM report as disabled |

## Notes vs. the spec

- **Secret hashing** uses stdlib `scrypt` instead of bcrypt/argon2, so there are no native dependencies.
- **Extra indexes:** `Devices.hardwareId-index` (needed for the 409 check) and `PushEndpoints.endpointArn-index` (token re-registration and disabled-endpoint cleanup).
- **ProvisioningSessions TTL** is on the numeric `expiresAtEpoch`, since DynamoDB TTL can't use ISO strings. Expiry is also enforced on read.
- **Alert idempotency:** `alertId = uuid5(deviceId:dedupeKey)`, used as the SQS `MessageDeduplicationId`.
- **`pressedAt`** is optional (defaults to receive time). `PATCH /users/me/notifications` also accepts `phoneE164` (required before enabling SMS).
- **Revoked devices** get `401 {"code":"DEVICE_REVOKED"}` so firmware can wipe its credentials and re-enter pairing.
- **Telemetry `commands`** come from an optional `pendingCommands` list on the Devices row, cleared once delivered.
- **Custom domain** (`api.<domain>`): add an API Gateway custom domain and map it to the HTTP API with an empty base path. Routes already include `/v1`.
