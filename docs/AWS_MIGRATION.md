# Sevim AWS migration plan (target architecture)

## TL;DR

| Resource | Service | Spec | Monthly cost (est.) |
|---|---|---|---|
| Compute | ECS Fargate | 2× (1 vCPU, 2 GB) | $30 |
| Database | RDS Postgres | db.t4g.small | $25 |
| Object store | S3 standard | ≤10 GB + low egress | $5 |
| Load balancer | ALB | basic | $20 |
| CDN + DNS | CloudFront + Route53 | low traffic | $5 |
| Secrets | Secrets Manager | 1 secret | $1 |
| Logs | CloudWatch | low volume | $5 |
| **Sub-total infra** | | | **~$90** |
| LLM (gpt-4o) | OpenAI API | 500 prompts/day | ~$750 |
| **Total** | | | **~$840/month** |

The fine-tuning pipeline is the lever that drops the OpenAI line near zero.

## Code changes required for AWS (vs current local)

The current local code has three implicit assumptions that AWS breaks:

1. **Local file system for canvases** (`~/.local/share/sevim/canvases/`)
   * Fix: introduce `service.storage` abstraction with `LocalStorage` (current) and `S3Storage` (new) backends, picked by `SEVIM_STORAGE_BACKEND` env var.
   * S3Storage uses `boto3` and a single bucket: `s3://sevim-prod-canvases/<canvas_id>/intro.wav` etc.
   * The viewer endpoints `/canvas/<id>/intro.wav` already serve via `FileResponse`; swap to a redirect to a presigned S3 URL when storage is S3.

2. **In-memory `REGISTRY` (CanvasRegistry singleton)**
   * Today: every Studio process has its own dict.  Fine on a single box; broken when 2+ ECS tasks share users.
   * Fix: move `REGISTRY` to Postgres (a `canvases_live` table) OR sticky-session at the ALB so a user always lands on the same task.
   * Recommendation: sticky sessions for v1 (zero code change, ALB feature flag).  Move to Postgres-backed registry only if we hit horizontal scaling pain.

3. **SQLite telemetry**
   * Today: one `~/.local/share/sevim/telemetry.db` file.
   * Fix: same SQL, switch driver from sqlite3 → psycopg.  The `Telemetry` class abstracts this; only the connection string changes.
   * Migration: `pg_dump` of the local SQLite (via sqlite-to-postgres tools or our own export script) into RDS at deploy time.

Two tiny additions:

4. **Health probe endpoint** for ALB target group: `GET /studio/health` already exists; just point ALB at it.
5. **OPENAI_API_KEY pull from Secrets Manager** at boot:
   ```python
   if os.environ.get("AWS_REGION"):
       import boto3
       sm = boto3.client("secretsmanager")
       v = sm.get_secret_value(SecretId="sevim/openai")
       os.environ["OPENAI_API_KEY"] = v["SecretString"]
   ```
   Lives in `studio/__main__.py` so the Fargate task always has the key.

## Container

Single Dockerfile (multi-stage):

```dockerfile
FROM python:3.12-slim AS base
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev
COPY . .
ENV SEVIM_HTTP_PORT=8080
ENV SEVIM_STORAGE_BACKEND=s3
ENV SEVIM_TELEMETRY_DB=postgresql://...
ENV SEVIM_NO_BROWSER=1
EXPOSE 8080
CMD ["python", "-m", "studio"]
```

Image size estimate: ~600 MB (Python slim + uv sync of our 7 deps + piper voice ~80 MB).
If image size matters: pull piper voice from S3 at boot instead of bundling.

## Deployment steps (when we're ready)

```bash
# 1. Build + push image
aws ecr create-repository --repository-name sevim
docker build -t sevim:v0.4 .
docker tag sevim:v0.4 <acct>.dkr.ecr.<region>.amazonaws.com/sevim:v0.4
docker push <acct>.dkr.ecr.<region>.amazonaws.com/sevim:v0.4

# 2. RDS
aws rds create-db-instance \
  --db-instance-identifier sevim-prod \
  --engine postgres --db-instance-class db.t4g.small \
  --allocated-storage 20 ...

# 3. S3
aws s3 mb s3://sevim-prod-canvases

# 4. Secret
aws secretsmanager create-secret --name sevim/openai \
  --secret-string "$OPENAI_API_KEY"

# 5. ECS cluster + service via Terraform / CDK / console
#    Task def: 1 vCPU, 2 GB, port 8080, image from step 1
#    Env: SEVIM_TELEMETRY_DB, SEVIM_STORAGE_BACKEND=s3, ...
#    IAM role with S3 read+write to sevim-prod-canvases and SecretsManager read

# 6. ALB → target group → ECS service
#    Health check: GET /studio/health (200)
#    Sticky sessions on (until we Postgres-back the canvas registry)

# 7. CloudFront + Route53 → ALB
```

## Observability

Prometheus-style counters exported via FastAPI middleware:

* `sevim_studio_requests_total{outcome}` (preference, express, error, timeout)
* `sevim_studio_express_retries_total`
* `sevim_studio_vision_audit_verdicts_total{verdict}` (PASS, FAIL)
* `sevim_studio_express_duration_seconds` (histogram)
* `sevim_studio_openai_cost_usd_total` (counter, accumulated estimated cost)

Push to CloudWatch via the OTel collector or scrape via a sidecar. Wired up post-launch.

## Cost levers (in order of impact)

1. **Self-host the LLM** — fine-tuned Qwen2.5-7B on a g5.xlarge ($0.50/hr) ≈ $360/month. Saves ~$400/month vs gpt-4o for our scale; pays for the GPU.
2. **Cache common prompts** — "matrix multiplication" gets asked 100 times/day; save 99 of those by hashing the (prompt, context) pair and serving the prior result. ~30% cost reduction.
3. **Drop vision audit retry to 0 by default** — already done in v0.4. Vision audit is one of the two big LLM calls per turn; halving it saves real money.
4. **Switch to gpt-4o-mini** for the vision-audit call only — the rubric is simple enough; gpt-4o-mini at $0.15/$0.60 per million tokens is 10× cheaper than gpt-4o.

## Failure modes to plan for

| Failure | Detection | Mitigation |
|---|---|---|
| OpenAI outage | non-200 from chat-completions | Friendly error + retry-after; eventually fail-over to local Qwen |
| RDS down | telemetry write fails | Log to local fallback (jsonl on EBS); replay at recovery |
| S3 bucket full / throttled | 5xx on canvas write | Rate-limit canvas creation per session |
| Single user OpenAI bombing | cost guard triggers | Auto-mute session for 24h; email admin |
| Prompt injection extracting system prompt | content-filter checks | Reject with stock message |

## Migration checklist

- [ ] Implement `service.storage` abstraction (LocalStorage + S3Storage).
- [ ] Implement Postgres backend in `sevim.telemetry`.
- [ ] Add `boot_aws.py` that pulls secrets at startup.
- [ ] Build Dockerfile + smoke-test image locally (`docker run` against a local Postgres).
- [ ] Wire ECS task definition + ALB + target group via Terraform (separate repo `sevim-infra`).
- [ ] Test deploy to staging with a single task + RDS + S3.
- [ ] Production deploy + Route53 cutover.
