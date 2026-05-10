"""Secrets bootstrap — populates os.environ before app code reads it.

Two paths, picked at call time:

  * **Production (AWS)** — when ``AWS_REGION`` is set, pulls each entry
    in ``_SECRETS_MAP`` from AWS Secrets Manager.  The ECS task's IAM
    role must grant ``secretsmanager:GetSecretValue`` on each id.

  * **Local dev** — when ``AWS_REGION`` is unset, sources the
    project-root ``.env`` file (gitignored).  Existing env vars win over
    .env so a developer can override per-shell with ``export``.

Both branches are no-op-on-failure: a missing secret (or missing boto3)
logs a warning to stderr and continues.  Sevim must boot even when an
optional secret like ``SEVIM_AUTH_SECRET`` hasn't been provisioned yet.

Call ``bootstrap()`` BEFORE importing anything that reads
``OPENAI_API_KEY`` / ``SEVIM_VLLM_URL`` / ``SEVIM_TELEMETRY_DB`` —
practically, as the first line of ``main()`` in the entry points.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Secret-id → env-var-to-populate.  Add new entries here as the secrets
# inventory grows; nothing else has to change.
_SECRETS_MAP: dict[str, str] = {
    "sevim/openai":        "OPENAI_API_KEY",
    "sevim/auth_secret":   "SEVIM_AUTH_SECRET",
    "sevim/db_url":        "SEVIM_TELEMETRY_DB",
    "sevim/ip_hash_salt":  "SEVIM_IP_HASH_SALT",
    "sevim/ses_from":      "SEVIM_SES_FROM_ADDRESS",
}


def bootstrap() -> None:
    """Populate os.environ from Secrets Manager (AWS) or .env (local)."""
    if os.environ.get("AWS_REGION"):
        _pull_from_secrets_manager()
        # Still source .env if present — useful when running the image
        # locally with `docker run --env-file .env` for staging.
    _load_dotenv()
    _hydrate_db_url_from_rds_secret()


def _hydrate_db_url_from_rds_secret() -> None:
    """When ECS injects the RDS-managed secret as ``SEVIM_DB_SECRET_JSON``
    (a JSON dict with username/password/host/port/dbname), assemble it
    into ``SEVIM_TELEMETRY_DB`` so the telemetry layer's URL parser can
    pick the Postgres backend.

    Only runs when ``SEVIM_TELEMETRY_DB`` isn't already set explicitly.
    """
    if os.environ.get("SEVIM_TELEMETRY_DB"):
        return
    raw = os.environ.get("SEVIM_DB_SECRET_JSON")
    if not raw:
        return
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        _warn("SEVIM_DB_SECRET_JSON is not valid JSON — skipping")
        return
    user = info.get("username")
    password = info.get("password")
    host = info.get("host")
    port = info.get("port", 5432)
    dbname = info.get("dbname") or info.get("database") or "sevim"
    if not (user and password and host):
        _warn("SEVIM_DB_SECRET_JSON missing required fields — skipping")
        return
    # urlencode the password so special chars (/, @, :) don't break parsing.
    from urllib.parse import quote
    os.environ["SEVIM_TELEMETRY_DB"] = (
        f"postgresql://{user}:{quote(password)}@{host}:{port}/{dbname}"
    )


def _pull_from_secrets_manager() -> None:
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        _warn("AWS_REGION set but boto3 not installed — pip install 'sevim[aws]'")
        return
    sm = boto3.client("secretsmanager")
    for secret_id, env_var in _SECRETS_MAP.items():
        if os.environ.get(env_var):
            continue  # explicit env wins over Secrets Manager
        try:
            v = sm.get_secret_value(SecretId=secret_id)
        except (BotoCoreError, ClientError) as exc:
            _warn(f"{secret_id}: {type(exc).__name__} — skipping")
            continue
        raw = v.get("SecretString") or ""
        if not raw:
            continue
        os.environ[env_var] = _coerce_value(raw, env_var)


def _coerce_value(raw: str, env_var: str) -> str:
    """Secrets Manager values are strings, but the AWS console encourages
    wrapping multi-field secrets as JSON objects.  Handle both shapes:
    plain string → use as-is; JSON object → pick the matching key, else
    the first value.
    """
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(obj, dict):
        if env_var in obj:
            return str(obj[env_var])
        if obj:
            return str(next(iter(obj.values())))
        return ""
    return str(obj)


def _load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # setdefault — never override an explicit export or Secrets Manager pull.
        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'"),
        )


def _warn(msg: str) -> None:
    print(f"[secrets] {msg}", flush=True, file=sys.stderr)
