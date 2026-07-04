---
name: 2026-05-19 — slow go-live: security hardening, mobile fixes, video playlists, telemetry email
description: Full 2026-05-19 session — security-conscious go-live phase; mobile-viewer overhaul, 8-item security hardening, 5 video playlists, and email-based audience stats all shipped.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
The user decided to "slowly go live" — security now matters.

**Why:** moving khayyammath.com from launch-prep toward real public
traffic; the user wants the app defensible before opening it up.

**How to apply:** treat production changes as higher-stakes — test
hard, prefer separate focused deploys, confirm before deploying.
Deploy via `infra/deploy.sh` always.

Shipped this session (all live on origin/main, latest commit
`d5a857c`):

* **5 YouTube video playlists** — 60 narrated mp4s in
  `/tmp/khayyam_videos/{fractions,trigonometry,differential_equations,
  theory_of_computation,functional_analysis}/`. NOTE: /tmp is
  ephemeral — not yet moved to permanent storage; tmpfiles.org
  upload was failing. Functional Analysis figures are mostly
  LLM-drawn (abstract topics), against the all-deterministic goal.

* **Mobile canvas-viewer overhaul** (`service/static/canvas.html`) —
  legibility floor computed deterministically from the SVG's own
  font sizes (engine-independent); `#stage` full-width block fixes
  the iPad 300px-collapse; mobile breakpoint 720→900px; pan hint.
  Audited across WebKit/Chromium/Firefox at 6 device sizes, 0
  problems.

* **8-item security hardening** — FastAPI auto-docs disabled; all
  non-public endpoints auth-gated; canvas IDs 32→128-bit; per-canvas
  `Canvas.owner` + ownership check (`_require_canvas`); generic
  user-facing copy + generic server errors; security headers
  (CSP/HSTS/X-Frame-Options/…); HTML comments stripped from served
  pages; uvicorn `--no-server-header`.

* **Magic-link auth stays ON** — briefly flipped off by mistake,
  reverted. See feedback_keep_magic_link_auth.md.

* **Audience stats** — `sessions.user_email` column added (+ additive
  ALTER); `upsert_session()` records the signed-in email;
  `/studio/admin/stats` now reports `distinct_users` / `distinct_ips`
  / `sessions`. user_email populates from 2026-05-19 onward;
  historical rows have NULL email.

As of 2026-05-19 the telemetry DB had 31 sessions / 6 distinct IPs /
179 figures — essentially the user's own pre-launch testing.

Pre-existing rate limiting + cost guard already on in prod
(SEVIM_RATE_LIMIT=1, SEVIM_COST_GUARD=1, $10/day).
