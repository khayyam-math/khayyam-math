# Security Policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.
Public disclosure before a fix is in place puts users at risk.

**Report privately via GitHub's built-in security advisory tool:**
1. Go to the [Security tab](../../security) of this repository.
2. Click **"Report a vulnerability"**.
3. Fill in the details (see the template below).

If you cannot use the GitHub tool, email the maintainer directly at the
address on the [CITATION.cff](CITATION.cff) file with the subject line
`[khayyam-math] Security report`.

## What to include in a report

- A short description of the vulnerability and its impact.
- Steps to reproduce or a minimal proof-of-concept.
- The component affected (Studio web app, MCP server, a specific template,
  the auth system, CI/CD, infra, etc.).
- Whether you believe it is exploitable in the current public deployment
  at [khayyammath.com](https://khayyammath.com).

You do not need a complete exploit — a clear description of the attack
surface and the preconditions is enough to start a conversation.

## Response timeline

| Milestone | Target |
|---|---|
| Acknowledgement of your report | Within 48 hours |
| Triage (confirmed / not confirmed) | Within 5 business days |
| Fix or mitigation shipped | Depends on severity (see below) |
| Public disclosure | After fix is deployed, coordinated with reporter |

Severity targets:

- **Critical** (authentication bypass, RCE, secret leakage): fix within 7 days.
- **High** (XSS in the canvas iframe, SSRF, privilege escalation): fix within 14 days.
- **Medium / Low**: scheduled in the next regular release.

## Scope

In scope:

- The Studio web application (`studio/`, served at `khayyammath.com`).
- The MCP server (`mcp_server/`).
- Authentication and session handling (`studio/auth.py`).
- The SVG generation pipeline and any injection surface in generated figures.
- Infrastructure configuration (`infra/`) that affects secret handling or
  access control.

Out of scope (please do not report these as vulnerabilities):

- Rate-limit bypasses that require rotating real IPs — we know the
  in-memory limiter has limits and accept that trade-off for simplicity.
- Issues that require physical access to a maintainer's machine or AWS
  account with admin rights.
- Findings from automated scanners without manual verification that
  they are exploitable in this codebase.
- Vulnerabilities in third-party dependencies that are not exploitable
  through the surfaces above — please report those upstream.

## Credit

We will credit researchers who report valid, in-scope vulnerabilities
in the public GitHub Security Advisory once the fix ships, unless you
prefer to remain anonymous.

## Supported versions

Security fixes are applied to the current `main` branch only.
No backport releases are planned.
