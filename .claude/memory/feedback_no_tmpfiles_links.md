---
name: Push to repo, don't surface tmpfiles links
description: When making changes to the paper or other files in a git repo, push to the remote and stop — don't also upload to tmpfiles.org and paste download links in the chat.
type: feedback
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
When editing files in a git repo (paper, code, configs), push the change to the remote and stop. Do not also upload the file to tmpfiles.org and paste download links into the chat reply.

**Why:** The user pulls from the repo on their other machine; the tmpfiles links are clutter once the repo push is the source of truth. Felt like noise.

**How to apply:** After a commit + push, the response is "pushed as <SHA>" and a one-line summary of the change. Skip the tmpfiles upload, skip the file/size/URL table. Only upload to tmpfiles when the user explicitly asks for a downloadable copy (e.g. "upload it so I can download it on my Mac").
