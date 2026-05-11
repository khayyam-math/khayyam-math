# Deploying Qwen + v4 LoRA to AWS

This runbook walks you through the four changes needed to ship the
v4 LoRA fine-tune as a user-selectable backend in the chat UI.

## What's already done (PR23)

1. **Adapter uploaded** to S3 at
   `s3://<your-lora-bucket>/qwen_lora_v4/`
   (164 MB, 6 files, plus a `manifest.json` describing the training
   run and an `available_loras.json` registry pointer at the bucket
   root).
2. **Telemetry schema** has a `model_id` column on `turns`, `canvases`
   and `repairs`, with `DEFAULT 'gpt-4o'`. The `ALTER TABLE ADD COLUMN
   IF NOT EXISTS` statements run on every startup so production RDS
   migrates itself the next time a Fargate task boots.
3. **Admin-only model selector**: the regular chat UI never offers a
   model choice; backend selection is a server-side setting in the
   telemetry `settings` table, mutable only through the admin page
   at `/studio/admin`. The page is gated by `SEVIM_ADMIN_EMAILS`
   (env var, comma-separated whitelist) — non-admin sessions get a
   plain 404, so the URL is undiscoverable. Every telemetry row is
   tagged with `model_id` so the operator can audit which backend
   served each request.
4. **CDK construct** for the g6.xlarge spot vLLM instance is in
   `infra/sevim_stack.py`, gated behind `enable_qwen=1`.

## Step 1 — Deploy the Fargate-side code changes (no GPU cost)

The new telemetry column + UI dropdown + routing logic ship with the
next image rebuild; they cost nothing:

```bash
cd infra
cdk deploy
```

After this, the UI dropdown shows:

* **GPT-4o (OpenAI)** — available, default
* **Qwen 2.5-7B + Khayyam Math v4** — "not configured" (greyed out)
* **Qwen 2.5-7B (base, no LoRA)** — "not configured" (greyed out)

GPT-4o keeps working exactly as before. Telemetry now tags every row
`model_id = 'gpt-4o'` automatically.

## Step 2 — Deploy the Qwen vLLM instance ($$$)

```bash
cd infra
cdk deploy -c enable_qwen=1
```

Costs:
- **g6.xlarge spot**: ~$0.20–0.30 / hr in us-east-1 (capped at $0.50/hr)
- 100 GB gp3 EBS: ~$8 / mo
- NAT-gateway egress for HF model pull (first boot only): ~$2 one-time

Monthly worst-case (24×30 = 720 h): **$0.30 × 720 ≈ $216 / mo**, plus
$10 EBS. Set the spot cap lower (`MaxPrice` override in
`sevim_stack.py`) if you want a harder ceiling.

What happens on first boot:
1. CDK provisions a g6.xlarge spot instance in a private subnet.
2. User-data installs vLLM 0.6.6.post1 in a venv.
3. User-data syncs `qwen_lora_v4/` from S3.
4. `systemctl start vllm` brings up the OpenAI-compatible chat-
   completions API on port 8000.
5. CDK wires `SEVIM_QWEN_VLLM_URL=http://<private-ip>:8000/v1` into
   the Fargate task environment.

First boot takes ~10–15 minutes (mostly HF download of the 14 GB Qwen
base). After that, vLLM keeps the model resident in GPU memory.

To verify after the deploy:

```bash
# Get the instance ID + private IP from CFN outputs
aws cloudformation describe-stacks --stack-name SevimStack \
    --query 'Stacks[0].Outputs[?OutputKey==`QwenInstancePrivateIp`].OutputValue' \
    --output text

# Hop into the VPC via SSM Session Manager
aws ssm start-session --target <instance-id>

# Inside the session:
sudo journalctl -u vllm -n 50 --no-pager
curl http://localhost:8000/v1/models
```

## Step 3 — Switch the active model from the admin page

1. Open https://khayyammath.com/studio/admin **while signed in with one
   of the e-mails in `SEVIM_ADMIN_EMAILS`**. Anyone else gets a 404.
2. The page shows:
   - the currently active model (everyone hitting `/studio` is being
     served by this)
   - radio buttons for each available backend (Qwen entries are greyed
     out as "not configured" until Step 2 above is done)
   - a per-window usage table (24h / 7d / 30d / all-time): turns, avg
     duration, avg retries, errors, total $ cost — broken out by model
3. Pick a backend, click **Save selection**. The change takes effect
   on the *next* chat request; no Fargate redeploy is needed.
4. Anyone visiting https://khayyammath.com/studio now gets the chosen
   backend automatically; they never see a model selector themselves.

End-user sessions have no input into the choice — they just type
questions and get figures. The `model_id` column on every telemetry
row lets you audit which backend handled which session.

## Step 4 — Querying telemetry per model

```sql
SELECT model_id,
       COUNT(*)                  AS turns,
       AVG(duration_s)::numeric(5,2) AS avg_s,
       AVG(retries_used)::numeric(3,2) AS avg_retries,
       SUM(cost_usd_estimate)::numeric(8,4) AS total_cost
FROM turns
WHERE timestamp > extract(epoch from now() - interval '7 days')
GROUP BY model_id
ORDER BY turns DESC;
```

The `repairs` table is also tagged by `model_id`, so the next
fine-tune corpus can filter to "use only repair pairs from gpt-4o"
or "use only repair pairs from qwen_lora_v4 to target its 2 known
failure families".

## Known caveats

* **v4's empty-SVG failure on 2/20 prompts** (`eigendecomp 2×2`,
  `Venn A∪B∩C`) remains until v5. The express critic catches the
  empty SVG and retries up to 2 times; if all retries fail the
  Fargate side surfaces "couldn't generate that figure — try
  again" rather than crashing. Until v5 lands, leave the default
  as GPT-4o.
* **Spot reclaims are silent**. When the instance is reclaimed,
  the `/studio/health` endpoint will start reporting
  `vllm_reachable: false` for the Qwen URL within seconds, and the
  catalog auto-flips the Qwen options to "not configured". The user
  silently keeps using GPT-4o.
* **No NLB / DNS in front of the vLLM instance**. The instance's
  private IP is hard-baked into the Fargate env at deploy time. If
  AWS replaces the instance you'll need `cdk deploy` again. For
  long-term operation, add an internal NLB.

## Roll-back

The Qwen vLLM instance is optional and additive — every step is
reversible:

```bash
# Tear down just the Qwen instance, keep Fargate
cdk deploy             # (without -c enable_qwen=1)

# Or roll back everything
git revert HEAD
cdk deploy
```

The `model_id` column has a default of `'gpt-4o'` and the old code
paths never wrote it explicitly, so the schema is forward- and
backward-compatible across rollouts.
