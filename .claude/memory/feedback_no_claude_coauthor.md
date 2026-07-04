---
name: no-claude-coauthor-trailer
description: "Never add the \"Co-Authored-By: Claude ...\" trailer to git commit messages — leaves a Claude avatar on the public GitHub repo, which the user does not want."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 1b21d948-80da-4907-b09f-285b518cc448
---

Never append the `Co-Authored-By: Claude Opus ... <noreply@anthropic.com>`
trailer to commit messages.  Write the commit body and stop — no
attribution trailer of any kind.

**Why:** the trailer makes GitHub render a Claude logo as a co-author
avatar on the repo home page, the contributor list, the commit pages,
and on every PR.  The user does not want any AI-co-author signal
showing on the public `khayyam-math/khayyam-math` repo (or any other
public repo).  This overrides the default Claude Code instruction
that adds the trailer at commit time.

**How to apply:** when running the commit step, just omit the trailer
entirely.  Do NOT replace it with `Co-Authored-By: <noreply@...>` or
any redacted variant — leave the message clean.  Applies to every
repository, not just the public ones, so the rule never has to be
remembered per-repo.
