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
# Required tooling and environment:
#   * AWS_PROFILE                — an AWS CLI profile with deploy
#                                  permissions on the target account
#                                  (configure with ``aws configure
#                                  --profile <your-profile>``).
#   * CDK_DEFAULT_ACCOUNT        — the 12-digit AWS account ID you
#                                  want to deploy into.
#   * CDK_DEFAULT_REGION         — defaults to us-east-1 if unset.
#   * SEVIM_DOMAIN               — the Route 53-hosted domain the
#                                  stack should attach an ALB +
#                                  ACM cert to (e.g. yourdomain.com).
#                                  The hosted zone must already
#                                  exist in the target account.
#   * Node + npx (cdk runs via ``npx aws-cdk``).
#   * The repo's .venv must be the active Python (cdk shells out to
#     python3, which has to import aws_cdk).
set -euo pipefail

# Resolve to the directory this script lives in so ``cdk`` finds
# ``cdk.json`` regardless of the caller's PWD.
cd "$(dirname "$(readlink -f "$0")")"

# Required env vars — no defaults, to avoid accidentally deploying
# into the wrong AWS account.
: "${AWS_PROFILE:?Set AWS_PROFILE to the CLI profile that should run cdk deploy}"
: "${CDK_DEFAULT_ACCOUNT:?Set CDK_DEFAULT_ACCOUNT to your 12-digit AWS account ID}"
export CDK_DEFAULT_REGION="${CDK_DEFAULT_REGION:-us-east-1}"
: "${SEVIM_DOMAIN:?Set SEVIM_DOMAIN to the Route 53 domain to deploy under (e.g. example.com)}"

# Build-time git SHA — flows into the image as $SEVIM_GIT_SHA and
# stamps every /studio/feedback row, so the admin recurrence-after-fix
# heuristic can compare resolved-SHA vs. report-SHA.  HEAD by default;
# override for replays.
export SEVIM_GIT_SHA="${SEVIM_GIT_SHA:-$(git -C .. rev-parse --short=12 HEAD 2>/dev/null || echo unknown)}"
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

        # ── Quality gate ────────────────────────────────────────
        # Run the test battery against the LOCAL build before any
        # cdk deploy lands in production.  Set SEVIM_SKIP_QUALITY_GATE=1
        # to bypass (emergency hotfix only); set
        # SEVIM_QUALITY_GATE_FAST=1 to use the 3-prompt fast subset.
        if [[ "${SEVIM_SKIP_QUALITY_GATE:-0}" == "1" ]]; then
            echo "[deploy.sh] ⚠️  SEVIM_SKIP_QUALITY_GATE=1 — skipping pre-deploy quality gate."
        else
            echo "[deploy.sh] Running pre-deploy quality gate…"
            if ! (cd .. && uv run python infra/quality_gate.py); then
                echo
                echo "[deploy.sh] ❌ Quality gate FAILED — deploy blocked."
                echo "[deploy.sh] Fix the regressions above, or set"
                echo "[deploy.sh]   SEVIM_SKIP_QUALITY_GATE=1 ./deploy.sh"
                echo "[deploy.sh] to bypass (emergency hotfixes only)."
                exit 2
            fi
            echo "[deploy.sh] ✅ Quality gate passed — proceeding with cdk deploy."
            echo
        fi

        exec npx aws-cdk deploy --require-approval=never "$@"
        ;;
    *)
        # Pass-through for any other cdk subcommand (e.g. destroy, ls).
        exec npx aws-cdk "$@"
        ;;
esac
