---
name: Bug report
about: Something is rendering wrong, missing, off-canvas, or unsafe
title: 'bug: '
labels: bug
assignees: ''
---

**What prompt did you send?**
(Paste the exact text you typed into the studio chat.)

**What did you expect to happen?**

**What actually happened?**
(If possible, attach a screenshot of the canvas. The canvas iframe
ID is shown in the top-left of the canvas pane: `express_xxxxxx`.)

**Which route did the prompt take?**
(Check the status bar above the canvas; it shows `0 nodes / 0 edges`
for graphviz/LLM-SVG paths and node/edge counts otherwise. Or look
at the `template` field in the chat history.)

- [ ] Template fast-path (matrix, system, state-diagram)
- [ ] Graphviz route (DFA, Turing machine, tree, Hasse, etc.)
- [ ] LLM-SVG route (everything else)
- [ ] Unknown

**Environment**
- Where: live site (khayyammath.com) / local install / forked deploy
- Browser: Chrome / Firefox / Safari / mobile
- Date + time (helps us correlate to logs):

**Anything else?**
