---
name: Keep magic-link auth — it is not "bothering people with profiles"
description: The user wants magic-link sign-in kept ON; "no authentication / no profiles" does NOT mean disable auth — magic-link is the intended low-friction email-only flow.
type: feedback
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
Magic-link sign-in (`SEVIM_AUTH_REQUIRED=1`) must stay enabled.

**Why:** the user once said "I don't want authentication … don't
bother people with creating profiles", which I wrongly read as
"disable auth" and shipped `SEVIM_AUTH_REQUIRED=0`. They corrected
it: "No no. The magic link should be sent. We should keep the email
addresses for our statistics." Magic-link auth IS the low-friction
flow they want — email-only, no password, no profile page. The
"profiles" they object to are heavyweight signup, which the app
never had.

**How to apply:** never disable `SEVIM_AUTH_REQUIRED`. The captured
email is intentional — it's what lets distinct people be counted.
Telemetry stores it in `sessions.user_email`; `/studio/admin/stats`
reports `distinct_users`. If the user asks to "reduce friction" on
sign-in, improve the magic-link UX — do not remove the gate.
