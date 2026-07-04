---
name: UAE IP package — deferred polish items
description: Status of the four polish items deferred from the 2026-05-10 UAE IP build; item #3 was forced by the 2026-05-22 second rejection
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---

**Background.** The UAE Ministry of Economy & Tourism rejected the IP
filing **twice**. The second rejection (2026-05-22) carried the Arabic
comment "صور من صفحات التطبيق فقط" — "Screenshots of the application's
pages ONLY". This forced item #3 to be done immediately, not "later".

**Why:** the package previously used a TikZ wireframe for Screen 3
(authenticated chat surface) and a figure-output PNG (`vfx_normal.png`)
for Screen 4 (canvas viewer UI). Both fail the Ministry's "screenshots
of pages only" rule.

**How to apply when you resume:**

1. **Verbatim code excerpt** — still deferred. `important_source_code_part.tex`
   contains a *paraphrased* copy of `studio/express.py` lines 456–600.
   Replace with `\lstinputlisting[language=Python, firstline=456,
   lastline=600, firstnumber=456]{../../studio/express.py}` to load
   verbatim from source. (Note: line numbers will have drifted since
   2026-05-10 — re-pick the right block.)

2. **`source_code_explanation.pdf` truncated to 2 pages** — still
   deferred. Run `xelatex source_code_explanation.tex 2>&1` to find
   the silent abort (likely the `\lstdefinelanguage{JSON}` block).

3. **Screenshots in `application_forms_and_screens.pdf`** — ✅ **DONE
   on 2026-05-22.** All seven screens now use real UI screenshots:
   `screenshots/01_landing.png`, `02_signin.png`,
   `03_studio_chat.png` (authenticated two-pane: chat + canvas with
   quadratic solve), `04_canvas_viewer.png` (stand-alone /canvas/<id>/view
   with Play-narration controls), `05_terms.png`, `06_contact.png`,
   `07_admin_dashboard.png` (operator dashboard with model selector +
   usage table). Capture method, in case you need to redo it: start
   studio with `SEVIM_AUTH_REQUIRED=1
   SEVIM_AUTH_SECRET=insecure-dev-screencap-secret-32bytes!!
   SEVIM_ADMIN_EMAILS=<your email>` on port 8901, then mint a
   sevim_auth cookie with that secret and drive Playwright (script at
   `/tmp/capture_uae_screenshots.py`). The local studio runs against
   the real OpenAI key from `.env`, so the rendered figure is genuine.
   PDF rebuilt with `xelatex application_forms_and_screens.tex` —
   final size ~1.3 MB, 9 pages. Only this PDF needs re-upload to the
   Ministry.

4. **Belt-and-suspenders env warning** — still deferred. Add a
   `os.environ.get("SEVIM_VLLM_URL", "")` empty/loopback guard in
   `mcp_server/__main__.py` after `_bootstrap_secrets()`.
