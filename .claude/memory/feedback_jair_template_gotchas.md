---
name: JAIR/ACMART template gotchas — Reference Format block + reproducibility checklist + abstract length
description: Three non-obvious JAIR submission requirements that cause automated desk-reject. Learned the hard way across paper_id 23063 (2026-05-24) and 23066 (2026-05-25).
type: feedback
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
The JAIR automated AI screener parses **only page 1** of the submitted PDF. Three issues all desk-reject:

**1. `\acmVolume{0}` or `\acmArticle{0}` placeholders silently suppress the JAIR Reference Format auto-block.**
The JAIR class (`jair.cls`) generates a "JAIR Reference Format:" block under "JAIR Associate Editor:" inside `\maketitle`. With `{0}`/`{0}` placeholders the internal conditional treats them as empty and skips the block. **Fix:** use `\acmVolume{1}` and `\acmArticle{1}` (any non-zero). Also add `\received[accepted]{TBD}` — the example template has both.

Side effect: when this conditional misfires, the first section heading also loses its number ("Introduction" instead of "1 Introduction").

**2. The reproducibility checklist is mandatory as an appendix.**
JAIR's submission guidelines (https://www.jair.org/index.php/jair/about/submissions#submission) state: *"Submissions without a completed checklist will be desk rejected."* The checklist text is at line 530 of `JAIR_Example_Template.tex` in the AuthorKit ZIP. Four sections: All articles / Theoretical / Computational experiments / Data sets. Must be `\appendix` + `\section{Reproducibility Checklist for JAIR}` after `\printbibliography`.

**3. Abstract must be short enough that the JAIR Reference Format block fits on page 1.** ← THIS IS WHAT KILLED PAPER_ID 23066.
The screener desk-rejects with "moves directly from the abstract-like 'Conclusions' to the author's contact information and legal notices" when the abstract is too long. Even with `\acmVolume{1}` correctly set, an oversized abstract pushes the Reference Format block to page 2, where the screener doesn't look. The JAIR template's example abstract is ~250 words; ours was ~600+ and got rejected. **Fix:** keep the Background/Objectives/Methods/Results/Conclusions structure but 1–3 sentences per section, ~250–350 words total.

**How to apply:** Before submitting any JAIR paper, render the PDF and confirm — *visually on page 1, not in the source* — that:
- "JAIR Reference Format:" appears on page 1 under "JAIR Associate Editor:"
- "1 Introduction" section header appears on page 1 (even if just the heading + first line)
- An appendix "Reproducibility Checklist for JAIR" exists at the end

If "JAIR Reference Format" is rendered but on page 2, the abstract is too long — shorten it. The screener is unforgiving and only sees page 1.
