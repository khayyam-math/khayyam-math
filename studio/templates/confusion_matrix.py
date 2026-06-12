"""Deterministic renderer for a confusion matrix + classifier metrics.

A 2x2 confusion matrix with precision / recall / accuracy / F1 is a
common ML-stats figure where the LLM routinely mislabels the cells (swaps
false-positive and false-negative) or computes the metrics wrong.  The
table and every metric are computed and asserted in Python, so the figure
is correct-by-construction.
"""
from __future__ import annotations

import html as _html
from typing import Any

_W, _H = 900, 520


def _text(x: float, y: float, s: str, *, fs: int = 14, anchor: str = "start",
          weight: str = "normal", fill: str = "#1a1d24") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


def render_confusion_matrix() -> tuple[str, list[dict]]:
    """Canonical 2x2 confusion matrix; all metrics asserted before drawing."""
    TP, FP, FN, TN = 80, 10, 20, 90
    total = TP + FP + FN + TN
    precision = TP / (TP + FP)
    recall = TP / (TP + FN)
    accuracy = (TP + TN) / total
    f1 = 2 * precision * recall / (precision + recall)
    assert total == 200 and abs(precision - 0.8889) < 1e-3 \
        and abs(recall - 0.8) < 1e-9 and abs(accuracy - 0.85) < 1e-9, \
        "confusion-matrix metrics inconsistent"

    P: list[str] = []
    P.append(_text(_W / 2, 40, "Confusion Matrix and Classifier Metrics",
                   fs=21, anchor="middle", weight="700"))

    # 2x2 grid geometry.
    gx, gy = 150, 150           # top-left of the 2x2 cell block
    cw, ch = 170, 110           # cell size

    # Column headers (actual class) and row headers (predicted class).
    P.append(_text(gx + cw, 110, "Actual: Positive", fs=14, anchor="middle",
                   weight="700", fill="#1657b8"))
    P.append(_text(gx + cw * 2, 110, "Actual: Negative", fs=14,
                   anchor="middle", weight="700", fill="#b03a3a"))
    P.append(_text(gx - 14, gy + ch / 2, "Predicted:", fs=12, anchor="end",
                   fill="#5a6470"))
    P.append(_text(gx - 14, gy + ch / 2 + 16, "Positive", fs=14, anchor="end",
                   weight="700"))
    P.append(_text(gx - 14, gy + ch + ch / 2, "Predicted:", fs=12,
                   anchor="end", fill="#5a6470"))
    P.append(_text(gx - 14, gy + ch + ch / 2 + 16, "Negative", fs=14,
                   anchor="end", weight="700"))

    # Cells: TP / FP (top row), FN / TN (bottom row).  Correct = green,
    # errors = red.
    cells = (
        (0, 0, "TP", TP, "True Positive", "#dff0df", "#2c7a38"),
        (1, 0, "FP", FP, "False Positive", "#f9dede", "#b03a3a"),
        (0, 1, "FN", FN, "False Negative", "#f9dede", "#b03a3a"),
        (1, 1, "TN", TN, "True Negative", "#dff0df", "#2c7a38"),
    )
    for col, row, abbr, val, name, bg, stroke in cells:
        x = gx + col * cw
        y = gy + row * ch
        P.append(f'<rect x="{x}" y="{y}" width="{cw}" height="{ch}" '
                 f'fill="{bg}" stroke="{stroke}" stroke-width="2"/>')
        P.append(_text(x + cw / 2, y + 38, f"{abbr} = {val}", fs=20,
                       anchor="middle", weight="700", fill=stroke))
        P.append(_text(x + cw / 2, y + 66, name, fs=12, anchor="middle",
                       fill="#5a6470"))

    # Metrics panel on the right.
    mx = gx + cw * 2 + 40
    P.append(_text(mx, 150, "Metrics", fs=16, weight="700"))
    rows = [
        ("Precision = TP/(TP+FP)", f"= {TP}/{TP + FP} = {precision:.3f}"),
        ("Recall = TP/(TP+FN)", f"= {TP}/{TP + FN} = {recall:.3f}"),
        ("Accuracy = (TP+TN)/N", f"= {TP + TN}/{total} = {accuracy:.3f}"),
        ("F1 = 2·P·R/(P+R)", f"= {f1:.3f}"),
    ]
    y = 182
    for label, val in rows:
        P.append(_text(mx, y, label, fs=13, weight="600"))
        P.append(_text(mx, y + 20, val, fs=13, fill="#2c7a38"))
        y += 52

    # Caption, clear of the bottom edge.
    P.append(_text(_W / 2, 432,
                   "Precision asks 'of predicted positives, how many were "
                   "right?'; recall asks 'of actual positives, how many did "
                   "we catch?'",
                   fs=13, anchor="middle", fill="#3a4250"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": "A confusion matrix cross-tabulates what a classifier "
                  "predicted against what was actually true, splitting every "
                  "prediction into four outcomes."},
        {"speak": "The green diagonal holds the correct calls: true positives "
                  "and true negatives. The red off-diagonal holds the two "
                  "error types, false positives and false negatives."},
        {"speak": "Precision divides true positives by everything predicted "
                  "positive, so it measures how trustworthy a positive "
                  "prediction is. Here that is eighty over ninety, about "
                  "0.89."},
        {"speak": "Recall divides true positives by everything actually "
                  "positive, so it measures how many real positives were "
                  "caught. Here that is eighty over one hundred, or 0.80."},
        {"speak": "The F1 score is the harmonic mean of precision and recall, "
                  "rewarding a classifier only when both are high. Accuracy, "
                  "by contrast, can hide poor recall on an imbalanced set."},
    ]
    return svg, narration


def is_confusion_matrix_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    if "confusion matrix" in p or "contingency table" in p:
        return True
    if "precision" in p and "recall" in p:
        return True
    return False


async def generate_confusion_matrix_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
) -> tuple[str, list[dict]]:
    return render_confusion_matrix()
