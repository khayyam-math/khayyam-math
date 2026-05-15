## What this PR does

A 1-3 sentence summary. Lead with the user-visible change, then the
implementation approach.

## Why

What problem does this solve? Reference an issue if applicable
(`Closes #123`).

## How to verify

```bash
# Concrete commands a reviewer can paste to confirm the change works.
.venv/bin/python -m pytest tests/...
```

## Checklist

- [ ] Tests pass (`.venv/bin/python -m pytest -q`)
- [ ] No new heavy native dependency added (or, if added, discussed
      in a prior issue)
- [ ] No secrets / API keys committed
- [ ] User-visible change? README / ARCHITECTURE.md updated
- [ ] If touching the express loop or layout planner: ran a manual
      screenshot audit (`scripts/audit_studio_screenshots.py`) on
      3+ prompts and confirmed no regression
- [ ] For larger PRs: I opened an issue / discussion first per
      CONTRIBUTING.md

## Screenshots (optional but appreciated)

If your change affects rendered output, paste a before/after.
