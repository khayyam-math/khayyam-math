"""Graphviz-based diagram renderer.

For prompts that ask for a GRAPH-SHAPED figure (state machines,
Turing machines, DAGs, trees, dependency graphs, automata), we
prompt the LLM to emit DOT-language source instead of raw SVG, then
hand it to Graphviz's mature layout engine.

Why DOT > raw SVG:
- DOT is small and well-structured (LLMs are very good at it)
- Graphviz has 30+ years of well-tuned layout algorithms
- Engines: `dot` (hierarchical), `neato` (spring), `fdp`/`sfdp` (force),
  `circo` (circular), `twopi` (radial) — picked per diagram shape
- Output is valid, in-bounds SVG with no overlap by construction

Public API:
    render_graphviz(dot_source, engine="dot") -> svg_text
    is_graphviz_prompt(prompt) -> bool
    GRAPHVIZ_SYSTEM_PROMPT -> str  # for the LLM's system message
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Optional

# DOT engines, ordered by typical use case in our domain.
_KNOWN_ENGINES: tuple[str, ...] = (
    "dot", "neato", "fdp", "sfdp", "circo", "twopi", "patchwork",
)


def is_graphviz_binary_available() -> bool:
    return shutil.which("dot") is not None


def render_graphviz(
    dot_source: str, *, engine: str = "dot", timeout_s: float = 10.0,
) -> Optional[str]:
    """Render DOT source to SVG. Returns None on any failure.

    Fails open: a malformed DOT source yields None so the caller can
    fall back to the LLM-SVG path.

    Post-processes the output so the SVG scales to its container
    (mobile viewports, chat-side embed snapshots) instead of being
    fixed at Graphviz's natural pt size.
    """
    if not is_graphviz_binary_available():
        return None
    if engine not in _KNOWN_ENGINES:
        engine = "dot"
    try:
        result = subprocess.run(
            [engine, "-Tsvg"],
            input=dot_source.encode("utf-8"),
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.decode("utf-8", errors="replace")
    if not out or "<svg" not in out:
        return None
    return _make_svg_responsive(out)


def _make_svg_responsive(svg: str) -> str:
    """Strip Graphviz's fixed pt width/height on the root <svg> and
    replace with responsive sizing.

    Before: <svg width="217pt" height="458pt" viewBox="-8 -8 232.7 473.59">
    After:  <svg viewBox="-8 -8 232.7 473.59"
                 preserveAspectRatio="xMidYMid meet"
                 width="100%" style="max-width:100%; max-height:90vh;
                                     height:auto;">

    The viewBox is preserved (proper aspect ratio), the size adapts
    to the container, and `xMidYMid meet` ensures the entire diagram
    fits within the iframe pane without clipping.
    """
    # Only touch the FIRST <svg ... > tag (the root).
    m = re.search(r"<svg\b[^>]*>", svg)
    if not m:
        return svg
    tag = m.group(0)
    # Strip fixed width="..." and height="..." attrs.
    new_tag = re.sub(r'\swidth="[^"]*"', "", tag)
    new_tag = re.sub(r'\sheight="[^"]*"', "", new_tag)
    # Ensure preserveAspectRatio is present (Graphviz omits it).
    if "preserveAspectRatio" not in new_tag:
        new_tag = new_tag.replace(
            "<svg", '<svg preserveAspectRatio="xMidYMid meet"', 1,
        )
    # Insert responsive width + style.
    new_tag = new_tag.replace(
        "<svg",
        ('<svg width="100%" '
         'style="max-width:100%; max-height:90vh; height:auto; '
         'display:block; margin:0 auto;"'),
        1,
    )
    return svg[: m.start()] + new_tag + svg[m.end() :]


# Heuristic keyword classifier for routing to the Graphviz path.
# Tighter than the math-bucket classifier because we want HIGH
# precision (false negative = fallthrough to LLM-SVG, harmless;
# false positive = Graphviz emits a weird-looking diagram for
# something that shouldn't be a graph).
_GRAPHVIZ_KEYWORDS: tuple[str, ...] = (
    # finite-state / automata
    "state machine", "state diagram", "finite automaton",
    "finite-state automaton", "finite state machine",
    "dfa for", "nfa for", "regular language",
    "transition graph", "transition diagram",
    "turing machine", "pushdown automaton",
    # graph theory
    "directed acyclic graph", "dag", "topological sort",
    "tree diagram", "binary tree", "search tree", "decision tree",
    "syntax tree", "parse tree", "expression tree", "ast for",
    "abstract syntax tree", "call graph", "control flow",
    "control-flow graph", "dependency graph",
    "graph colouring", "graph coloring",
    "minimum spanning tree", "shortest path graph",
    "bayesian network", "neural network architecture",
    # specific math objects
    "hasse diagram", "poset diagram", "lattice diagram",
    "cayley graph", "cayley diagram",
    "petersen graph", "k_{", "k_n", "complete graph k",
    "bipartite graph",
    # category theory / commutative diagrams
    "commutative diagram", "commuting diagram", "commutative square",
    "isomorphism theorem", "first isomorphism", "second isomorphism",
    "third isomorphism", "exact sequence", "short exact sequence",
    "chain complex", "snake lemma", "pullback diagram",
    "pushout diagram", "natural transformation", "category diagram",
    "universal property",
    # workflow / process
    "flowchart", "flow chart", "state transition",
    "process diagram", "activity diagram",
)


def is_graphviz_prompt(prompt: str) -> bool:
    """Return True if the prompt looks like a graph-shaped diagram."""
    p = (prompt or "").lower()
    return any(kw in p for kw in _GRAPHVIZ_KEYWORDS)


# Per-diagram-class engine suggestion.
_ENGINE_HINTS: tuple[tuple[str, str], ...] = (
    ("circle", "circo"),     # graphs drawn in a circle
    ("tree", "dot"),
    ("dag", "dot"),
    ("topological", "dot"),
    ("flow", "dot"),
    ("hierarchy", "dot"),
    ("hasse", "dot"),
    ("force", "fdp"),
    ("bipartite", "neato"),
    ("complete graph", "circo"),
    ("petersen", "circo"),
    ("cayley", "circo"),
    ("state machine", "dot"),
    ("state diagram", "dot"),
    ("turing", "dot"),
    ("automaton", "dot"),
    ("decision tree", "dot"),
    ("binary tree", "dot"),
    ("commutative", "dot"),
    ("isomorphism", "dot"),
    ("exact sequence", "dot"),
    ("snake lemma", "dot"),
)


def suggest_engine(prompt: str) -> str:
    """Pick a sensible engine for the diagram shape."""
    p = (prompt or "").lower()
    for kw, engine in _ENGINE_HINTS:
        if kw in p:
            return engine
    return "dot"


# LLM system prompt for the DOT-emit path.
GRAPHVIZ_SYSTEM_PROMPT = """\
You are a math TEACHER drawing a graph-shaped diagram (state \
machine / Turing machine / DAG / tree / automaton / dependency \
graph / Hasse diagram / Cayley graph / etc.).

Emit GRAPHVIZ DOT source code that, when compiled with `dot -Tsvg` \
(or another Graphviz engine), produces a clean, labelled diagram.

Rules:
  1. ONLY return the DOT source — no prose, no SVG, no markdown.
  2. Use the `strict digraph G { ... }` form for directed graphs, \
or `strict graph G { ... }` for undirected.
  3. **Use `rankdir=LR` by default** for state machines, Turing \
machines, DFAs, NFAs, automata, control-flow graphs and flowcharts. \
These are conventionally drawn left-to-right and scroll cleanly on \
narrow mobile viewports. Use `rankdir=TB` ONLY for hierarchical trees \
(binary trees, decision trees, Hasse diagrams, syntax trees). \
For Cayley graphs, Petersen graphs, and circular layouts, omit \
rankdir and rely on the `circo` engine.
  4. Use `node [shape=circle, style=filled, fillcolor=lightblue]` \
for state-machine states; \
double-circle for accepting states (`shape=doublecircle`).
  5. Label EVERY node with a meaningful name and every edge with a \
meaningful transition / weight / condition (use `label="..."`).
  6. Pick concrete values: for a DFA over {a, b}, write actual \
transitions; for a Turing machine, write actual tape moves and \
state transitions.
  7. Keep node count small (4–10 nodes typical) unless the prompt \
demands more.
  8. Use `fontname="Helvetica"` and `fontsize=12` for legibility.
  9. For Turing machines: label edges `read/write,move` (e.g. \
`"0/1,R"` for read-0 write-1 move-right).
  10. For COMMUTATIVE DIAGRAMS (isomorphism theorems, exact \
sequences, pullbacks/pushouts, category-theory diagrams): the \
objects are mathematical structures, not states. Use \
`node [shape=plaintext, fontname=Helvetica, fontsize=14]` so each \
object renders as a plain label (e.g. `G`, `H`, `G/ker φ`, `im φ`). \
Label EVERY arrow with its morphism. Use only Unicode letters that \
render reliably (`φ`, `π`, `ι`, `θ`); avoid combining diacritics \
(no macron/tilde over a letter). For an induced isomorphism, label \
the arrow with the bare symbol `≅`. Use `rank=same` to align objects \
that belong on the same row of the diagram so the square/triangle \
shape is preserved. Draw the induced/canonical map as `style=dashed`. \
Keep `splines=true`. Do NOT use circles or doublecircles here. \
For a short exact sequence `0 -> A -> B -> C -> 0`, lay it out \
left-to-right with `rankdir=LR`.

DOT example (state machine):
strict digraph G {
  rankdir=LR;
  node [shape=circle, style=filled, fillcolor=lightblue, \
fontname=Helvetica, fontsize=12];
  start [shape=point, fillcolor=black];
  q0;
  q1;
  q2 [shape=doublecircle];
  start -> q0;
  q0 -> q1 [label="a"];
  q1 -> q2 [label="b"];
  q1 -> q1 [label="a"];
}

DOT example (commutative diagram — first isomorphism theorem,
G/ker φ ≅ im φ):
digraph G {
  splines=true;
  node [shape=plaintext, fontname=Helvetica, fontsize=14];
  G; H [label="im φ"]; Q [label="G / ker φ"];
  { rank=same; G; H; }
  G -> H [label="φ"];
  G -> Q [label="π"];
  Q -> H [label="≅", style=dashed];
}
"""


def extract_dot_from_response(text: str) -> Optional[str]:
    """Pull the DOT source out of an LLM response. Tolerates the LLM
    wrapping it in ```dot or just emitting raw."""
    if not text:
        return None
    # Strip code fences.
    fence_match = re.search(
        r"```(?:dot|graphviz)?\s*([\s\S]*?)```", text,
    )
    if fence_match:
        text = fence_match.group(1)
    # Must contain `digraph` or `graph` keyword to be valid.
    if not re.search(r"\b(?:strict\s+)?(?:di)?graph\s+\w*\s*\{",
                     text):
        return None
    return text.strip()


# --------------------------------------------------------------------
# LLM-emit-DOT fast-path
# --------------------------------------------------------------------

async def llm_emit_dot(
    user_prompt: str,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    timeout_s: float = 20.0,
) -> Optional[str]:
    """Ask the LLM to emit DOT source for the given prompt. Returns
    DOT text on success, None on any failure."""
    import httpx
    payload = {
        "model": model,
        "max_tokens": 1500,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": GRAPHVIZ_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload, headers=headers,
            )
        if r.status_code != 200:
            return None
        content = (
            r.json()["choices"][0]["message"]["content"] or ""
        )
    except Exception:
        return None
    return extract_dot_from_response(content)


async def generate_graphviz_svg(
    user_prompt: str,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
) -> tuple[str, str] | None:
    """End-to-end: prompt → LLM-emitted DOT → Graphviz SVG.

    Returns (svg, dot) on success, None on any failure. Caller falls
    back to the LLM-SVG path on None.
    """
    if not is_graphviz_binary_available():
        return None
    dot = await llm_emit_dot(
        user_prompt, api_key=api_key, base_url=base_url, model=model,
    )
    if not dot:
        return None
    engine = suggest_engine(user_prompt)
    svg = render_graphviz(dot, engine=engine)
    if not svg:
        return None
    return svg, dot


# --------------------------------------------------------------------
# Narration for the Graphviz route
# --------------------------------------------------------------------
#
# Graphviz emits a stable `id="nodeN"` / `id="edgeN"` on every node and
# edge group, and the viewer's highlighter matches `[id="..."]`.  So a
# narration script that references those ids gives graph-shaped figures
# (DFAs, Turing machines, trees, commutative diagrams) the same
# phrase-synced highlighting the LLM-SVG and template paths already
# have.  Without this, every Graphviz figure rendered silently with
# nothing flashing while the audio played.

_NARRATE_GRAPHVIZ_SYSTEM = """\
You are a math teacher narrating a graph-shaped diagram (state \
machine, automaton, Turing machine, tree, dependency graph, \
commutative diagram, etc.) to a student.

You are given the diagram request and the list of its elements, each \
with an id (nodeN / edgeN) and a label.  Write a spoken walkthrough.

Rules:
  1. Write 5 to 9 phrases.  Each phrase is 1-2 short sentences.
  2. SPOKEN WORDS ONLY — no symbols, no markdown, no LaTeX.  Read \
labels naturally (say "state q one", "the edge labelled a", etc.).
  3. Every phrase MUST include a "highlight" array of 1-3 element ids \
copied VERBATIM from the provided list.  Never invent an id.
  4. Phrase 1: what the whole diagram represents.
  5. Middle phrases: walk through the key nodes and the transitions / \
edges between them in a logical reading order.
  6. Final phrase: the takeaway / what the student should remember.
  7. Highlight the element you are actually talking about in each \
phrase.

Respond with ONLY a JSON object, no prose:
  {"phrases": [{"speak": "...", "highlight": ["node1"]}, ...]}
"""


def _parse_graphviz_elements(svg: str) -> list[tuple[str, str, str]]:
    """Pull (id, kind, label) for every Graphviz node/edge group."""
    import html
    elems: list[tuple[str, str, str]] = []
    for m in re.finditer(
        r'<g id="(node\d+)" class="node">\s*<title>(.*?)</title>',
        svg, re.S,
    ):
        elems.append(("node", m.group(1),
                       html.unescape(m.group(2)).strip()))
    for m in re.finditer(
        r'<g id="(edge\d+)" class="edge">\s*<title>(.*?)</title>',
        svg, re.S,
    ):
        elems.append(("edge", m.group(1),
                      html.unescape(m.group(2)).strip()))
    # node ids first, then edges — return as (id, kind, label).
    return [(eid, kind, label) for kind, eid, label in elems]


async def narrate_graphviz(
    user_prompt: str,
    svg: str,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    timeout_s: float = 25.0,
) -> list[dict]:
    """Produce a phrase-timed narration script for a Graphviz SVG.

    Each phrase highlights real `nodeN` / `edgeN` ids so the viewer
    spotlights the element under discussion. Returns [] on any failure
    (the figure still renders, just without narration)."""
    import json

    import httpx

    elems = _parse_graphviz_elements(svg)
    if not elems:
        return []
    valid_ids = {eid for eid, _, _ in elems}
    listing = "\n".join(
        f'  {eid} — {kind}' + (f' labelled "{label}"' if label else "")
        for eid, kind, label in elems
    )
    user = (
        f"Diagram request: {user_prompt}\n\n"
        f"The rendered diagram contains these elements. Use the id "
        f"values (left column) verbatim in every highlight array:\n"
        f"{listing}\n\nWrite the spoken walkthrough now."
    )
    payload = {
        "model": model,
        "max_tokens": 1100,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _NARRATE_GRAPHVIZ_SYSTEM},
            {"role": "user", "content": user},
        ],
    }
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload, headers=headers,
            )
        if r.status_code != 200:
            return []
        content = r.json()["choices"][0]["message"]["content"] or ""
        data = json.loads(content)
    except Exception:  # noqa: BLE001
        return []
    phrases = data.get("phrases") if isinstance(data, dict) else None
    if not isinstance(phrases, list):
        return []
    out: list[dict] = []
    for ph in phrases:
        if not isinstance(ph, dict):
            continue
        speak = (ph.get("speak") or "").strip()
        if not speak:
            continue
        h = ph.get("highlight") or []
        if isinstance(h, str):
            h = [h]
        h = [x for x in h if isinstance(x, str) and x in valid_ids]
        out.append({"speak": speak, "highlight": h})
    return out
