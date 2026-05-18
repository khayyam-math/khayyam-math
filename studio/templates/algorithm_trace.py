"""Deterministic algorithm-trace route.

"Show <algorithm> step by step" was the worst vision-judge category
(~5/10): the sequential route decomposed the prompt and let the LLM
redraw each step independently, so the numbers drifted between steps
and the per-step figures were wrong or irrelevant.

This route computes EVERY intermediate state in Python and renders it
deterministically as a vertical stack of grids.  Arithmetic
correctness and cross-step consistency are guaranteed by construction;
the LLM is used ONLY to extract the input data from the prompt.

Public API:
    is_algorithm_trace_prompt(prompt) -> bool
    generate_algorithm_trace_svg(prompt, *, api_key, base_url, model)
        -> (svg, narration) | None
"""
from __future__ import annotations

import json
from fractions import Fraction
from typing import Optional

_ALWAYS = ("insertion sort", "bubble sort", "selection sort",
           "binary search", "long division")
_STEP_GATED = ("gaussian elimination", "row reduction", "row echelon",
               "determinant", "linear system", "system of equations",
               "solve the system")
_STEP_CUES = ("step by step", "step-by-step", "steps of", "trace",
              "walk through", "each step", "show the steps")


def is_algorithm_trace_prompt(prompt: str) -> bool:
    p = f" {(prompt or '').lower()} "
    if any(k in p for k in _ALWAYS):
        return True
    if any(c in p for c in _STEP_CUES) and any(k in p for k in _STEP_GATED):
        return True
    return False


ALGO_SYSTEM = """\
You extract the input data for an algorithm-trace figure.  Return
ONLY a JSON object:

  {"algo": "<insertion_sort|bubble_sort|selection_sort|binary_search|
             gaussian_elimination|determinant|long_division>",
   "array": [int, ...],
   "target": <int>,
   "matrix": [[num,...], ...],
   "rhs": [num, ...],
   "dividend": <int>, "divisor": <int>}

Rules:
  1. "algo" is the algorithm named in the prompt.
  2. sorts + binary_search use "array"; binary_search also needs
     "target".  gaussian_elimination + determinant use "matrix";
     gaussian_elimination also needs "rhs" (the right-hand side).
     long_division uses "dividend" and "divisor".
  3. If the prompt gives explicit numbers, use them exactly.
     Otherwise invent small friendly textbook numbers:
       - array: 6 to 8 single/double-digit positive integers,
         UNSORTED for sorts, SORTED ASCENDING for binary_search.
       - target: for binary_search, a value that IS in the array.
       - matrix: a 3x3 integer matrix with a non-zero determinant.
       - rhs: 3 small integers.
       - dividend: a 3-4 digit positive integer; divisor: a small
         integer from 2 to 12.
  4. Include only the fields the chosen algo needs.

Respond with ONLY the JSON object.
"""


async def llm_algo_spec(
    user_prompt: str, *, api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini", timeout_s: float = 20.0,
) -> Optional[dict]:
    import httpx
    payload = {
        "model": model, "max_tokens": 400, "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": ALGO_SYSTEM},
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
                json=payload, headers=headers)
        if r.status_code != 200:
            return None
        spec = json.loads(
            r.json()["choices"][0]["message"]["content"] or "")
    except Exception:  # noqa: BLE001
        return None
    return spec if isinstance(spec, dict) else None


# ── number formatting ────────────────────────────────────────────────
def _fmt(v) -> str:
    if isinstance(v, Fraction):
        return (str(v.numerator) if v.denominator == 1
                else f"{v.numerator}/{v.denominator}")
    if isinstance(v, float):
        return (str(int(round(v))) if abs(v - round(v)) < 1e-9
                else f"{v:.2f}")
    return str(v)


# ── tracers: each returns list of step dicts ─────────────────────────
# step = {"label", "kind": "array"|"matrix", "data", "hi", "caption"}

def _arr_step(label, arr, hi, caption):
    return {"label": label, "kind": "array", "data": list(arr),
            "hi": dict(hi), "caption": caption}


def _mat_step(label, mat, hi, caption):
    return {"label": label, "kind": "matrix",
            "data": [list(r) for r in mat], "hi": set(hi),
            "caption": caption}


_DONE = "#d6ebd6"
_ACTIVE = "#ffe39e"
_CMP = "#bcd6f0"
_DROP = "#e8e8e8"


def _trace_insertion_sort(a: list[int]) -> list[dict]:
    a = list(a)
    steps = [_arr_step("Initial list", a, {},
                       "The unsorted input list.")]
    for i in range(1, len(a)):
        key = a[i]
        j = i - 1
        while j >= 0 and a[j] > key:
            a[j + 1] = a[j]
            j -= 1
        a[j + 1] = key
        hi = {k: _DONE for k in range(i + 1)}
        hi[j + 1] = _ACTIVE
        steps.append(_arr_step(
            f"Insert element {i}", a, hi,
            f"Insert {key} into the sorted prefix; "
            f"positions 0 to {i} are now sorted."))
    steps.append(_arr_step("Sorted", a,
                           {k: _DONE for k in range(len(a))},
                           "The list is fully sorted."))
    return steps


def _trace_bubble_sort(a: list[int]) -> list[dict]:
    a = list(a)
    n = len(a)
    steps = [_arr_step("Initial list", a, {}, "The unsorted input list.")]
    for p in range(n - 1):
        for k in range(n - 1 - p):
            if a[k] > a[k + 1]:
                a[k], a[k + 1] = a[k + 1], a[k]
        hi = {k: _DONE for k in range(n - 1 - p, n)}
        steps.append(_arr_step(
            f"After pass {p + 1}", a, hi,
            f"Pass {p + 1} bubbles the largest remaining value to "
            f"position {n - 1 - p}."))
        if all(a[k] <= a[k + 1] for k in range(n - 1)):
            break
    steps.append(_arr_step("Sorted", a,
                           {k: _DONE for k in range(n)},
                           "The list is fully sorted."))
    return steps


def _trace_selection_sort(a: list[int]) -> list[dict]:
    a = list(a)
    n = len(a)
    steps = [_arr_step("Initial list", a, {}, "The unsorted input list.")]
    for i in range(n - 1):
        m = min(range(i, n), key=lambda k: a[k])
        a[i], a[m] = a[m], a[i]
        hi = {k: _DONE for k in range(i + 1)}
        hi[i] = _ACTIVE
        steps.append(_arr_step(
            f"Selection {i + 1}", a, hi,
            f"Select the smallest of the unsorted tail and place it "
            f"at position {i}."))
    steps.append(_arr_step("Sorted", a,
                           {k: _DONE for k in range(n)},
                           "The list is fully sorted."))
    return steps


def _trace_binary_search(a: list[int], target: int) -> list[dict]:
    a = sorted(a)
    lo, hi = 0, len(a) - 1
    steps = [_arr_step(
        "Sorted array", a, {k: _CMP for k in range(len(a))},
        f"Search for {target} in the sorted array.")]
    n_probe = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        n_probe += 1
        col = {k: _DROP for k in range(len(a))}
        for k in range(lo, hi + 1):
            col[k] = _CMP
        col[mid] = _ACTIVE
        v = a[mid]
        if v == target:
            steps.append(_arr_step(
                f"Probe {n_probe}", a, col,
                f"Midpoint index {mid} holds {v}, which equals the "
                f"target. Found it."))
            return steps
        direction = "right" if v < target else "left"
        steps.append(_arr_step(
            f"Probe {n_probe}", a, col,
            f"Midpoint index {mid} holds {v}; the target {target} is "
            f"{'larger' if v < target else 'smaller'}, so search the "
            f"{direction} half."))
        if v < target:
            lo = mid + 1
        else:
            hi = mid - 1
    steps.append(_arr_step("Not found", a,
                           {k: _DROP for k in range(len(a))},
                           f"The target {target} is not in the array."))
    return steps


def _trace_gaussian(matrix, rhs) -> list[dict]:
    n = len(matrix)
    # augmented matrix of Fractions
    M = [[Fraction(matrix[r][c]) for c in range(n)] + [Fraction(rhs[r])]
         for r in range(n)]
    steps = [_mat_step("Augmented matrix", M, set(),
                       "Write the system as an augmented matrix.")]
    for k in range(n):
        if M[k][k] == 0:
            for r in range(k + 1, n):
                if M[r][k] != 0:
                    M[k], M[r] = M[r], M[k]
                    break
        if M[k][k] == 0:
            continue
        for r in range(k + 1, n):
            if M[r][k] != 0:
                f = M[r][k] / M[k][k]
                for c in range(n + 1):
                    M[r][c] -= f * M[k][c]
        if k == n - 1:
            continue  # last pivot has no rows below it
        hi = {(r, k) for r in range(k + 1, n)}
        hi.add((k, k))
        steps.append(_mat_step(
            f"Eliminate column {k + 1}", M, hi,
            f"Use the pivot in row {k + 1} to zero out the entries "
            f"below it in column {k + 1}."))
    # back substitution
    x = [Fraction(0)] * n
    for k in range(n - 1, -1, -1):
        s = M[k][n] - sum(M[k][c] * x[c] for c in range(k + 1, n))
        x[k] = s / M[k][k] if M[k][k] != 0 else Fraction(0)
    sol = ", ".join(f"x{i + 1} = {_fmt(x[i])}" for i in range(n))
    steps.append(_mat_step(
        "Back-substitute", M, {(r, r) for r in range(n)},
        f"The matrix is upper-triangular; solving from the bottom "
        f"row up gives {sol}."))
    return steps


def _trace_determinant(matrix) -> list[dict]:
    n = len(matrix)
    M = [[int(matrix[r][c]) for c in range(n)] for r in range(n)]
    steps = [_mat_step("The matrix", M, set(),
                       "Compute the determinant by cofactor expansion "
                       "along the first row.")]
    if n == 2:
        d = M[0][0] * M[1][1] - M[0][1] * M[1][0]
        steps.append(_mat_step(
            "Determinant", M, {(0, 0), (1, 1), (0, 1), (1, 0)},
            f"det = {M[0][0]}*{M[1][1]} - {M[0][1]}*{M[1][0]} = {d}."))
        return steps
    total = 0
    for j in range(n):
        minor = [[M[r][c] for c in range(n) if c != j]
                 for r in range(1, n)]
        md = minor[0][0] * minor[1][1] - minor[0][1] * minor[1][0]
        sign = 1 if j % 2 == 0 else -1
        term = sign * M[0][j] * md
        total += term
        steps.append(_mat_step(
            f"Minor M1{j + 1}", minor, {(0, 0), (1, 1), (0, 1), (1, 0)},
            f"Delete row 1 and column {j + 1}. "
            f"Its determinant is {minor[0][0]}*{minor[1][1]} - "
            f"{minor[0][1]}*{minor[1][0]} = {md}. "
            f"Term: {'+' if sign > 0 else '-'} {M[0][j]}*{md} = {term}."))
    steps.append(_mat_step(
        "Result", M, set(),
        f"Sum the three cofactor terms: det(A) = {total}."))
    return steps


# ── deterministic renderer ───────────────────────────────────────────
def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _wrap(text: str, width: int = 96) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def _render_array(arr, hi, cx, top) -> tuple[list[str], float]:
    box, gap = 52.0, 8.0
    w = len(arr) * (box + gap) - gap
    x0 = cx - w / 2
    out: list[str] = []
    for i, v in enumerate(arr):
        bx = x0 + i * (box + gap)
        fill = hi.get(i, "#ffffff")
        out.append(f'<rect x="{bx:.1f}" y="{top:.1f}" width="{box}" '
                   f'height="{box}" fill="{fill}" stroke="#333" '
                   f'stroke-width="1.5"/>')
        out.append(f'<text x="{bx + box / 2:.1f}" y="{top + box / 2 + 7:.1f}" '
                   f'font-size="22" text-anchor="middle" '
                   f'font-family="serif">{_esc(v)}</text>')
        out.append(f'<text x="{bx + box / 2:.1f}" y="{top + box + 16:.1f}" '
                   f'font-size="12" text-anchor="middle" fill="#999">'
                   f'{i}</text>')
    return out, box + 22


def _render_matrix(mat, hi, cx, top) -> tuple[list[str], float]:
    cell = 54.0
    rows, cols = len(mat), len(mat[0]) if mat else 0
    w = cols * cell
    x0 = cx - w / 2
    out: list[str] = []
    for r in range(rows):
        for c in range(cols):
            bx, by = x0 + c * cell, top + r * cell
            fill = _ACTIVE if (r, c) in hi else "#ffffff"
            out.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{cell}" '
                       f'height="{cell}" fill="{fill}" stroke="#333" '
                       f'stroke-width="1.3"/>')
            out.append(
                f'<text x="{bx + cell / 2:.1f}" y="{by + cell / 2 + 6:.1f}" '
                f'font-size="19" text-anchor="middle" '
                f'font-family="serif">{_esc(_fmt(mat[r][c]))}</text>')
    return out, rows * cell


def _render(title: str, steps: list[dict]) -> tuple[str, list]:
    W = 900.0
    margin, hdr_h, gap = 26.0, 32.0, 16.0
    pad_top, cap_lh, pad_bot = 20.0, 19.0, 18.0
    cx = W / 2

    body: list[str] = []
    narration: list = [{
        "speak": f"Let's trace {title.lower()}, one step at a time.",
        "highlight": ["title"]}]
    y = 66.0
    for i, st in enumerate(steps):
        cap_lines = _wrap(st["caption"])
        if st["kind"] == "array":
            content_h = 52.0 + 22.0
        else:
            content_h = len(st["data"]) * 54.0
        cell_h = (hdr_h + pad_top + content_h + 12.0
                  + cap_lh * len(cap_lines) + pad_bot)
        body.append(f'<rect x="{margin}" y="{y:.1f}" '
                    f'width="{W - 2 * margin}" height="{hdr_h}" '
                    f'fill="#eef2f8" stroke="#bbb" stroke-width="1"/>')
        body.append(f'<text id="step_{i}" x="{margin + 12}" '
                    f'y="{y + 21:.1f}" font-size="16" font-family="serif" '
                    f'font-weight="bold" fill="#1a3a5c">'
                    f'Step {i + 1}: {_esc(st["label"])}</text>')
        body.append(f'<rect x="{margin}" y="{y + hdr_h:.1f}" '
                    f'width="{W - 2 * margin}" '
                    f'height="{cell_h - hdr_h:.1f}" fill="none" '
                    f'stroke="#bbb" stroke-width="1"/>')
        ctop = y + hdr_h + pad_top
        if st["kind"] == "array":
            frag, used = _render_array(st["data"], st["hi"], cx, ctop)
        else:
            frag, used = _render_matrix(st["data"], st["hi"], cx, ctop)
        body.extend(frag)
        capy = ctop + used + 12.0 + cap_lh
        for ln in cap_lines:
            body.append(f'<text x="{cx:.1f}" y="{capy:.1f}" '
                        f'font-size="14" text-anchor="middle" '
                        f'fill="#333" font-family="serif">'
                        f'{_esc(ln)}</text>')
            capy += cap_lh
        narration.append({
            "speak": f"Step {i + 1}, {st['label']}. {st['caption']}",
            "highlight": [f"step_{i}"]})
        y += cell_h + gap

    H = y + margin
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">',
        f'<rect x="0" y="0" width="{W:.0f}" height="{H:.0f}" '
        f'fill="white"/>',
        f'<text id="title" x="{cx:.0f}" y="40" font-size="23" '
        f'text-anchor="middle" font-family="serif" font-weight="bold" '
        f'fill="#111">{_esc(title)}</text>',
    ]
    out.extend(body)
    out.append("</svg>")
    return "".join(out), narration


def _render_long_division(dividend: int, divisor: int
                          ) -> tuple[str, list]:
    """Classic long-division 'house': quotient on top, the divisor and
    dividend under the bracket, and the subtraction staircase below."""
    D = str(int(dividend))
    v = int(divisor)
    L = len(D)
    CW, LH = 46.0, 32.0
    X0 = 210.0
    cx = [X0 + j * CW + CW / 2 for j in range(L)]
    body: list[str] = []

    def put(x, y, ch, weight="normal", fill="#111", size=23):
        body.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
            f'text-anchor="middle" font-family="monospace" '
            f'fill="{fill}" font-weight="{weight}">{_esc(ch)}</text>')

    def put_num(s, end_col, y, **kw):
        for k, ch in enumerate(reversed(str(s))):
            j = end_col - k
            if 0 <= j < L:
                put(cx[j], y, ch, **kw)

    # quotient digits
    carry = 0
    q = ["" for _ in range(L)]
    start = None
    for j in range(L):
        cur = carry * 10 + int(D[j])
        qd = cur // v
        carry = cur - qd * v
        if qd > 0 and start is None:
            start = j
        if start is not None:
            q[j] = str(qd)
    if start is None:
        start = L - 1

    quot_y, bar_y = 58.0, 70.0
    div_y = bar_y + 32.0
    for j in range(L):
        if q[j]:
            put(cx[j], quot_y, q[j], weight="bold", fill="#1a3a5c")
    body.append(f'<line x1="{X0 - 8:.1f}" y1="{bar_y:.1f}" '
                f'x2="{X0 + L * CW:.1f}" y2="{bar_y:.1f}" '
                f'stroke="#333" stroke-width="2"/>')
    body.append(f'<path d="M {X0 - 8:.1f} {bar_y:.1f} '
                f'Q {X0 - 34:.1f} {(bar_y + div_y) / 2:.1f} '
                f'{X0 - 8:.1f} {div_y + 9:.1f}" fill="none" '
                f'stroke="#333" stroke-width="2"/>')
    put(X0 - 52, div_y, str(v), weight="bold", fill="#1a3a5c")
    for j in range(L):
        put(cx[j], div_y, D[j])

    carry = 0
    cur_y = div_y
    narration: list = [{
        "speak": f"Long division of {D} by {v}, digit by digit.",
        "highlight": ["title"]}]
    for j in range(L):
        cur = carry * 10 + int(D[j])
        qd = cur // v
        prod = qd * v
        rem = cur - prod
        if j >= start:
            prod_y = cur_y + LH
            put_num(prod, j, prod_y, fill="#b03030")
            plen = len(str(prod))
            sub_y = prod_y + 9
            body.append(
                f'<line x1="{cx[j - plen + 1] - CW / 2 + 5:.1f}" '
                f'y1="{sub_y:.1f}" x2="{cx[j] + CW / 2 - 5:.1f}" '
                f'y2="{sub_y:.1f}" stroke="#333" stroke-width="1.5"/>')
            nxt_y = sub_y + LH
            if j < L - 1:
                put_num(rem * 10 + int(D[j + 1]), j + 1, nxt_y)
            else:
                put_num(rem, j, nxt_y, weight="bold", fill="#1a7a1a")
            narration.append({
                "speak": (f"{cur} divided by {v} is {qd}; "
                          f"{qd} times {v} is {prod}, remainder {rem}."),
                "highlight": []})
            cur_y = nxt_y
        carry = rem

    quotient, remainder = divmod(int(D), v)
    W = X0 + L * CW + 70.0
    H = cur_y + 104.0
    cap = (f"{D} divided by {v} equals {quotient}"
           + (f" remainder {remainder}." if remainder else
              " exactly."))
    body.append(f'<text x="{W / 2:.1f}" y="{cur_y + 56:.1f}" '
                f'font-size="16" text-anchor="middle" '
                f'font-family="serif" fill="#333">{_esc(cap)}</text>')
    narration.append({"speak": cap, "highlight": []})
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">',
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="white"/>',
        f'<text id="title" x="{W / 2:.1f}" y="32" font-size="20" '
        f'text-anchor="middle" font-family="serif" font-weight="bold" '
        f'fill="#111">Long Division: {D} ÷ {v}</text>',
    ]
    out.extend(body)
    out.append("</svg>")
    return "".join(out), narration


_TITLES = {
    "insertion_sort": "Insertion Sort",
    "bubble_sort": "Bubble Sort",
    "selection_sort": "Selection Sort",
    "binary_search": "Binary Search",
    "gaussian_elimination": "Gaussian Elimination",
    "determinant": "Determinant by Cofactor Expansion",
}


async def generate_algorithm_trace_svg(
    user_prompt: str, *, api_key: str, base_url: str, model: str,
) -> Optional[tuple[str, list]]:
    spec = await llm_algo_spec(user_prompt, api_key=api_key,
                               base_url=base_url, model=model)
    if not spec:
        return None
    algo = str(spec.get("algo") or "").strip().lower()
    try:
        if algo in ("insertion_sort", "bubble_sort", "selection_sort"):
            arr = [int(v) for v in (spec.get("array") or [])]
            if not (3 <= len(arr) <= 16):
                return None
            steps = {
                "insertion_sort": _trace_insertion_sort,
                "bubble_sort": _trace_bubble_sort,
                "selection_sort": _trace_selection_sort,
            }[algo](arr)
        elif algo == "binary_search":
            arr = [int(v) for v in (spec.get("array") or [])]
            if not (3 <= len(arr) <= 20):
                return None
            tgt = int(spec.get("target", arr[len(arr) // 2]))
            steps = _trace_binary_search(arr, tgt)
        elif algo == "gaussian_elimination":
            mat = spec.get("matrix") or []
            rhs = spec.get("rhs") or []
            n = len(mat)
            if not (2 <= n <= 5) or any(len(r) != n for r in mat) \
                    or len(rhs) != n:
                return None
            steps = _trace_gaussian(mat, rhs)
        elif algo == "determinant":
            mat = spec.get("matrix") or []
            n = len(mat)
            if n not in (2, 3) or any(len(r) != n for r in mat):
                return None
            steps = _trace_determinant(mat)
        elif algo == "long_division":
            dividend = int(spec.get("dividend", 0))
            divisor = int(spec.get("divisor", 0))
            if divisor < 2 or not (1 <= dividend <= 10 ** 9):
                return None
            return _render_long_division(dividend, divisor)
        else:
            return None
    except (TypeError, ValueError, ZeroDivisionError, KeyError):
        return None
    if len(steps) < 2:
        return None
    return _render(_TITLES.get(algo, "Algorithm Trace"), steps)
