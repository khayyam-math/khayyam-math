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

# Source the operator's gitignored repo-root .env so deploy-time-only
# settings reach `cdk synth` WITHOUT being committed to source.  This is
# where personal / per-operator values live (e.g. SEVIM_ADMIN_EMAILS,
# SEVIM_PROBE_ENABLED, SEVIM_PROBE_ALERT_EMAIL).  A fresh clone with no
# .env simply deploys the public defaults: no admin whitelist, no probe.
if [ -f ../.env ]; then
    set -a            # export every var the file defines
    # shellcheck disable=SC1091
    . ../.env
    set +a
fi

# Required env vars — no defaults, to avoid accidentally deploying
# into the wrong AWS account.
: "${AWS_PROFILE:?Set AWS_PROFILE to the CLI profile that should run cdk deploy}"
: "${CDK_DEFAULT_ACCOUNT:?Set CDK_DEFAULT_ACCOUNT to your 12-digit AWS account ID}"
export CDK_DEFAULT_REGION="${CDK_DEFAULT_REGION:-us-east-1}"
# CDK reads CDK_DEFAULT_REGION for the stack target, but the AWS SDK
# underneath (used for assume-role / lookup / deploy-role calls) reads
# AWS_REGION / AWS_DEFAULT_REGION and otherwise falls back to the active
# profile's region.  If those don't match CDK_DEFAULT_REGION, CDK tries
# to load bootstrap from the wrong region and fails with
# "SSM parameter /cdk-bootstrap/hnb659fds/version not found".  Export
# them explicitly so a profile with a different default region (e.g.
# ap-south-1) doesn't sabotage the deploy.
export AWS_REGION="$CDK_DEFAULT_REGION"
export AWS_DEFAULT_REGION="$CDK_DEFAULT_REGION"
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

        # ── GeoLite2 refresh ────────────────────────────────────
        ./refresh_geolite.sh || {
            echo "[deploy.sh] ⚠️  refresh_geolite.sh failed — continuing with existing mmdb (if any)."
        }

        # ── Pre-deploy verifier ─────────────────────────────────
        # Runs the actual prod container against a REAL Postgres
        # locally, hits every critical endpoint, catches container-
        # start crashes / SQL bugs / endpoint regressions BEFORE
        # they hit production.  Catches what pytest can't (Postgres-
        # specific SQL, telemetry init, real container boot path).
        # Bypass with SEVIM_SKIP_VERIFIER=1 (emergency hotfix only).
        if [[ "${SEVIM_SKIP_VERIFIER:-0}" == "1" ]]; then
            echo "[deploy.sh] ⚠️  SEVIM_SKIP_VERIFIER=1 — skipping pre-deploy verifier."
        else
            echo "[deploy.sh] Running pre-deploy verifier (docker + Postgres + endpoint smoke tests)…"
            if ! ./verify_before_deploy.sh; then
                echo
                echo "[deploy.sh] ❌ Pre-deploy verifier FAILED — deploy blocked."
                echo "[deploy.sh] Fix the failure above, or (emergency hotfix only):"
                echo "[deploy.sh]   SEVIM_SKIP_VERIFIER=1 ./deploy.sh"
                exit 2
            fi
            echo
        fi

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

        # Look up cluster + service from CloudFormation stack resources
        # (resolved at runtime so the CDK-autogenerated names are never
        # hardcoded in source).  Stack name is constant; the resource
        # PhysicalResourceIds are what we want.
        STACK_NAME="${SEVIM_STACK_NAME:-SevimStack}"
        CLUSTER=$(AWS_PROFILE="$AWS_PROFILE" aws cloudformation list-stack-resources \
            --stack-name "$STACK_NAME" \
            --region "$CDK_DEFAULT_REGION" \
            --query 'StackResourceSummaries[?ResourceType==`AWS::ECS::Cluster`].PhysicalResourceId | [0]' \
            --output text 2>/dev/null || echo "")
        SERVICE=$(AWS_PROFILE="$AWS_PROFILE" aws cloudformation list-stack-resources \
            --stack-name "$STACK_NAME" \
            --region "$CDK_DEFAULT_REGION" \
            --query 'StackResourceSummaries[?ResourceType==`AWS::ECS::Service`].PhysicalResourceId | [0]' \
            --output text 2>/dev/null || echo "")
        if [[ -z "$CLUSTER" || "$CLUSTER" == "None" || -z "$SERVICE" || "$SERVICE" == "None" ]]; then
            echo "[deploy.sh] ⚠️  Could not resolve cluster/service from stack $STACK_NAME — auto-rollback will be unavailable."
            CLUSTER=""
            SERVICE=""
        else
            # Service physical id is the full ARN, ECS update-service
            # accepts either ARN or short name; using the trailing
            # segment for readability in logs.
            SERVICE_SHORT="${SERVICE##*/}"
            echo "[deploy.sh] Resolved cluster=$CLUSTER  service=$SERVICE_SHORT"
        fi

        # Record the PREVIOUS task def so we can auto-rollback if
        # post-deploy health checks fail.  Best-effort: if we can't
        # read it, the auto-rollback step below will be a no-op.
        PREV_TD=""
        if [[ -n "$CLUSTER" && -n "$SERVICE" ]]; then
            PREV_TD=$(AWS_PROFILE="$AWS_PROFILE" aws ecs describe-services \
                --cluster "$CLUSTER" \
                --service "$SERVICE" \
                --region "$CDK_DEFAULT_REGION" \
                --query 'services[0].taskDefinition' --output text 2>/dev/null || echo "")
            if [[ -n "$PREV_TD" && "$PREV_TD" != "None" ]]; then
                echo "[deploy.sh] Previous task def (for auto-rollback): $PREV_TD"
            fi
        fi

        # Run cdk deploy — NOT exec'd, so the post-deploy verifier
        # below can run.
        if ! npx aws-cdk deploy --require-approval=never "$@"; then
            echo
            echo "[deploy.sh] ❌ cdk deploy itself failed; aborting (no auto-rollback needed — nothing changed)."
            exit 3
        fi

        # ── Post-deploy auto-rollback watch ─────────────────────
        # Watch /health for 60 s after deploy.  If 3 consecutive
        # checks fail (10 s apart), automatically revert the ECS
        # service to the previous task definition.  Skip with
        # SEVIM_SKIP_AUTO_ROLLBACK=1.
        if [[ "${SEVIM_SKIP_AUTO_ROLLBACK:-0}" == "1" || -z "$PREV_TD" || "$PREV_TD" == "None" || -z "$CLUSTER" || -z "$SERVICE" ]]; then
            echo "[deploy.sh] auto-rollback watch SKIPPED (flag set or stack lookup failed)."
        else
            URL="https://${SEVIM_DOMAIN}/health"
            echo "[deploy.sh] Post-deploy watch: probing $URL for 60 s (3 consecutive failures = rollback)"
            consec_fail=0
            for _ in 1 2 3 4 5 6; do
                code=$(curl -s -o /dev/null -m 5 -w "%{http_code}" "$URL" || echo "000")
                if [[ "$code" == "200" ]]; then
                    consec_fail=0
                    echo "[deploy.sh]   /health → $code"
                else
                    consec_fail=$((consec_fail + 1))
                    echo "[deploy.sh]   /health → $code  (consecutive failures: $consec_fail)"
                    if [[ "$consec_fail" -ge 3 ]]; then
                        echo
                        echo "[deploy.sh] 🚨 3 consecutive /health failures — rolling back to $PREV_TD"
                        AWS_PROFILE="$AWS_PROFILE" aws ecs update-service \
                            --cluster "$CLUSTER" \
                            --service "$SERVICE" \
                            --task-definition "$PREV_TD" \
                            --region "$CDK_DEFAULT_REGION" \
                            --query 'service.taskDefinition' --output text
                        AWS_PROFILE="$AWS_PROFILE" aws ecs wait services-stable \
                            --cluster "$CLUSTER" \
                            --services "$SERVICE" \
                            --region "$CDK_DEFAULT_REGION"
                        echo "[deploy.sh] ⏪  Auto-rollback complete.  Inspect logs to fix the underlying issue."
                        exit 4
                    fi
                fi
                sleep 10
            done
            echo "[deploy.sh] ✅ Post-deploy watch passed — service healthy."
        fi
        ;;
    *)
        # Pass-through for any other cdk subcommand (e.g. destroy, ls).
        exec npx aws-cdk "$@"
        ;;
esac
