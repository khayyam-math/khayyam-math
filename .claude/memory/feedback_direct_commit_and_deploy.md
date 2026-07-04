---
name: feedback-direct-commit-and-deploy
description: "For user's own changes (or Claude acting on user's behalf), commit + deploy directly to production; do NOT route through PR + CI check, even though the branch-protection ruleset enforces it for external contributors."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 16dbc859-0357-4179-ab4a-f9892cd4f209
---

When the user asks for a fix or change, the workflow is: edit → test → commit on main → `infra/deploy.sh` → push origin/main. Do NOT push to a feature branch, open a PR, wait for CI, then merge — that path exists for external contributors only.

**Why:** The branch-protection ruleset on `khayyam-math/khayyam-math` (id 17211693, see [[project_launch_ready_2026_05_15]]) was set up for the open-source launch so external PRs go through CI. It was not meant to slow down the maintainer's own iteration on production bugs. The user is the sole-author of the software (see [[project_authorship]]) and treats production as their own dev environment — a 10-minute PR turnaround for every small fix is friction without value for them.

**How to apply:**
- Run tests locally; if green, commit directly on `main`.
- Push via `git push origin main`. If the ruleset rejects with "Changes must be made through a pull request" / "Required status check expected", that means the user's admin-bypass is not yet enabled on the ruleset — flag it so they can enable it (Settings → Rules → Rulesets → edit the active ruleset → Bypass list → add "Repository admin").
- Until bypass is enabled, the workable fallback is: deploy from the local tree first (`infra/deploy.sh` ships the working-copy commit regardless of remote state), then push a feature branch + open a PR + ask the user to merge so origin/main catches up.
- Branch protection is meant for [[feedback_sevim_paper_separate]]-style external contributors, not for the maintainer.

Related: [[feedback_deploy_wrapper]] (always `infra/deploy.sh`, never bare `cdk deploy`), [[feedback_test_generation_after_deploy]] (always test live after deploy).
