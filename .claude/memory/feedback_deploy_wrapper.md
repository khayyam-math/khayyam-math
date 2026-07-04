---
name: Always deploy via infra/deploy.sh — bare `cdk deploy` is destructive
description: Direct `npx aws-cdk deploy` without SEVIM_DOMAIN exported drops the ACM cert and flips PublicListener back to port 80, racing the redirect listener. Use the wrapper.
type: feedback
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
Use `infra/deploy.sh` (or `infra/deploy.sh diff`, `infra/deploy.sh synth`, etc.) for ANY SevimStack deploy.  It exports the env vars the stack needs.

**Why:** running bare `npx aws-cdk deploy` without `SEVIM_DOMAIN=khayyammath.com` causes CDK to synth a no-HTTPS stack.  The diff becomes destructive — drops the ACM cert, flips PublicListener from 443 HTTPS → port 80 HTTP, and conflicts with the existing port-80 redirect listener.  Deploy fails with `Listener port '80' is already in use`; CloudFormation auto-rolls back, but the operator was about to demolish production HTTPS.  This happened once (2026-05-13 06:30) and rollback saved us.

**How to apply:**
- ANY infra change → `cd infra && ./deploy.sh`.
- Want to dry-run first → `cd infra && ./deploy.sh diff`.
- Tear down (rare) → `cd infra && ./deploy.sh destroy`.
- Override env (typo-redirect domains, alternate profile, etc.) → export the vars BEFORE invoking the wrapper; it only sets defaults when unset.
- Live config: `SEVIM_DOMAIN=khayyammath.com`, `AWS_PROFILE=sevim`, account 332504859695, region us-east-1, no `SEVIM_REDIRECT_DOMAINS` currently.
