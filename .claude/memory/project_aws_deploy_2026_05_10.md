---
name: "Sevim AWS deployment — LIVE at khayyammath.com"
description: Sevim shipped to AWS Fargate on 2026-05-10. https://khayyammath.com is live. All 11 PRs done. AWS account 332504859695 (us-east-1). IAM user sevim-deployer with full deploy access.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---

## Status: LIVE 2026-05-10

**Public URL:** https://khayyammath.com
**ALB:** SevimS-Servi-5MvPuwj1q3UI-1563809884.us-east-1.elb.amazonaws.com
**Region:** us-east-1
**Account:** 332504859695

Confirmed working end-to-end:
- HTTPS via ACM cert, HTTP→HTTPS redirect
- `/health` 200, `/studio/health` `vllm_reachable: true`
- gpt-4o backend reachable, OpenAI key in `sevim/openai` Secrets Manager entry
- Magic-link auth → SES email delivery from `noreply@khayyammath.com` confirmed at 2026-05-10 ~10:07 UTC
- IP-aware rate limit + cost guard ON
- Telemetry → RDS Postgres (db.t4g.small, single AZ)
- Canvas WAVs → S3 (`sevimstack-canvasbucket-...`)
- Mobile UI: viewport meta + `@media (max-width: 720px)` single-column + 100dvh + safe-area + 16px input fonts (no iOS auto-zoom) + 44px tap targets

## Operational details

### Stack ARNs / names
- CFN stack: `SevimStack`
- ECS cluster: `SevimStack-ClusterEB0386A7-v4AtWGHqxGkN`
- ECS service: `SevimStack-Service9571FDD8-VxcEhl318ICU`
- Log group: `SevimStack-AppLogsC5DF83A6-67go79B0ttdt`
- Hosted zone: `Z0798668111KS8AKCI6HZ`
- RDS-managed secret: `sevim/db_credentials`
- App secrets: `sevim/openai`, `sevim/auth_secret` (auto), `sevim/ip_hash_salt` (auto), `sevim/ses_from`

### Secrets that must be populated by hand after a fresh deploy
- `sevim/openai` — OpenAI API key (auto-created with random placeholder, MUST be replaced)
- `sevim/ses_from` — verified SES sender as a **plain email** like `noreply@khayyammath.com`. Display-name format `Sevim <noreply@…>` causes SES `Local address contains illegal character` if it goes through any shell-quoted CLI step — use plain.

### Re-deploy procedure
```
cd infra
export AWS_PROFILE=sevim AWS_REGION=us-east-1 \
       CDK_DEFAULT_ACCOUNT=332504859695 CDK_DEFAULT_REGION=us-east-1 \
       SEVIM_DOMAIN=khayyammath.com
PATH="/home/ara/.npm-global/bin:$PATH" cdk deploy SevimStack --require-approval never
```
Required locally: Docker, AWS_PROFILE, the project venv with `aws` + `aws-cdk-lib` + `boto3` + `psycopg`.

### Force-rotate ECS task to pick up new secret values
After `aws secretsmanager put-secret-value`, ECS keeps the old value baked into the running task. Force a fresh task:
```
aws ecs update-service --cluster <cluster-arn> --service <service-arn> --force-new-deployment
```
Old task drains over ~90 sec; new task starts with the updated secret.

### Inline IAM policy added during initial bootstrap
`sevim-deployer` user got an inline policy `SevimDeploySSM` granting `ssm:*` so CDK could create its bootstrap parameter. The console managed-policies list does **not** include `AmazonECR-FullAccess` — the right name is `AmazonEC2ContainerRegistryFullAccess`.

### Estimated monthly cost (~$105 + OpenAI)
ALB $20, Fargate (1×1vCPU/2GB) $15, RDS db.t4g.small $25, NAT Gateway $32, S3+misc ~$10, domain $1.25, Route 53 zone $0.50, Secrets Manager $1.60, CloudWatch ~$5. OpenAI gpt-4o adds ~$0.10 per express turn (generate + review).

## Open follow-ups (post-launch)

1. **Real-device mobile test** — verified via Chrome DevTools emulation only; need a phone smoke test for keyboard / iOS safe-area / Android Chrome.
2. **Distillation cron** — `scripts/run_distillation_cycle.sh` is wired but no cron scheduled. Run locally on the 5090 once telemetry has accumulated ~50 clean turns.
3. **DMARC + SPF TXT records** at apex of khayyammath.com to improve email deliverability (currently DKIM only).
4. **SES bounce/complaint webhooks** to a Lambda — required by SES TOS for any meaningful volume.
5. **Container image rebuilds** — currently re-built from `cdk deploy` via local Docker. CodeBuild remote build is the cleaner long-term answer.
6. **Sandbox? No, not in sandbox** — `Max24HourSend: 50000`, no upgrade needed.
7. **Rotate the IAM access key** — `AKIAU22WVQQX2TLDOMP4` was pasted into chat for setup; treat as burned. IAM → Users → sevim-deployer → Security credentials → delete the active key, create a fresh one.

## Lessons from the deploy session

- `--frozen` uv sync silently ignores `pyproject.toml` deps that aren't in `uv.lock`. Run `uv lock` after every dep change.
- CDK `DockerImageAsset(directory="..")` recurses into `infra/cdk.out/` unless `infra/` is in `.dockerignore`. Already added.
- ECS `Secret.from_secrets_manager` injects the value at task BOOT, not on each request. After updating a secret, MUST `force-new-deployment` to surface the new value.
- AWS Route 53 domain registration for UAE registrants must NOT include `State` or `ZipCode` fields (validation rejects them).
- SES `Source` parameter chokes on shell-mangled display-name format; use plain `email@domain` if the Secrets-Manager-stored value goes through any quoted CLI step.
- CDK Node 18 prints `Found errors` to stderr from a deprecation notice even when synth/deploy succeed; trust the exit code, not the text.
