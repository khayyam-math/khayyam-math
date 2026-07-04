---
name: 2026-05-24/25 — JAIR submission cycle + viz+narration refocus + community polish
description: Long session covering JAIR draft refactor, desk-rejection fix, Zenodo deposit, conference paper refresh (AI4Math/AIED/LogiSymb), production footer deploy, and GitHub repo public-launch polish.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
## Final state at end of session

### JAIR paper (arashkermaniprojects/khayyam-math-paper)
- **HEAD:** `df533d2` (post-desk-reject fix); 30 pages
- **Title:** *"Khayyam Math: A Voice-Narrated Math Tutor with Multi-Tool Figure Routing and Vision-Audited Generation"* — short form; the "Open Negative Result on Neural Layout Correction" clause was dropped from the title (still a section inside the paper)
- **Author block:** Arash Kermani Kolankeh, arash_kermani@yahoo.com, Independent Researcher, **Germany** (not UAE; user identifies as German independent author)
- **JAIR submission:** paper_id 23063 desk-rejected 2026-05-24 by automated screener for (a) missing JAIR Reference Format block (root cause: `\acmVolume{0}\acmArticle{0}` triggered a degenerate render path), (b) missing reproducibility checklist. Both fixed in HEAD: bumped to `{1}{1}`, added `\received[accepted]{TBD}`, added Appendix A with all four checklist sections completed honestly. **Ready to resubmit** as soon as the user clicks through OJS again.
- **Comments-to-Editor:** the same three answers from the original submission can be reused, prepended with a short resubmission disclosure (drafted in last response).
- **Zenodo preprint live:** DOI `10.5281/zenodo.20367147`, CC-BY 4.0. Footer + README badge + paper "Code and live deployment" section all reference this DOI. After JAIR accept, edit the Zenodo record to add `isPreviousVersionOf` Related Identifier pointing to the JAIR DOI.

### Conference paper refresh (paper/submissions/, gitignored — local only)
- **AI4Math @ ICML 2026** (deadline 2026-05-25 AoE, TODAY): 6 pages, refreshed with viz+narration framing, LoRA section cut from 5-section + table to 1-paragraph side-artifact, title dropped "LoRA on Qwen2.5-7B" clause, Schema-Compliance-is-Cheap discussion subsection removed
- **AIED Interactive Events Track** (deadline 2026-05-29): 3 pages LNAI, was already viz+narration centred — only citation renames applied. Still needs the 5-min demo video as supplementary upload.
- **LogiSymb @ IJCAI-ECAI 2026** (deadline 2026-05-31): 5 pages, verifier chain is already the centerpiece — only citation renames applied
- All three are anonymised for double-blind. All compile clean (zero undefined refs).

### Production (khayyam-math/khayyam-math)
- **Footer deployed live on khayyammath.com:** "Source: github.com/khayyam-math/khayyam-math · Paper: doi.org/10.5281/zenodo.20367147 · Contact: LinkedIn (dr-arash-kermani-kolankeh-69b23296)". Footer on BOTH `service/static/landing.html` (homepage) and `studio/static/studio.html` (chat surface). Per user: NO email in the footer (spam concern).
- **ECS revision after deploy:** new container image; ServiceUrl = https://khayyammath.com; ALB + HTTPS preserved
- **GitHub repo public; Discussions enabled; squash-only merging applied** per user's settings update on 2026-05-24. Branch-protection rules NOT yet configured — recommended to user.
- **Social preview image:** NOT done. Built two attempts (unit-circle then Pythagoras), user disliked both — last attempt had the Pythagoras triangle bleeding into the title text. Open item for next session.

### Citation audit (full verification on the JAIR refs.bib)
5 critical errors fixed in the JAIR paper's refs.bib:
- `macina2025reviewllmtutors` → `garcia2024reviewllmtutors` (wrong authors entirely; venue was arXiv, actually Science & Education Springer 2024)
- `maurya2025adaptivity` → `borchers2025adaptivity` (Borchers/Shou is the real first author; "Maurya, Kaushal Kumar" was a hallucinated attribution)
- `kong2022layoutformerpp` → `jiang2023layoutformerpp` (Jiang is first author, CVPR 2023 not NeurIPS 2022)
- `zhang2024layoutdiffusion` → `zhang2023layoutdiffusion` (Guo Jiaqi not Jiaxian, Sun Shizhao not Shun, ICCV 2023 not AAAI 2024)
- `xing2026reasonsvg` (key kept): Xue Ziteng not Zhiyu, Guan Yandong not Yu
Cosmetic: `liu2024hallusionbench` → `guan2024hallusionbench` (Guan is first author), Self-Refine missing Hermann added, StarVector unverified pages removed.

The same 5 cite-key renames were also applied to the three conference papers via sed.

### AI-use disclosure
The JAIR paper's `\begin{acks}` block now contains an explicit disclosure: *"Anthropic's Claude large language model was used during manuscript preparation for prose drafting, copy-editing, and consistency checks across revisions; the author reviewed every claim, number, and citation in the submitted text and is responsible for the content."* Note: ONLY Claude is named, NOT GPT-4o (user corrected an earlier draft that mentioned both — GPT-4o was not used for prose).

## Open items for next session
1. **Social preview image** — neither the unit-circle nor the Pythagoras crops worked cleanly. Try a different approach: maybe a 4-panel grid of figures + tagline, OR a synthetic matplotlib-rendered Pythagoras drawn from scratch (no chat-header bleed-through), OR just title text on a clean math-themed background.
2. **Demo video with audio** — user will record on Loom (system audio capture). Once done, AIED IE submission can go in.
3. **JAIR resubmission** — user has the fixed PDF, needs to click through OJS again. Re-paste the three Comments-to-Editor answers with a one-line "resubmission after desk-reject" preface.
4. **Branch protection rules on khayyam-math/khayyam-math** — recommended setup walked through in last response; user hasn't configured yet.
5. **Three conference submissions** — refreshed PDFs ready; user needs to upload via the respective portals (OpenReview / EasyChair / OpenReview). AI4Math deadline is today AoE.
6. **arXiv preprint** — not done; Zenodo only. arXiv would massively improve academic discoverability; ~10-minute upload, same PDF, CC-BY.

## Hard-won gotchas this session
- `\acmVolume{0}` or `\acmArticle{0}` placeholders in the JAIR/ACMART template silently suppress the "JAIR Reference Format" auto-block that the screener checks for. Use `{1}{1}` or real values.
- JAIR's reproducibility checklist (line 530 of `JAIR_Example_Template.tex`) is mandatory as an appendix — desk-reject if missing.
- `paper/` is gitignored at the project root; conference submissions live local-only. Only the JAIR paper has a remote (khayyam-math-paper).
- The 3 conference papers' refs.bib files originally had Lean/Z3/Logic-LM entries that the JAIR refs.bib never had; copying JAIR's bib over them lost those entries. Re-appended in this session.
