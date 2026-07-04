---
name: project-session-2026-05-31-overnight
description: "Overnight 2026-05-30 -> 2026-05-31 — fixed language matching, fixed conversation-awareness for refinement requests"
metadata: 
  node_type: memory
  type: project
  originSessionId: 16dbc859-0357-4179-ab4a-f9892cd4f209
---

User went to bed leaving two final tasks: (1) make narration follow the
user's language including number-as-words for non-English TTS; (2) make
the system aware of the prior canvas so "please change the colour of
edge A-B to red" actually edits the figure instead of generating a
completely new one.  Worked through it autonomously, four deploys.

Deploys (all via `infra/deploy.sh`, AWS_PROFILE=sevim):

  - f50d823  language: narration follows the user's language + digits as words
              (system-prompt LANGUAGE RULE + localise_narration post-processor)

  - 3023062  conversation: refinement turns bypass deterministic routes
              (_refining = bool(context_canvases); every deterministic route
               in express_figure now gates on `and not _refining`)

  - 2a49dba  localise: fix English-to-Spanish hallucination on plain English
              prompts (ASCII fast-path + trust LLM's language='en' decision)

  - 2bfda71  refinement: route on user's literal message, send literal to
              figure LLM (looks_like_refinement now keys off
              original_user_prompt; figure LLM sees the user's literal in
              REFINEMENT MODE so "change colour to red" isn't lost to
              chat-LLM paraphrasing)

Live verified (screenshots in /tmp/shots, SSE logs in /tmp/refine and
/tmp/verify):

  - EN prompt -> English narration (no Spanish hallucination).
  - DE prompt -> German narration with "eins Komma fünf" (TTS-friendly).
  - FA prompt -> Persian narration with "یک و نیم".
  - ZH prompt -> Chinese narration with "一点五".
  - T1 (Newton) then T2 ("please change the colour of the function curve
    to red.  keep everything else the same.") -> the curve in T2 is now
    stroked in red (#c0392b) and the rest of the figure is preserved;
    context_used = [T1 canvas id]; retries_used = 2 (LLM-SVG path with
    REFINEMENT MODE, not the deterministic newton_method template).
  - T1 (Newton) then T3 ("show the Pythagorean theorem on a 3-4-5") ->
    context_used = []; title = "Pythagoras"; deterministic Pythagoras
    template fires.  No false-positive refinement.

Why: deploys 1+2 needed deploys 3+4 because verification surfaced (a)
gpt-4o-mini hallucinating Spanish on English prompts even though the
prompt told it to default to English, and (b) the chat LLM paraphrasing
"change the colour of the curve to red" into "Show f(x) = x² with the
tangent at x = 3" — which had no refinement cue so the existing
looks_like_refinement() filter dropped the prior canvas.  Fixed by
adding an ASCII fast-path to the localiser AND routing every
refinement-detection step on the user's literal `original_user_prompt`
rather than the chat-LLM's paraphrased tool prompt.

Known unfinished: the LLM-SVG path on refinement turns sometimes
produces vision-flagged figures (huge dots, mislabeled slope, the
common pre-existing LLM-SVG quality issues).  But the CORE refinement
behaviour — preserve the figure, apply the edit, no fresh-topic
overlay — now works.

Latest commit on origin/main: 2bfda71.  ECS rev advanced 4 times this
session.  Auth cookie minted from sevim/auth_secret via AWS Secrets
Manager for cookie-based curl verification scripts.
