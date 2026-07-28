#!/usr/bin/env bash
# Provision a fresh Debian/Ubuntu server to run Khayyam Math.
#
# Written for a bare Hetzner Cloud CX43 (8 vCPU / 16 GB / 160 GB NVMe,
# x86 — so the existing Docker image needs no rebuild), but nothing here
# is Hetzner-specific; any Debian 12+ or Ubuntu 22.04+ box works.
#
# Run as root on the NEW server, not on your workstation:
#
#   ssh root@<server-ip>
#   apt-get update && apt-get install -y git
#   git clone https://github.com/khayyam-math/khayyam-math.git /opt/khayyam-math
#   /opt/khayyam-math/deploy/selfhost/provision.sh
#
# It is idempotent — re-running is safe and is how you apply changes.
#
# What it does:
#   1. System packages + unattended security updates
#   2. Docker CE + compose plugin from Docker's own repo
#   3. A dedicated unprivileged `khayyam` user in the docker group
#   4. A default-deny firewall: SSH in, everything else out only
#   5. SSH hardening (key-only, no root password login)
#   6. Repo ownership + a .env skeleton to fill in
#
# What it deliberately does NOT do: start the stack, or touch DNS.  You
# fill in .env first, then `docker compose up -d`.  See README.md §8.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/khayyam-math}"
SELFHOST_DIR="$REPO_DIR/deploy/selfhost"
SERVICE_USER="${SERVICE_USER:-khayyam}"
SSH_PORT="${SSH_PORT:-22}"

log() { printf '\n\033[1m[provision]\033[0m %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this as root on the target server." >&2
    exit 1
fi
if [ ! -d "$SELFHOST_DIR" ]; then
    echo "❌ $SELFHOST_DIR not found." >&2
    echo "   Clone the repo to $REPO_DIR first (or set REPO_DIR)." >&2
    exit 1
fi

. /etc/os-release
log "Provisioning $PRETTY_NAME  ($(uname -m))"
if [ "$(uname -m)" != "x86_64" ]; then
    echo "⚠️  This is not x86_64.  The Dockerfile builds fine on arm64 but"
    echo "    the build will be slower and is less tested.  Continuing."
fi

# ── 1. Base packages ─────────────────────────────────────────────────
log "Installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    ca-certificates curl gnupg git ufw unattended-upgrades \
    age rclone postgresql-client jq

# Security updates apply themselves.  A server nobody logs into for a
# month is exactly the one that gets owned by a known CVE.
log "Enabling unattended security upgrades"
dpkg-reconfigure -f noninteractive unattended-upgrades

# ── 2. Docker ────────────────────────────────────────────────────────
# From Docker's repo, not the distro's: Debian/Ubuntu ship docker.io
# without the compose v2 plugin, which every script here depends on.
if ! command -v docker >/dev/null 2>&1; then
    log "Installing Docker CE"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$ID/gpg" \
        -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] " \
         "https://download.docker.com/linux/$ID $VERSION_CODENAME stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
                           docker-buildx-plugin docker-compose-plugin
else
    log "Docker already present: $(docker --version)"
    if ! docker compose version >/dev/null 2>&1; then
        apt-get install -y -qq docker-compose-plugin
    fi
fi
systemctl enable --now docker

# Cap the journal and container logs.  A 160 GB disk fills faster than
# you would think once a chatty container runs for a year.
log "Bounding log growth"
mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ]; then
    cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "5" }
}
JSON
    systemctl restart docker
fi
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/99-khayyam.conf <<'CONF'
[Journal]
SystemMaxUse=2G
CONF
systemctl restart systemd-journald

# ── 3. Service account ───────────────────────────────────────────────
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    log "Creating service account '$SERVICE_USER'"
    useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi
usermod -aG docker "$SERVICE_USER"

log "Setting ownership of $REPO_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$REPO_DIR"

# ── 4. Firewall ──────────────────────────────────────────────────────
# With Cloudflare Tunnel the origin needs NO inbound ports at all — the
# tunnel dials out.  SSH is the only exception, and it is the only thing
# an attacker can reach.  If you later drop the tunnel for Caddy +
# Let's Encrypt, open 80/443 here.
log "Configuring firewall (default deny inbound, SSH only)"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow "$SSH_PORT"/tcp comment 'SSH'
ufw --force enable
ufw status verbose

# ── 5. SSH hardening ─────────────────────────────────────────────────
# Only do this if a key is already installed, otherwise we would lock
# ourselves out of a freshly-imaged box that still uses a root password.
if [ -s /root/.ssh/authorized_keys ]; then
    log "Hardening SSH (key-only auth)"
    cat > /etc/ssh/sshd_config.d/99-khayyam.conf <<'CONF'
PasswordAuthentication no
PermitRootLogin prohibit-password
KbdInteractiveAuthentication no
CONF
    if sshd -t; then
        systemctl reload ssh 2>/dev/null || systemctl reload sshd
    else
        echo "⚠️  sshd config test failed — reverting, fix by hand." >&2
        rm -f /etc/ssh/sshd_config.d/99-khayyam.conf
    fi
else
    echo "⚠️  /root/.ssh/authorized_keys is empty — SKIPPING SSH hardening."
    echo "    Install your public key, then re-run this script."
fi

# ── 6. Config skeleton ───────────────────────────────────────────────
if [ ! -f "$SELFHOST_DIR/.env" ]; then
    log "Creating .env skeleton"
    cp "$SELFHOST_DIR/env.example" "$SELFHOST_DIR/.env"
    # Generate the values that have no reason to be typed by a human.
    # The auth secret and IP-hash salt are placeholders ONLY until
    # migrate_from_aws.sh --secrets copies the production values across;
    # keeping the originals is what stops every signed-in user being
    # logged out at cutover.
    pg_pass="$(openssl rand -base64 32 | tr -d '/+=' | head -c 40)"
    sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$pg_pass|" "$SELFHOST_DIR/.env"
    sed -i "s|^SEVIM_AUTH_SECRET=.*|SEVIM_AUTH_SECRET=$(openssl rand -hex 32)|" "$SELFHOST_DIR/.env"
    sed -i "s|^SEVIM_IP_HASH_SALT=.*|SEVIM_IP_HASH_SALT=$(openssl rand -hex 32)|" "$SELFHOST_DIR/.env"
fi
chown "$SERVICE_USER:$SERVICE_USER" "$SELFHOST_DIR/.env"
chmod 600 "$SELFHOST_DIR/.env"

# ── Done ─────────────────────────────────────────────────────────────
missing=()
for key in CF_TUNNEL_TOKEN OPENAI_API_KEY SEVIM_SMTP_USER SEVIM_SMTP_PASSWORD; do
    grep -qE "^${key}=.+" "$SELFHOST_DIR/.env" || missing+=("$key")
done

cat <<EOF

──────────────────────────────────────────────────────────────────────
✅ Server provisioned.

   repo      $REPO_DIR
   user      $SERVICE_USER  (in docker group)
   firewall  inbound: SSH only — the tunnel needs no open ports
   config    $SELFHOST_DIR/.env  (mode 600)

Still to fill in by hand:
EOF
if [ ${#missing[@]} -eq 0 ]; then
    echo "   (nothing — .env looks complete)"
else
    for k in "${missing[@]}"; do echo "   - $k"; done
fi
cat <<EOF

Then, as $SERVICE_USER:
   cd $SELFHOST_DIR
   docker compose up -d --build      # first build takes several minutes
   curl -s localhost:8080/health

Copy the production data across (read-only against AWS):
   AWS_PROFILE=<deploy> ./migrate_from_aws.sh --all

Finally install the timers and the boot unit:
   sudo SERVICE_USER=$SERVICE_USER $SELFHOST_DIR/systemd/install.sh
   sudo systemctl start khayyam-math.service
──────────────────────────────────────────────────────────────────────
EOF
