"""Seed the Phase-2 taxonomy from the existing routes/templates.

Each renderer template is registered under a category with a handful of
example prompts; its embedding is the centroid of those examples, and the
category centroid is the mean of its templates.  This bootstraps category
recognition; exemplar templates (open-ended classes like NP-completeness
proofs) are filled in later by the Phase-3 curation loop from the corpus.

Run as a script (needs an embedding key + DB):
    OPENAI_API_KEY=... SEVIM_DB_URL=... python -m studio.taxonomy_seed
or call ``seed(tel, embed_fn)`` from a test with a stub embedder.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Callable

# category_id -> (title, [(template_id, renderer_name, [example prompts])])
SEED: dict[str, tuple] = {
    "linear_algebra": ("Linear algebra", [
        ("matrix_multiplication", "matrix_multiplication",
         ["multiply matrices A and B", "show A times B", "compute the product of two matrices"]),
        ("matrix_transpose", "matrix_transpose",
         ["transpose of a matrix", "show A^T", "find the transpose"]),
        ("matrix_determinant", "matrix_determinant",
         ["determinant of a matrix", "compute det(A)", "find the determinant of a 3x3 matrix"]),
        ("matrix_inverse", "matrix_inverse",
         ["inverse of a matrix", "compute A inverse", "find the matrix inverse"]),
        ("system_of_equations", "system_of_equations",
         ["solve a system of linear equations", "solve the linear system", "Gaussian elimination of a system"]),
    ]),
    "geometry": ("Geometry", [
        ("pythagoras", "pythagoras",
         ["Pythagorean theorem", "right triangle 3 4 5", "prove a^2 + b^2 = c^2"]),
        ("triangle", "triangle",
         ["draw a triangle with given sides", "triangle with sides 5 6 7", "label a triangle's angles"]),
        ("number_line", "number_line",
         ["number line addition", "show 3 + 4 on a number line", "subtract on a number line"]),
    ]),
    "trigonometry": ("Trigonometry", [
        ("unit_circle", "unit_circle",
         ["unit circle", "show sine and cosine on the unit circle", "angles on the unit circle"]),
    ]),
    "set_theory": ("Set theory & logic", [
        ("venn_diagram", "venn_diagram",
         ["Venn diagram", "draw a Venn diagram of A and B", "intersection and union of sets"]),
    ]),
    "elementary": ("Elementary arithmetic", [
        ("place_value", "place_value",
         ["place value of a number", "break 3742 into place values", "hundreds tens ones"]),
        ("multiplication_array", "multiplication_array",
         ["multiplication as an array", "show 4 times 6 as dots", "array model of multiplication"]),
        ("fraction", "fraction",
         ["show a fraction", "draw 3/4 as a bar", "fraction as a pie"]),
    ]),
    "calculus": ("Calculus", [
        ("symbolic", "symbolic",
         ["compute the derivative of a function", "integrate a function", "find critical points"]),
        ("newton_method", "newton_method",
         ["Newton's method", "Newton-Raphson root finding", "iterate Newton's method"]),
        ("volume_solid", "volume_of_sphere",
         ["volume of a sphere", "volume of a cone", "disk method volume"]),
        ("function_plot", "matplotlib",
         ["plot a function", "graph y = x^2", "draw the curve of f(x)"]),
    ]),
    "graph_theory": ("Graph theory & automata", [
        ("graph_diagram", "graphviz",
         ["draw a DFA", "state machine diagram", "directed acyclic graph of tasks", "binary tree", "Hasse diagram"]),
        ("adjacency_matrix", "adjacency_matrix",
         ["adjacency matrix of a graph", "graph as an adjacency matrix"]),
    ]),
    "algorithms": ("Algorithms", [
        ("algorithm_trace", "algorithm_trace",
         ["insertion sort step by step", "trace bubble sort", "binary search steps", "long division steps"]),
    ]),
    "data": ("Data & tables", [
        ("data_table", "data_table",
         ["make a data table", "tabulate these values", "comparison table"]),
    ]),
    # Phase-4 renderer-first: this class now has a deterministic renderer
    # (studio/templates/np_completeness.py), so it's a 'renderer' template.
    "complexity_proofs": ("NP-completeness proofs", [
        ("np_complete_reduction", "np_completeness",
         ["prove vertex cover is NP-complete", "prove 3-SAT is NP-complete",
          "show the partition problem is NP-complete", "NP-completeness reduction"]),
    ]),
}


def seed(tel, embed_fn: Callable[[str], list | None]) -> dict:
    """Populate categories/templates/template_examples.  ``embed_fn`` maps
    a prompt to a vector (or None).  Returns a small summary dict."""
    import numpy as np
    n_cat = n_tpl = n_ex = 0
    model = os.environ.get("SEVIM_EMBED_MODEL", "text-embedding-3-small")
    for category_id, (title, templates) in SEED.items():
        tpl_centroids = []
        for template_id, renderer_name, prompts in templates:
            vecs = []
            for p in prompts:
                v = embed_fn(p)
                if v is None:
                    continue
                vecs.append(v)
                tel.add_template_example(template_id, p, json.dumps(v))
                n_ex += 1
            if not vecs:
                continue
            centroid = np.asarray(vecs, dtype="float64").mean(axis=0).tolist()
            kind = "renderer" if renderer_name else "exemplar"
            tel.upsert_template(
                template_id, category_id, kind,
                renderer_name=renderer_name,
                embedding_json=json.dumps(centroid),
                golden_prompt=prompts[0])
            tpl_centroids.append(centroid)
            n_tpl += 1
        cat_centroid = (np.asarray(tpl_centroids, dtype="float64")
                        .mean(axis=0).tolist() if tpl_centroids else None)
        tel.upsert_category(
            category_id, title,
            centroid_json=json.dumps(cat_centroid) if cat_centroid else None)
        n_cat += 1
    return {"categories": n_cat, "templates": n_tpl, "examples": n_ex,
            "embed_model": model}


def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from sevim import embeddings as emb
    from sevim.telemetry import get_telemetry
    if not emb.available():
        print("No embedding API key.", file=sys.stderr)
        return 2
    tel = get_telemetry()
    if tel is None:
        print("Telemetry not configured.", file=sys.stderr)
        return 2
    summary = seed(tel, emb.embed)
    print(f"taxonomy seeded: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
