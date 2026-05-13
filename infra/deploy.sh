#!/usr/bin/env bash
# Deploy SevimStack with the env vars the stack needs to keep its
# HTTPS / domain / redirect configuration.  Running ``cdk deploy``
# without these gives CDK an empty domain and produces a destructive
# diff (drops the ACM cert, flips PublicListener from 443 back to 80,
# and races the existing port-80 redirect listener).  Use this
# wrapper instead.
#
# Usage:
#   cd infra && ./deploy.sh                  # deploy the latest commit
#   cd infra && ./deploy.sh --hotswap        # extra flags forwarded
#   cd infra && ./deploy.sh diff             # diff instead of deploy
#
# Required tooling:
#   * AWS CLI profile named ``sevim`` with deploy permissions on
#     account REDACTED (see ~/.aws/config).
#   * Node + npx (cdk runs via ``npx aws-cdk``).
#   * The repo's .venv must be the active Python (cdk shells out to
#     python3, which has to import aws_cdk).
set -euo pipefail

# Resolve to the directory this script lives in so ``cdk`` finds
# ``cdk.json`` regardless of the caller's PWD.
cd "$(dirname "$(readlink -f "$0")")"

export AWS_PROFILE="${AWS_PROFILE:-sevim}"
export CDK_DEFAULT_ACCOUNT="${CDK_DEFAULT_ACCOUNT:-REDACTED}"
export CDK_DEFAULT_REGION="${CDK_DEFAULT_REGION:-us-east-1}"
export SEVIM_DOMAIN="${SEVIM_DOMAIN:-khayyammath.com}"
# SEVIM_REDIRECT_DOMAINS intentionally unset by default — the live
# stack does not have typo-redirect ACM certs / Route53 aliases for
# alternate domains.  Set explicitly in the caller's env if you
# want to add them.

cmd="${1:-deploy}"
case "$cmd" in
    diff)
        shift
        exec npx aws-cdk diff "$@"
        ;;
    synth)
        shift
        exec npx aws-cdk synth "$@"
        ;;
    deploy)
        # 'deploy' is the default; consume the arg if present.
        if [[ "${1:-}" == "deploy" ]]; then shift; fi
        echo "[deploy.sh] AWS_PROFILE=$AWS_PROFILE"
        echo "[deploy.sh] ACCOUNT=$CDK_DEFAULT_ACCOUNT  REGION=$CDK_DEFAULT_REGION"
        echo "[deploy.sh] SEVIM_DOMAIN=$SEVIM_DOMAIN"
        echo "[deploy.sh] SEVIM_REDIRECT_DOMAINS=${SEVIM_REDIRECT_DOMAINS:-(unset)}"
        echo
        exec npx aws-cdk deploy --require-approval=never "$@"
        ;;
    *)
        # Pass-through for any other cdk subcommand (e.g. destroy, ls).
        exec npx aws-cdk "$@"
        ;;
esac
