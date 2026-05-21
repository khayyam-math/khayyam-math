# syntax=docker/dockerfile:1.7
#
# Sevim runtime container.  Multi-stage so the runtime image carries
# only what's needed at request time — no compilers, no -dev headers,
# no build-time caches.  Image size is ~600 MB dominated by the piper
# voice ONNX (63 MB) and the Python deps.
#
# Build:    docker build -t sevim:dev .
# Run:      docker run -p 8080:8080 -e OPENAI_API_KEY=sk-... sevim:dev
# Health:   curl http://127.0.0.1:8080/health

# ── Stage 1: builder — compile + install all Python deps + fetch voice ──
FROM python:3.12-slim AS builder
WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

# Build-time deps: compilers + headers for cairosvg, piper-tts (onnxruntime),
# lxml, cffi.  curl + ca-certs to pull the piper voice from Hugging Face.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        libcairo2-dev libpango1.0-dev libffi-dev libxml2-dev \
        curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.5.7

# Resolve + install third-party deps first so the layer caches between
# source changes.  --no-install-project skips installing the local `sevim`
# package itself; we run uvicorn directly against /app/service/app.py at
# runtime, so the package doesn't need to be importable as `sevim` from
# site-packages — the working directory is on sys.path.
COPY pyproject.toml uv.lock ./
# --extra aws: install boto3 so service/secrets.py can pull from AWS
# Secrets Manager when AWS_REGION is set inside the ECS task.
RUN uv sync --frozen --no-dev --no-install-project --extra aws

# Pull piper voice ONNX + JSON config from Hugging Face.  Doing this in
# the builder stage avoids needing curl in the runtime image.
RUN mkdir -p /opt/sevim/voices \
 && curl -fsSL -o /opt/sevim/voices/en_US-lessac-medium.onnx \
        https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx \
 && curl -fsSL -o /opt/sevim/voices/en_US-lessac-medium.onnx.json \
        https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json

# Application source — copied last so dep-only changes don't bust this
# layer.  Each top-level package is its own COPY for explicit auditing.
COPY sevim     ./sevim
COPY service   ./service
COPY studio    ./studio
COPY mcp_server ./mcp_server


# ── Stage 2: runtime — slim base + .so libs only ─────────────────────────
FROM python:3.12-slim AS runtime
WORKDIR /app

# Runtime shared libs (no -dev, no compilers).  fonts-dejavu (full) +
# fonts-noto-core cover Greek, set-theory and math-operator glyphs that
# show up in figures.  chromium is the vision-reviewer's rasteriser:
# it renders SVG with the SAME engine as the canvas viewer, so the
# reviewer audits exactly what the learner sees (cairosvg mis-sized
# percentage tspans and lacked math glyphs).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
        libffi8 libxml2 \
        fonts-dejavu fonts-noto-core \
        ca-certificates \
        graphviz \
        chromium \
        curl \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/* \
 && useradd --system --create-home --home-dir /home/sevim --uid 1001 sevim \
 && mkdir -p /var/sevim/canvases /opt/sevim/voices \
 && chown -R sevim:sevim /var/sevim /opt/sevim /app

# ── Lean 4 (core — no Mathlib) ────────────────────────────────────────
# The math_verifier's third tier (after SymPy + Z3) sends decidable
# Nat-arithmetic claims through Lean's `decide` tactic for kernel-
# checked rigour.  Mathlib (~3 GB) is NOT installed — that lives in
# the offline catalog-verifier service.  Total cost ~300 MB.
# Disable at runtime with SEVIM_LEAN_VERIFIER=off if needed.
ENV ELAN_HOME=/opt/elan
ENV PATH=/opt/elan/bin:$PATH
RUN curl -fsSL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
      -o /tmp/elan-init.sh \
 && chmod +x /tmp/elan-init.sh \
 && /tmp/elan-init.sh -y --default-toolchain stable \
 && rm /tmp/elan-init.sh \
 && ln -sf /opt/elan/bin/lean /usr/local/bin/lean \
 && chown -R sevim:sevim /opt/elan

# Copy the resolved venv, voice assets, and application source.
COPY --from=builder --chown=sevim:sevim /app/.venv          /app/.venv
COPY --from=builder --chown=sevim:sevim /opt/sevim/voices   /opt/sevim/voices
COPY --from=builder --chown=sevim:sevim /app/sevim          /app/sevim
COPY --from=builder --chown=sevim:sevim /app/service        /app/service
COPY --from=builder --chown=sevim:sevim /app/studio         /app/studio
COPY --from=builder --chown=sevim:sevim /app/mcp_server     /app/mcp_server
COPY --from=builder --chown=sevim:sevim /app/pyproject.toml /app/pyproject.toml

# Defaults that make the image production-ready.  Overridden via the ECS
# task definition or `docker run -e` for local smoke tests.
#
#   * NO_BROWSER       — never try to spawn xdg-open inside a container.
#   * VOICE_MODEL      — point piper at the baked-in ONNX.
#   * DATA_DIR         — canvas WAVs land on the writable runtime volume.
#                        S3-backed storage is a follow-up PR.
#   * TELEMETRY=1      — capture every turn so the distillation pipeline
#                        has training data to mine.
#   * RATE_LIMIT=1
#   * COST_GUARD=1
#   * CONTENT_FILTER=1 — three abuse / cost-runaway switches that ought
#                        to default ON in any public deploy.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    SEVIM_HTTP_PORT=8080 \
    SEVIM_HTTP_HOST=0.0.0.0 \
    SEVIM_VOICE_MODEL=/opt/sevim/voices/en_US-lessac-medium.onnx \
    SEVIM_DATA_DIR=/var/sevim/canvases \
    SEVIM_NO_BROWSER=1 \
    SEVIM_TELEMETRY=1 \
    SEVIM_RATE_LIMIT=1 \
    SEVIM_COST_GUARD=1 \
    SEVIM_CONTENT_FILTER=1 \
    SEVIM_TRUST_PROXY=1

USER sevim
EXPOSE 8080

# Use Python instead of curl so we don't ship curl in the runtime layer.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3).status==200 else 1)" \
        || exit 1

CMD ["uvicorn", "service.app:app", \
     "--host", "0.0.0.0", "--port", "8080", \
     "--log-level", "info", "--access-log", \
     "--no-server-header"]
