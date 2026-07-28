# Self-hosting Khayyam Math

Runs the whole of khayyammath.com on one machine, with no AWS bill.

The AWS deployment is **not** removed by any of this. `infra/` still
deploys, the SES and S3 code paths are still in the source, and the tag
`aws-production-final` marks the exact commit the Fargate service was
running. Going back is a DNS change plus one `deploy.sh` — see
[§7 Reverting to AWS](#7-reverting-to-aws).

---

## What replaces what

| AWS resource | Self-hosted replacement | Where |
|---|---|---|
| ECS Fargate task (2 × 3 GB) | `app` container, 2 uvicorn workers | `compose.yml` |
| RDS PostgreSQL | `db` container + `pgdata` volume | `compose.yml` |
| RDS automated backups | nightly `pg_dump` | `backup.sh` + `khayyam-backup.timer` |
| S3 canvas bucket | `canvases` volume (`FileStorage`) | `compose.yml` |
| S3 training / LoRA buckets | not replaced — offline-only, see note below | — |
| Secrets Manager (5 secrets) | `.env`, mode 600 | `env.example` |
| ALB + ACM + Route 53 | Cloudflare Tunnel + Cloudflare DNS | `tunnel` service |
| SES | any SMTP relay (Brevo by default) | `service/mailer.py` |
| EventBridge Scheduler ×2 | systemd timers | `systemd/` |
| CloudWatch Logs | `docker compose logs` (json-file, rotated) | `compose.yml` |
| `infra/deploy.sh` | `redeploy.sh` (same quality gate, same rollback) | `redeploy.sh` |

The training and LoRA buckets fed the offline distillation pipeline, not
the live site. Nothing in the request path reads them, so the self-hosted
stack simply doesn't set `SEVIM_EXPORT_S3_BUCKET` / `SEVIM_LORA_S3_BUCKET`
and `export_finetune.py` writes locally instead. If you want the historical
contents, `aws s3 sync` them somewhere before teardown.

### What you give up

Be clear-eyed about this before cutting over:

- **Availability is now your problem.** This box is a desktop on a
  residential connection. Power cuts, ISP outages, kernel updates, and
  "I need to reboot" all become site downtime. Fargate across two AZs
  did not have that failure mode.
- **Durability is now your problem.** RDS snapshots and S3's eleven
  nines are replaced by `backup.sh` writing to the same disk the data is
  on. Set `BACKUP_REMOTE` to an off-box target or the backup protects
  you from nothing but `DROP TABLE`.
- **The GPU is shared.** The 5090 in this box is also your dev machine's
  GPU. Nothing in the current request path uses it (generation is the
  OpenAI API), but if you later move inference local, a training run and
  a live request will fight.

---

## 1. Prerequisites

```bash
# Docker with the compose v2 plugin
sudo apt install docker-compose-v2        # or the user-scoped install:
#   mkdir -p ~/.docker/cli-plugins && curl -fsSL -o ~/.docker/cli-plugins/docker-compose \
#     https://github.com/docker/compose/releases/download/v2.40.3/docker-compose-linux-x86_64
#   chmod +x ~/.docker/cli-plugins/docker-compose
docker compose version

# You must be in the docker group (log out and back in after adding)
groups | grep -q docker || sudo usermod -aG docker "$USER"
```

The image build needs ~6 GB of disk and pulls a Lean toolchain plus
Chromium, so the first build takes several minutes.

---

## 2. Cloudflare Tunnel

The tunnel dials **out** to Cloudflare's edge, so no router port is
forwarded, no inbound firewall rule is opened, and a dynamic residential
IP never matters. Cloudflare terminates TLS with a managed certificate,
which is what makes ACM redundant.

1. Create a free Cloudflare account and **add the site** `khayyammath.com`.
2. Cloudflare shows you two nameservers. Change them at your **registrar**
   (where the domain is registered — not in Route 53). Propagation is
   usually under an hour.
   - Copy your existing Route 53 records into Cloudflare first if you
     have any beyond the site itself (MX, TXT, verification records).
     Cloudflare's import scans the zone but is not exhaustive.
3. Zero Trust → **Networks → Tunnels → Create a tunnel** → *Cloudflared* →
   name it `khayyam-math`. Choose **Docker** on the install screen and
   copy the long token out of the `docker run` command it shows.
4. Put that token in `.env` as `CF_TUNNEL_TOKEN`.
5. On the tunnel's **Public Hostnames** tab, add two entries:

   | Subdomain | Domain | Service |
   |---|---|---|
   | *(blank)* | khayyammath.com | `http://app:8080` |
   | `www` | khayyammath.com | `http://app:8080` |

   `app` resolves because cloudflared shares the compose network with it.

Do **not** create the DNS records by hand — the tunnel creates the
correct proxied CNAMEs itself.

---

## 3. Mail (replacing SES)

Sign-in is magic-link only, so a dead mail path means nobody can log in.
Treat this as a hard dependency, not a nice-to-have.

1. Create a Brevo account (free tier: 300 emails/day).
2. Brevo → **Senders, Domains & Dedicated IPs → Domains** → add
   `khayyammath.com`. It gives you DKIM and DMARC records.
3. Add those records **in Cloudflare** (DNS → Records), and set SPF:

   ```
   TXT   @   v=spf1 include:spf.brevo.com ~all
   ```

   Your current SPF in Route 53 says `include:amazonses.com`. If you want
   to keep the SES path warm for a revert, include both:
   `v=spf1 include:spf.brevo.com include:amazonses.com ~all`
4. Brevo → **SMTP & API → SMTP** → generate a key. Put the login and key
   in `.env` as `SEVIM_SMTP_USER` / `SEVIM_SMTP_PASSWORD`.
5. Verify before cutover:

   ```bash
   docker compose run --rm --no-deps app python -c "
   from service.mailer import backend_name, sender_address, send_email
   print('backend:', backend_name(), '| from:', sender_address())
   print('sent:', send_email('you@example.com', 'Khayyam self-host test',
                             'If you are reading this, SMTP works.'))"
   ```

   Check the spam folder too — a new sending domain reputation starts
   from zero even with correct SPF/DKIM.

Leaving `SEVIM_SMTP_HOST` empty makes the mailer fall back to SES, which
is the escape hatch if Brevo has a bad day and you still have AWS creds.

---

## 4. Bring it up

```bash
cd deploy/selfhost
cp env.example .env && chmod 600 .env && $EDITOR .env
docker compose up -d --build
docker compose logs -f app          # watch for "Application startup complete"
curl -s localhost:8080/health
```

Then install the timers and the boot unit:

```bash
sudo systemd/install.sh
sudo systemctl start khayyam-math.service
systemctl list-timers 'khayyam-*'
```

---

## 5. Migrating the data, then cutting over

Everything in `migrate_from_aws.sh` is **read-only against AWS**. The
Fargate service keeps serving khayyammath.com the whole time, so a failed
migration costs you nothing but a re-run.

```bash
export AWS_PROFILE=<your deploy profile>   # needs secretsmanager/rds/s3 read
./migrate_from_aws.sh --secrets     # Secrets Manager → .env
./migrate_from_aws.sh --db          # RDS → local Postgres
./migrate_from_aws.sh --canvases    # S3 → canvases volume
```

> The `polly` profile in `~/.aws` is **not** sufficient — it has no
> `rds:`, `s3:`, or `secretsmanager:` permissions. Use the profile you
> deploy with.

> RDS lives in a private subnet, so `--db` will tell you it cannot connect
> unless you first open a path (temporary security-group rule for your
> IP, an SSH tunnel, or dumping from inside the VPC). The script prints
> all three recipes and exits rather than hanging.

Copying `sevim/auth_secret` and `sevim/ip_hash_salt` across is what keeps
signed-in users signed in and keeps telemetry IP hashes comparable to the
historical rows. Generating fresh ones silently breaks both.

**Cutover** is then just the tunnel's DNS records taking over — which
happens the moment your nameservers point at Cloudflare and the tunnel is
running. Verify before you trust it:

```bash
curl -sI https://khayyammath.com/health          # 200, and no AWS ALB headers
curl -s  https://khayyammath.com/health | head
# sign in end-to-end with a real email address
# generate one figure and confirm narration audio plays
```

Keep AWS running for at least a week of real traffic. It costs a few
dollars and it is the difference between a rollback and an outage.

---

## 6. Teardown (only after you are satisfied)

Nothing above does this. Run it yourself, deliberately.

```bash
cd infra
AWS_PROFILE=<deploy profile> ./deploy.sh destroy
```

Before you do:

- Take a final RDS snapshot and download it. `cdk destroy` removes the
  database.
- `aws s3 sync` the training and LoRA buckets somewhere if you want the
  distillation corpus history.
- Note the values of every `sevim/*` secret — they are gone afterwards.
- The Route 53 hosted zone can be deleted once Cloudflare is
  authoritative and you have re-created every record there. Check MX and
  TXT records specifically; losing an MX record silently kills inbound
  mail for the domain.

Keeping the AWS **account** open costs nothing. Only the running
resources bill.

---

## 7. Reverting to AWS

The migration was additive. To go back:

```bash
git checkout aws-production-final     # or: keep current code, it still works
cd infra && AWS_PROFILE=<profile> ./deploy.sh
```

Then in Cloudflare, either delete the tunnel's public hostname and point
the record at the ALB, or move the nameservers back to Route 53.

The current code needs **no changes** to run on AWS again:

- `service/mailer.py` falls back to the SES backend whenever
  `SEVIM_SMTP_HOST` is empty, and still reads `SEVIM_SES_FROM_ADDRESS`.
- `service/storage.py` still has the S3 backend; setting
  `SEVIM_STORAGE_URL=s3://…` re-enables it.
- `service/secrets.py` still pulls from Secrets Manager whenever
  `AWS_REGION` is set.
- `infra/sevim_stack.py` was not modified.

The one thing to carry back is the data: dump the local Postgres and
restore it into RDS, and sync the `canvases` volume back to S3.

---

## Operations

```bash
# Deploy the current working tree (runs the quality gate, auto-rolls back)
./redeploy.sh

# Logs
docker compose logs -f app
docker compose logs -f tunnel

# Database shell
docker compose exec db psql -U sevim -d sevim

# Run the probe / digest by hand
docker compose run --rm --no-deps app python -m studio.quality_probe
docker compose run --rm --no-deps app python -m studio.feedback_digest

# Backup now, and check what it produced
./backup.sh && ls -lh ~/khayyam-backups/$(date -u +%F)/

# Restore a backup into a running stack
gunzip -c ~/khayyam-backups/<date>/db.sql.gz \
  | docker compose exec -T db psql -U sevim -d sevim

# Stop everything
docker compose down          # add -v to also delete the data volumes
```

### Health checklist after any change

1. `curl -s localhost:8080/health` → 200
2. `curl -sI https://khayyammath.com/health` → 200 through the tunnel
3. Sign-in email arrives (and not in spam)
4. One figure generates end to end, with audio
5. `docker compose exec db psql -U sevim -d sevim -c 'select count(*) from turns'`
   increases after that figure
