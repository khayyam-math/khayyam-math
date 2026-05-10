#!/usr/bin/env python3
"""Sevim CDK entry point.

Deploys the public Sevim stack to AWS.  Run from the repo root:

    # one-time per AWS account / region
    cd infra && ../.venv/bin/cdk bootstrap

    # diff: show what would change
    cd infra && ../.venv/bin/cdk diff

    # deploy / re-deploy
    cd infra && ../.venv/bin/cdk deploy --require-approval never

Required env vars:
    AWS_REGION          — defaults to us-east-1
    AWS_ACCOUNT_ID      — required by CDK; cdk diff/deploy auto-fills
                          via STS.  Override only if synthesising
                          for a specific account from a context that
                          can't run STS.

Optional env vars:
    SEVIM_DOMAIN        — public hostname (e.g. sevim.example).  When
                          set, the stack adds an ACM cert and a Route 53
                          A-alias record.  When unset, deploy uses the
                          raw ALB DNS — fine for a soft launch, but the
                          magic-link emails will reference the ALB DNS
                          which surprises users.
    SEVIM_DOMAIN_ZONE   — Route 53 hosted zone ID for the domain (when
                          ZONE != domain).  If unset, the stack does a
                          ``HostedZone.from_lookup`` on SEVIM_DOMAIN.

Cost note: a fresh deploy provisions ALB ($20/mo), RDS db.t4g.small
($25/mo), 1× Fargate task (~$15/mo), NAT gateway ($32/mo), S3 (~$5/mo).
~$95/mo before traffic.  Tear down with ``cdk destroy``.
"""
from __future__ import annotations

import os

import aws_cdk as cdk

from sevim_stack import SevimStack


def _account() -> str | None:
    return os.environ.get("CDK_DEFAULT_ACCOUNT") or os.environ.get("AWS_ACCOUNT_ID")


def _region() -> str:
    return (
        os.environ.get("CDK_DEFAULT_REGION")
        or os.environ.get("AWS_REGION")
        or "us-east-1"
    )


app = cdk.App()

def _redirect_domains() -> list[str]:
    raw = os.environ.get("SEVIM_REDIRECT_DOMAINS", "").strip()
    if not raw:
        return []
    return [d.strip() for d in raw.split(",") if d.strip()]


SevimStack(
    app,
    "SevimStack",
    env=cdk.Environment(account=_account(), region=_region()),
    description=(
        "Sevim public deployment — ECS Fargate + RDS Postgres + ALB + "
        "S3 + Secrets Manager + (optional) Route 53 + ACM."
    ),
    domain=os.environ.get("SEVIM_DOMAIN"),
    domain_zone_id=os.environ.get("SEVIM_DOMAIN_ZONE"),
    redirect_domains=_redirect_domains(),
)

app.synth()
