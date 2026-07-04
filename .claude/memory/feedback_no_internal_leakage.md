---
name: Never leak internal implementation detail to the client
description: The user wants no internals (model names, "SVG"/"OpenAI"/"vLLM", endpoint schema, architecture comments, raw errors) reachable by reading the webpage or its network traffic.
type: feedback
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
Anything the browser receives must not disclose how Khayyam Math
works internally. Concretely the user objected to: a status pill
reading "openai unreachable", a loading message "Drafting the SVG…",
model names in `/health`, FastAPI's auto-docs, and architectural
comments in the served HTML.

**Why:** the user is doing a slow, deliberate go-live and said
"security matters … all kind of information should be protected …
no one should be able to reverse-engineer it." Reframe the goal
honestly: a web app can't be made impossible to reverse-engineer
(the client must know which endpoints to call), but unauthorized
*use* and internal-detail *disclosure* can and must be eliminated.

**How to apply:** when adding any user-facing string, status, or
error, keep it generic ("Service temporarily unavailable",
"Preparing your visualization…", "Couldn't generate that figure").
Never echo raw exception text to the client. Keep new endpoints out
of the OpenAPI schema and behind auth. Don't reintroduce model /
provider / pipeline names into client code or comments.
