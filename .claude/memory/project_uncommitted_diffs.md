---
name: Pre-existing diffs left uncommitted on 2026-05-14
description: Two tracked files have modifications from PRIOR sessions that the 2026-05-14 graphviz session did NOT touch. Listed here so a future session knows they're real work, not abandoned scratch.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
After the 2026-05-14 graphviz + neural-layout commits, two tracked
files still showed `M` in `git status`. They are NOT from the
2026-05-14 session; they were already modified when that session
started. Listed here so future sessions don't either:
  (a) lose them by reverting, or
  (b) commit them under the wrong session's authorship.

  - `infra/cdk.context.json` — added a `xam.ae` hosted-zone cache
    entry. Safe to commit when you set up the alt-domain.
  - `scripts/generate_reference_corpus.py` — added support for
    `image_path` (local file) as a source alongside the existing
    `image_url` (Wikimedia Commons). Looks complete. Commit when
    you intend to use it.

**How to apply:** `git diff infra/cdk.context.json scripts/generate_reference_corpus.py`
to review. If you want them in, commit them with a session-appropriate
message. If you want them out, `git checkout -- <file>`.

Why:** the user is paranoid about "destructive operations" and
the 2026-05-14 session deliberately left these alone to avoid
either taking credit for older work or reverting it.
