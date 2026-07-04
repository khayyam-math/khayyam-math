---
name: reference_email_auth_dns
description: "SES email-auth DNS records for khayyammath.com (magic-link deliverability) — managed out of band, not in CDK"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 99d729af-e760-4960-a041-8a1eccc50fb2
---

Magic-link mail sends from `noreply@khayyammath.com` via SES (us-east-1), code in `studio/auth.py:_send_magic_link`. From-address secret: Secrets Manager `sevim/ses_from`. SES domain identity `khayyammath.com` was created MANUALLY (not a CloudFormation resource), so email-auth records are managed OUT OF BAND in Route53 zone `Z0798668111KS8AKCI6HZ`, not in CDK (declaring `ses.EmailIdentity` would collide).

Records (applied 2026-06-10 to fix spam-foldering):
- DKIM: Easy-DKIM CNAMEs, SES-managed, status SUCCESS (was already fine).
- SPF apex: TXT `v=spf1 include:amazonses.com ~all` (alongside the google-site-verification TXT — keep both when editing the apex TXT).
- MAIL FROM: `mail.khayyammath.com` (MX `10 feedback-smtp.us-east-1.amazonses.com` + SPF TXT). Set via `aws sesv2 put-email-identity-mail-from-attributes`, BehaviorOnMxFailure=USE_DEFAULT_VALUE.
- DMARC: `_dmarc` TXT `v=DMARC1; p=none; sp=none; adkim=r; aspf=r` (monitoring only). No `rua` because the domain has no inbound MX. To tighten later: add a reporting mailbox, confirm alignment, then `p=quarantine`.

Root cause of spam was missing DMARC + missing MAIL-FROM SPF alignment despite valid DKIM (Gmail/Yahoo 2024 sender rules). See [[reference_runtime_paths]] and [[feedback_keep_magic_link_auth]].
