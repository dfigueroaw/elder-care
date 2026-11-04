#!/usr/bin/env bash
# Deploy (or destroy) the healthcheck Lambda with Terraform.
#
# Usage:
#   ./deploy.sh            # init + plan + apply
#   ./deploy.sh destroy    # tear everything down
#
# Extra Terraform variables can be passed via env, e.g.:
#   TF_VAR_aws_region=us-west-2 TF_VAR_target_url=https://example.com ./deploy.sh

set -euo pipefail

TF_DIR="$(cd "$(dirname "$0")/terraform" && pwd)"
ACTION="${1:-apply}"

for cmd in terraform aws; do
  command -v "$cmd" >/dev/null || { echo "error: $cmd is not installed" >&2; exit 1; }
done

aws sts get-caller-identity >/dev/null || { echo "error: AWS credentials not configured" >&2; exit 1; }

terraform -chdir="$TF_DIR" init -input=false

case "$ACTION" in
  apply)
    terraform -chdir="$TF_DIR" plan -input=false -out=tfplan
    terraform -chdir="$TF_DIR" apply -input=false tfplan
    rm -f "$TF_DIR/tfplan"

    URL="$(terraform -chdir="$TF_DIR" output -raw function_url)"
    echo
    echo "Deployed. Testing $URL"
    curl -sS -w '\nHTTP %{http_code}\n' "$URL"
    ;;
  destroy)
    terraform -chdir="$TF_DIR" destroy -input=false
    ;;
  *)
    echo "usage: $0 [apply|destroy]" >&2
    exit 1
    ;;
esac
