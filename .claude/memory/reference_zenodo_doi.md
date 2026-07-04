---
name: reference_zenodo_doi
description: "Khayyam Math paper Zenodo DOIs: concept (always-latest) vs version; which to cite"
metadata:
  node_type: memory
  type: reference
  originSessionId: 99d729af-e760-4960-a041-8a1eccc50fb2
---

Khayyam Math paper on Zenodo (title "Khayyam Math: A Voice-Narrated Math Tutor with Multi-Tool Figure Routing and Vis..."):

- **CONCEPT DOI (cite this): `10.5281/zenodo.20367146`** — version-agnostic, always resolves to the newest version. Use everywhere people cite the paper. Find it on the Zenodo record's right sidebar under "Versions" → "Cite all versions?".
- Version DOIs: `…20367147` (v1, 2026-05-24) and `…20664387` (latest, 2026-06-12). These point at one specific upload only.
- The site (landing page, studio footer, all `service/static/learn/*.html`) links to the **concept DOI** as of 2026-06-12 (commit 470f857, deployed) — so future paper revisions need NO site change.

**SeVim is a SEPARATE paper** (the deterministic predecessor system): "SeVim: Deterministic Semantic-to-Visual Mapping for Real-Time Diagram Generation via Hybrid Neuro-Symbolic Reasoning", Arash Kermani Kolankeh & Rita Zgheib, 2026-05-03. SeVim **concept DOI `10.5281/zenodo.20011106`** (version `…20011107`). The paper's SeVim citation was WRONG (pointed to `…20367147` = Khayyam Math's own record); FIXED 2026-06-12 (commit 95994b7) to cite SeVim's concept DOI `…20011106`. So: Khayyam Math paper = concept `…20367146`; SeVim paper = concept `…20011106`. Don't conflate them. See [[project_authorship]] (Rita Zgheib joint on SeVim) and [[project_session_2026_05_25]] (old note lists `…20367147` — that was a conflation).
