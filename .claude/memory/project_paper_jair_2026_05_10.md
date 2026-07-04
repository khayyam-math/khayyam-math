---
name: 2026-05-10 — JAIR paper draft + LoRA v2 ablation + narration interrupt
description: Wrote a 12-page JAIR submission ("Sevim Studio: A Narrated Diagram Tutor...") covering the express path, narration interrupt, telemetry→LoRA loop, and the v1+v2 hyperparam A/B. Paper at paper/sevim_studio_jair/main.pdf. Cites Kermani Kolankeh & Zgheib 2026 Zenodo prior work + 33 other refs.
type: project
originSessionId: ddab3e35-4da7-437e-965d-3a536788200b
---
## Venue

JAIR (Journal of Artificial Intelligence Research). Diamond OA, Q1 in
AI on Scimago, AI Access Foundation nonprofit (no APC), ~3-month
decisions.  Template based on ACM acmart.cls + jair.cls wrapper, uses
biblatex with acmauthoryear style and biber backend.  Author kit at
https://www.jair.org/index.php/jair/libraryFiles/downloadPublic/6.

Other venues considered and rejected:
- **Computers and Education: AI** — appears in some "diamond OA"
  lists but the official Elsevier author guide says "all authors
  understand they are responsible for payment" (it's a paid OA).  Not
  free.
- **TMLR** — free + fast + JMLR-affiliated, but too new for a
  Scimago Q1 ranking, so failed the explicit Q1 criterion.
- **JMLR** — Q1 + free but too slow (~6 month decision).

## Files

```
paper/sevim_studio_jair/
  main.tex              41 KB  the manuscript (12 pages typeset)
  refs.bib              15 KB  34 entries, 33 cited
  main.pdf             554 KB  the compiled output
  acmart.cls / jair.cls / acmauthoryear.bbx / .cbx / .dbx
                              copied from JAIR AuthorKit
```

The whole paper/ directory is already gitignored at line 44 of
.gitignore (`/paper/`), so nothing leaks into the public repo.

## Compilation gotchas

1. JAIR's `acmauthoryear.bbx` uses biblatex options `halid`, `swhid`,
   `swlabels`, `vcs` that require biblatex >= 3.20.  TinyTeX shipped
   an older version; commented those out near line 894 of
   `acmauthoryear.bbx` (5 lines).  All software-citation flags off
   but normal cites still render.
2. acmart requires `\country{}` to be non-empty even for blinded
   submissions.  Set to `N/A`.
3. `Dess{\`\i}` in the Toolformer cite triggered a Unicode combining
   accent error in biblatex output — replaced with plain `Dessi`.
4. `Ma'ayan` in the Penrose cite needed `Ma{\textquoteright}ayan` to
   typeset cleanly.
5. doclicense + xifthen aren't in the minimal TinyTeX bundle — installed
   via `tlmgr install doclicense xifthen`.
6. biber lives at `/home/ara/.TinyTeX/bin/x86_64-linux/biber`, not on
   default PATH — export `PATH="/home/ara/.TinyTeX/bin/x86_64-linux:$PATH"`.

## Build sequence

```
cd paper/sevim_studio_jair
export PATH="/home/ara/.TinyTeX/bin/x86_64-linux:$PATH"
pdflatex -interaction=nonstopmode main.tex   # first pass
biber main                                    # bbl
pdflatex -interaction=nonstopmode main.tex   # crossrefs
pdflatex -interaction=nonstopmode main.tex   # final
```

Three remaining warnings, all benign: "no city present" (cosmetic
acmart accessibility nag), "node center" PGF warning (figure renders
correctly), and a `\vspace` warning emitted by biblatex's
internals.

## Paper structure (12 pages)

1. Introduction — motivates narrated diagrams, lists 4 contributions,
   cites prior Zenodo work (Kermani Kolankeh & Zgheib 2026)
2. Background and Related Work — vector-graphics gen (4 sub-paras),
   diagram synthesis, LLMs as tutors and judges, MCP, LoRA
3. System Architecture — TikZ block diagram (Fig. 1), express path
   with JSON-schema listing, vision-audit retry, regex pre-router
4. Browser Viewer & Narration Sync — SSE, Piper TTS, reveal mask,
   multi-element highlights, 0.18 s phrase gap
5. Narration Interrupt — postMessage on focus/input/mic/send,
   why mic-click trigger is essential
6. Closed-Loop MCP Server — 14 tools, closed vocabulary stats
7. Closed-Loop Improvement — telemetry schema, export filter,
   42 s LoRA fine-tune (Table: hyperparams v1 vs v2)
8. Empirical Study — 20 prompts, 3 models, schema compliance table,
   LLM-judge mean scores, bimodal-failure breakout, v2 ablation
9. Discussion, Limitations, Conclusion

## Key facts in the paper, all double-checked against the codebase

- 14/14 cited file paths exist
- canvas.html: 2 mentions of `sevim_pause_audio`
- studio.html: 5 mentions of `interruptNarration` (1 def + 4 calls)
- telemetry.py: 3 CREATE TABLE statements (sessions/turns/canvases)
- diverse_prompts_test.PROMPTS: exactly 20 entries
- 54 pytest tests collected (matches paper claim)
- Piper voice path `~/.local/share/sevim/voices/en_US-lessac-medium.onnx`
  documented in sevim/narrate.py
- _PHRASE_GAP_S = 0.18 in sevim/narrate.py (matches paper claim)
- LoRA v1 trained 42 s, final loss 0.28 (per /tmp/lora_train.log)
- LoRA v2 trained 28 s, final loss 1.18 (per /tmp/lora_train_v2.log)
- Judge totals match `/tmp/sevim_compare/judge_scores.csv` and
  `judge_lora_v1_vs_v2.csv` byte-for-byte

## Citation of authors' prior work

`@misc{kermani2026sevim, ...}` cites
`https://zenodo.org/records/20011107` (DOI 10.5281/zenodo.20011107)
in §1 (Introduction) where the new work is positioned as the
LLM-evolution of the prior deterministic semantic-to-visual mapping
system.

## Authors as listed

- Arash Kermani Kolankeh (corresponding) — arash_kermani@yahoo.com
- Rita Zgheib — placeholder email + "Affiliation withheld for review"
  (since blinding wasn't specified, and no current affiliation in
  scope; user can replace with real ones before submission)

## Reproducing the paper from scratch

If the bibliography goes stale or a referee asks for an update:

```bash
cd /home/ara/Documents/Programming/sevim_plugin/paper/sevim_studio_jair
$EDITOR main.tex refs.bib       # change content
export PATH="/home/ara/.TinyTeX/bin/x86_64-linux:$PATH"
pdflatex main.tex && biber main && pdflatex main.tex && pdflatex main.tex
```

## Opportunities to extend before submission

- Add 2-3 actual rendered example figures from the corpus (PNG
  side-by-sides of GPT-4o vs LoRA-v1 vs LoRA-v2 on the same prompt)
  to ground the bimodal-failure narrative in visible artefacts.
  Today the paper has only the architecture diagram.
- Run a held-out eval set to address the "no held-out eval" threat
  to validity.
- Add a screenshot of Studio (chat + canvas iframe) so the paper
  isn't an entirely text-based description of a UI.
- Replace the placeholder affiliation/email for Rita Zgheib.
- If targeting JAIR camera-ready, switch
  `\documentclass[manuscript, screen, review]{jair}` to
  `\documentclass[]{jair}`.
