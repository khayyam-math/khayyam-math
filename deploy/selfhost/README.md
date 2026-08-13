# Self-hosting Khayyam Math

Runs the whole of khayyammath.com on one machine, with no AWS bill.

The AWS deployment is **not** removed by any of this. `infra/` still
deploys, the SES and S3 code paths are still in the source, and the tag
`aws-production-final` marks the exact commit the Fargate service was
running. Going back is a DNS change plus one `deploy.sh` — see
[§7 Reverting to AWS](#7-reverting-to-aws).

**Starting from a bare cloud server?** Go to
[§8 Provisioning a fresh server](#8-provisioning-a-fresh-server) first,
then come back to §2. The numbered sections after that assume Docker is
installed and the repo is checked out.

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
| ALB + ACM + Route 53 | Caddy (Let's Encrypt) + an A record | `caddy` service |
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

Be clear-eyed about this before cutting over. The list depends on where
you run it.

**On any single box:**

- **No multi-AZ redundancy.** Fargate ran two tasks the ALB could fail
  between. One host means kernel updates, a bad deploy, or a hardware
  fault are all site downtime. `redeploy.sh` auto-rolls-back, which
  covers the common case, but not the host dying.
- **Durability is now your problem.** RDS snapshots and S3's eleven
  nines are replaced by `backup.sh`. Set `BACKUP_REMOTE` to an off-box
  target (a Hetzner Storage Box is ~€4/mo) or the backup lives on the
  same disk as the data and protects you from nothing but `DROP TABLE`.

**Additionally, on a workstation at home:**

- **A residential line is the availability model.** Power cuts, ISP
  outages, and "I need to reboot" all become downtime, and a consumer
  uplink is not built for it.
- **The GPU is shared.** Nothing in the current request path uses it
  (generation is the OpenAI API), but if you later move inference local,
  a training run and a live request will fight.

A cheap cloud VPS removes the second group entirely and makes the first
group's off-box backup trivial, which is why
[§8](#8-provisioning-a-fresh-server) exists.

---

## 1. Prerequisites

**Provisioning a server?** Skip this section — `provision.sh`
([§8](#8-provisioning-a-fresh-server)) installs all of it for you.

For a workstation checkout:

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

## 2. TLS and DNS (Caddy)

Caddy terminates TLS on this box with a Let's Encrypt certificate it
obtains on first start and renews unattended, which is what makes ACM
and the ALB redundant. There is **no edge credential to obtain** — the
whole configuration is two DNS records and two open ports.

1. Point **A records** at this host's public IP, at whatever DNS provider
   holds the zone:

   | Name | Type | Value |
   |---|---|---|
   | `@` | A | *this host's public IP* |
   | `www` | A | *this host's public IP* |

   Both, because both currently resolve to the AWS load balancer and
   `Caddyfile` requests a certificate for both. A missing `www` record
   makes issuance fail for the whole site block, not just for `www`.

2. Confirm ports 80 and 443 are open (`provision.sh` does this; verify
   with `sudo ufw status`). **Port 80 is not optional.** The ACME
   HTTP-01 challenge runs over it, so closing it appears to work and then
   silently breaks renewal about 60 days later.

3. Set `SEVIM_DOMAIN` in `.env`. Caddy reads it for the certificate
   names, and the app reads it to build magic-link URLs.

4. `docker compose up -d` and watch the first issuance:

   ```bash
   docker compose logs -f caddy       # "certificate obtained successfully"
   ```

Do this **after** the data migration (§5), not before: issuance needs the
A record already pointing here, and that is the cut-over itself.

### Why not the Cloudflare Tunnel

Earlier versions of this stack used one, and it is still the right answer
if you run the site from a machine that cannot accept inbound connections
— behind CGNAT, or on a home router you do not control. On a cloud VPS
with a static public IP it buys nothing and costs three things: an
always-on daemon whose failure mode is "site unreachable", a token you
must obtain from a third party before the site can serve at all, and two
coupled switches that fail silently when only one is set.

What you give up by dropping it is Cloudflare's DDoS absorption and
origin-IP hiding. If you want those back, put Cloudflare in front as
**proxied DNS** pointing at this IP and restrict `ufw` to Cloudflare's
published ranges. Caddy stays exactly as it is; nothing in this repo
changes.

---

## 3. Mail (replacing SES)

Sign-in is magic-link only, so a dead mail path means nobody can log in.
Treat this as a hard dependency, not a nice-to-have.

1. Create a Brevo account (free tier: 300 emails/day).
2. Brevo → **Senders, Domains & Dedicated IPs → Domains** → add
   `khayyammath.com`. It gives you DKIM and DMARC records.
3. Add those records at whatever DNS provider holds the zone, and set SPF:

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
./redeploy.sh                       # builds and starts
docker compose logs -f app          # watch for "Application startup complete"
curl -s localhost:8080/health
```

Use `./redeploy.sh`, not a bare `docker compose build`. The GeoLite2
database (66 MB) is gitignored — MaxMind's licence forbids
redistribution — so a fresh clone doesn't have it and the Docker build
fails at `COPY infra/geolite/GeoLite2-City.mmdb`. `redeploy.sh` fetches
it first, exactly as `infra/deploy.sh` does for the AWS path. That needs
`MAXMIND_ACCOUNT_ID` and `MAXMIND_LICENSE_KEY` in `.env`
([free signup](https://www.maxmind.com/en/geolite2/signup)).

Then install the timers and the boot unit:

```bash
sudo systemd/install.sh                          # workstation checkout
sudo SERVICE_USER=khayyam systemd/install.sh     # server (see §8)
sudo systemctl start khayyam-math.service
systemctl list-timers 'khayyam-*'
```

The `.service` files in `systemd/` are templates carrying
`@@WORKDIR@@` / `@@USER@@` placeholders; `install.sh` substitutes the
real checkout path and owning account before writing to
`/etc/systemd/system`. That is what lets the same repo drive a
workstation checkout under `~/` and a server checkout under
`/opt/khayyam-math` with no hand-editing. It refuses to install if the
user is missing from the `docker` group or `.env` does not exist, since
both produce units that fail only at 3 a.m.

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

**Cutover** is repointing the `@` and `www` A records at this host. The
moment they resolve here, Caddy answers the ACME challenge, obtains the
certificate and starts serving; first issuance takes a few seconds.

Restart Caddy as part of the cut-over:

```bash
docker compose up -d caddy && docker compose restart caddy
```

This is not superstition. If Caddy has been running while the domain
still pointed at AWS, every issuance attempt failed validation and Caddy
backed off exponentially — up to roughly a day between retries. It will
eventually notice the new DNS on its own, but "eventually" can be hours
after you flipped the record, which looks exactly like a broken
cut-over. Restarting resets the retry schedule and it issues at once.

Verify before you trust it:

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
- The Route 53 hosted zone can be deleted once another provider is
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

Then point the `@` and `www` records back at the ALB (or move the
nameservers back to Route 53). Caddy simply stops being reached; there is
no edge registration to unwind.

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

## 8. Provisioning a fresh server

`provision.sh` turns a bare Debian/Ubuntu box into a host ready to run
the stack. Written against Hetzner Cloud but nothing in it is
Hetzner-specific.

### Picking a plan

A worker needs a genuine **3 GB** — the headless Chromium the vision
reviewer spawns and the piper TTS synth both live inside that budget.
That was the lesson of the 2026-06-08 OOM, when a 2 GB Fargate task was
killed. Postgres wants ~1 GB, the OS and Docker ~0.7 GB.

| Plan | Specs | €/mo incl. VAT | Config |
|---|---|---:|---|
| CX43 / CAX31 | 8 · 16 GB | ~19 / ~25 | `WORKERS=2`, `MEM_LIMIT=8g` |
| **CPX32** | 4 · 8 GB | ~42 | **`WORKERS=1`, `MEM_LIMIT=5g`** ← the defaults |
| CPX42 | 8 · 16 GB | ~83 | `WORKERS=2`, `MEM_LIMIT=8g` |

The Cost-Optimized line (CX/CAX) is the best value by a wide margin, but
as of July 2026 it carries a **"Limited availability"** badge and was
sold out in all three EU locations. Check it first anyway — stock
returns, and you can rescale onto it later without rebuilding the disk.

Prefer **x86** if you have the choice: the image builds unchanged. ARM
(CAX) works — every dependency publishes aarch64 builds and it compiles
natively on the box — but it is slower to build and less tested here.

### On 8 GB, one worker is the honest limit

Two workers plus Postgres does not fit. The trade-off versus Fargate is
real: an OOM becomes ~15 s of downtime while Docker restarts the
container, rather than a sibling task absorbing it. `restart:
unless-stopped` plus the healthcheck make recovery automatic, and
`provision.sh` adds swap so a spike degrades into a slow second instead
of a kill — but it is less resilient than the two-task setup, and you
should know that going in.

```bash
ssh root@<server-ip>
apt-get update && apt-get install -y git
git clone https://github.com/khayyam-math/khayyam-math.git /opt/khayyam-math
/opt/khayyam-math/deploy/selfhost/provision.sh
```

It is idempotent — re-run it to apply changes. It installs base packages
and unattended security upgrades, Docker CE with the compose plugin
(from Docker's repo, because the distro's `docker.io` ships without
compose v2), a `khayyam` service account in the `docker` group, bounded
journal and container logs, a default-deny firewall, SSH key-only auth,
and a `.env` skeleton with the database password and secrets generated.

**The firewall opens SSH, 80 and 443, and nothing else.** Caddy
terminates TLS here, so those two ports are the site. Port 80 must stay
open for ACME renewal, not just for the redirect to HTTPS. Everything
else, the database included, is bound to loopback and unreachable from
off the box.

SSH hardening is skipped when `/root/.ssh/authorized_keys` is empty, so
a freshly-imaged box that still uses a root password can't lock you out.
Install your key, then re-run.

Then fill in `OPENAI_API_KEY`, `SEVIM_DOMAIN`, and the SMTP
credentials in `/opt/khayyam-math/deploy/selfhost/.env`, and follow
[§4](#4-bring-it-up) onwards.

> Run `migrate_from_aws.sh --secrets` **before** your first real
> sign-in. It overwrites the generated `SEVIM_AUTH_SECRET` and
> `SEVIM_IP_HASH_SALT` with the production values, which is what keeps
> existing users signed in and telemetry IP hashes comparable across the
> cutover.

---

## Operations

```bash
# Deploy the current working tree (runs the quality gate, auto-rolls back)
./redeploy.sh

# Logs
docker compose logs -f app
docker compose logs -f caddy

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
2. `curl -sI https://khayyammath.com/health` → 200 through Caddy
3. Sign-in email arrives (and not in spam)
4. One figure generates end to end, with audio
5. `docker compose exec db psql -U sevim -d sevim -c 'select count(*) from turns'`
   increases after that figure
