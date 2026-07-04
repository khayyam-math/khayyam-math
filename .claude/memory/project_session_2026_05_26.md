---
name: 2026-05-26-jair-resubmission
description: "Long session covering AI4Math anonymisation+citation audit, JAIR paper_id 23066 second desk-reject fix (abstract too long), figure-overflow fixes on JAIR paper, LogiSymb verifier-chain figure + citation audit, CUD academic-calendar conflict discovery, count_users.py script for telemetry."
metadata: 
  node_type: memory
  type: project
  originSessionId: 62ff0567-cafc-4ed2-92de-f238762f89ea
---

## Conferences — current state at session end

| Item | Deadline | PDF state | tmpfiles link |
|---|---|---|---|
| **JAIR resubmission #3** (paper_id 23066) | rolling (was desk-rejected twice) | Ready, 30pp, JAIR Reference Format on p1 | https://tmpfiles.org/dl/wwwewYVGBDWE/khayyam_jair_submission.zip |
| **AI4Math @ ICML 2026** (Seoul, Jul 11) | May 25 AoE — last few hours of Dubai May 26 | Ready, 6pp, double-blind, audited citations | https://tmpfiles.org/dl/wXwdw3bQwAPb/main.pdf |
| **LogiSymb @ IJCAI-ECAI 2026** (Bremen, Aug 15-21) | May 31 | Ready, 5pp, citation audit + disprove-arrow fix | https://tmpfiles.org/dl/wYwjwJbsJwQv/main.pdf |
| **AIED 2026** (Seoul, Jun 29–Jul 3) | May 29 | **DROP** — in-person booth required, conflicts with CUD Summer 1 | — |
| **ICETM 2026** (Shenzhen, Oct 16-18) | Jun 30 | weekend trip, on track | — |

User is teaching CUD Summer 1: **Jun 1 → Jul 10 classes; Jul 11 & 12 final exams**. AI4Math workshop falls on Jul 11 (the exam day) — user has decided they CAN attend AI4Math in person but needs exam coverage. AIED conference week (Jun 29 – Jul 3) is fully inside teaching weeks.

## JAIR paper (arashkermaniprojects/khayyam-math-paper)

**HEAD: `9f87be3`** — "Drop Zenodo deposit links from paper".

Three commits this session (`13ba1ca` → `4f3a14e` → `9f87be3`):
1. **Compress abstract** (~600 → ~290 words) so the JAIR Reference Format block fits on page 1. **Root cause of paper_id 23066 desk-reject**: oversized abstract pushed the auto-generated Reference Format block to page 2; AI screener only parses page 1.
2. **Production deployment figure** (Figure 7, p16): tightened node spacing (8mm/14mm → 6mm/8mm), restructured to fix the SES/Postgres arrow overlap, wrapped in `\resizebox{\textwidth}{!}`. Float placement `[t]` → `[!htbp]` on all figures so they don't orphan.
3. **URL fixes**: removed `arashkermaniprojects/khayyam-math` (wrong personal-user URL) — all instances now `github.com/khayyam-math/khayyam-math` (org). Removed Zenodo DOI 10.5281/zenodo.20367147 from paper body since post-publication it'll be redundant; kept in Comments-for-Editor disclosure only.

**Submission package** (lean, no README/cover letter): `main.pdf`, `main.tex`, `main.bbl`, `refs.bib`, `jair.cls`, `acmart.cls`, `acmauthoryear.{bbx,cbx}`, `acmdatamodel.dbx`, `figs/*.png`.

**Comments-for-Editor text** drafted this session with corrected GitHub URL and accurate Zenodo disclosure (acknowledging the existing preprint at 10.5281/zenodo.20367147 rather than claiming "not preprinted"). User needs to paste this when re-uploading to OJS.

## AI4Math @ ICML paper (paper/submissions/ai4math_icml/)

**Citation audit found and fixed** (refs.bib):
- `xing2024svgdreamer`: title "Text-Guided" → "Text Guided" (no hyphen, matches CVPR canonical)
- `schick2023toolformer`: "Janvi Dwivedi-Yu" → "Jane Dwivedi-Yu"; **added missing co-author Eric Hambro** at position 6; `@article`+`journal` → `@inproceedings`+`booktitle` (NeurIPS proceedings has 9 authors, our bib had 8)
- `mangrulkar2022peft`: added missing co-author **Marian Tietz** (upstream has 7 authors, bib had 6)

**Claim misuse fixed** (main.tex):
- Dropped sentence claiming Lean/Z3 papers support "recent gains in formal-math benchmarks" (neither paper does)
- `Z3 4.13~\cite{demoura2008z3}` → `Z3~\cite{demoura2008z3}` (2008 paper can't document v4.13)
- "Blinded multimodal judging" pair (Zheng + HallusionBench) → only Zheng with "LLM-as-judge methodology" (HallusionBench is a hallucination benchmark, not a judging methodology — misuse)
- Split DiagrammerGPT/Chat2SVG sentence — Chat2SVG uses diffusion-based refinement, not LLM-with-renderer-feedback as previously claimed

**Layout fixes**:
- `figure` → `figure*` for routing diagram, verifier-chain figure, judge-score table (all were overflowing the single ICML column)
- Figure 1 (router): rewrote node positioning so all 4 paths (Python templates, Graphviz, matplotlib, LLM-SVG) are visible in a clean row — matplotlib was previously hidden behind LLM-SVG due to `right=of gv` placement collision
- `disprove` arrow label: moved `block` further left + smaller font + white-fill mask so label doesn't overlap SymPy box border
- Added Figure 2 — anonymised app screenshot (`screenshot_anon.png`) cropped from the AIED screenshot to remove "Khayyam Math" branding

Final: 6 pages, anonymised double-blind, OpenReview-ready at https://openreview.net/group?id=ICML.cc/2026/Workshop/AI4Math

## LogiSymb @ IJCAI-ECAI paper (paper/submissions/logisymb_ijcai/)

**Same audit as AI4Math** uncovered:
- `nonint2025symbolicaided` and `adaptive2025symbolic` **cited but missing from refs.bib** (bibtex was throwing warnings every build) — removed cleanly from text
- Same Toolformer + PEFT bugs as AI4Math — fixed identically
- Same HallusionBench misuse (paired with Zheng as judging methodology) — split: Zheng carries "LLM-judge evaluations", HallusionBench carries "VLM judges suffer entangled hallucination/illusion failures"
- Same `disprove` arrow overlap — fixed identically

Final: 5 pages, double-blind, OpenReview-ready at https://openreview.net/group?id=ijcai.org/IJCAI-ECAI/2026/Workshop/LogiSymb

## Telemetry / production state

**`scripts/count_users.py` created** — connects to RDS Postgres via `DATABASE_URL` / `SEVIM_TELEMETRY_DB_URL`, reports total sessions, distinct IP hashes (proxy for distinct people since auth is stateless and no email column exists), total turns, estimated spend, and 24h/7d/30d activity. RDS is in private subnet → need bastion SSH tunnel or run from CloudShell.

**Country-of-origin breakdown is impossible from current data** — IP hash is salted/one-way, ALB access logging is **not enabled** in CDK, user_agent locale hints are unreliable. Two ways to enable going forward (neither backfills):
- **Option A**: Enable ALB access logs → S3 → Athena + MaxMind GeoLite2
- **Option B**: Add `sessions.country` column, look up via `geoip2` at session create

Neither option chosen yet. Open decision.

## Open items for next session

1. **Upload AI4Math PDF** to OpenReview before ~16:00 Dubai today (May 26) — last few hours of AoE
2. **Upload JAIR `main.pdf` + paste Comments-for-Editor text** to OJS
3. **Upload LogiSymb PDF** to OpenReview before May 31
4. **Skip AIED** — formally drop, do not upload (in-person + Summer 1 conflict)
5. **Country tracking** — decide Option A vs B, deploy. ~10 lines either way.
6. **AI4Math exam coverage** — user needs someone to invigilate CUD finals on Jul 11 & 12 if travelling to Seoul
7. arXiv preprint of Khayyam Math (deferred from earlier sessions)
8. GitHub social preview image (deferred)
9. Branch protection on khayyam-math/khayyam-math (deferred)

## Hard-won gotchas from this session

- **JAIR AI screener only parses page 1.** Even with `\acmVolume{1}` correctly set, an oversized abstract pushes the JAIR Reference Format block to page 2 where the screener doesn't look. Visual check on page 1 of the rendered PDF is mandatory before submitting. Memory updated in [[feedback_jair_template_gotchas]].
- **The "figure-too-wide for column" pattern is recurrent in this codebase.** Every TikZ flowchart has at some point exceeded its column boundary. Default fix sequence: (a) check if `figure*` (2-column span) is appropriate; (b) tighten `node distance`; (c) only then `\resizebox{\textwidth}{!}{...}`. Pure `\resizebox` on a 1-column figure can cause float-orphaning to last page — relax `[t]` → `[!htbp]` when applying.
- **`disprove` arrow label overlap** in verifier-chain figures: arrow line sits at box centerline, so `above=Xpt` labels land inside the box's vertical span. Fix is `left=20mm of sympy` (wider gap) + `\tiny\sffamily` font + `fill=white, inner sep=1pt` mask, NOT a diagonal arrow (user explicitly rejected that as "silly").
- **Auth is stateless in Khayyam Math** — email is signed into the magic-link token and the session cookie, never persisted to a DB column. "Distinct users" can only be approximated by `COUNT(DISTINCT ip_hash) FROM sessions`.
- **AI4Math's `ICML.cc/2026/Workshop/AI4Math` OpenReview link is the Seoul workshop** — user briefly thought it pointed to Bremen (IJCAI-ECAI). Cross-check via [ai4math2026.github.io](https://ai4math2026.github.io/) if doubtful.

Related: [[project_session_2026_05_25]] (prior day — JAIR draft, conference paper refresh, footer deploy).
