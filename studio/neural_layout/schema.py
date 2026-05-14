"""Canonical dataclasses for the neural-layout training corpus.

The training task is **layout correction**: given a broken scene graph
(parsed from a poorly-laid-out SVG) and the original prompt, predict a
fixed scene graph. We store both source and target as `SceneGraph`s,
not as raw SVG strings — see PLAN.md "Why raw SVG is not the best
first step".

Serialisation is JSONL with one `TrainingPair` per line. Records are
versioned via `SCHEMA_VERSION`; bump on any breaking field change so
old corpora can be detected and migrated rather than silently mis-read.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 1

# Closed vocabularies — keep small. Anything unknown maps to "other".
NODE_TYPES: tuple[str, ...] = (
    "rect", "circle", "ellipse", "line", "polyline", "polygon", "path",
    "text", "tspan", "g", "matrix-group", "axis", "axis-tick",
    "caption", "title-text", "highlight", "arrow", "image", "other",
)

EDGE_RELATIONS: tuple[str, ...] = (
    "parent_of", "sibling_of", "narration_co_anchor",
    "matrix_cell_of", "axis_tick_of", "label_of", "connects",
    "semantic_other",
)

VIEWPORT_KINDS: tuple[str, ...] = ("phone", "tablet", "desktop")

MATH_BUCKETS: tuple[str, ...] = (
    "geometry", "calculus", "linear_algebra", "set_theory_logic",
    "combinatorics", "probability", "real_analysis", "topology",
    "group_theory", "number_theory", "complexity", "proof",
    # Added after inspection of PROMPTS_V5: 7 new buckets to absorb
    # what was previously falling into "other".
    "differential_equations", "optimization", "signal_processing",
    "statistics_ml", "complex_analysis", "physics", "function_plot",
    "other",
)


@dataclass
class NodeFeatures:
    """One SVG element or rigid group.

    `bbox` is in raw SVG units (the same coord system as the viewBox).
    Quantisation to a 256-bin grid happens at *batching* time, not
    storage time — keeping floats here means a single corpus can feed
    multiple model variants with different quantisation strategies.
    """
    id: str
    type: str  # one of NODE_TYPES; "other" for anything else.
    bbox: tuple[float, float, float, float]  # x, y, w, h
    text: str = ""
    font_size: float = 0.0
    stroke_width: float = 0.0
    parent_id: str | None = None
    top_level_group_id: str | None = None
    is_narration_anchor: bool = False
    is_caption: bool = False
    is_protected: bool = False  # do-not-move (titles, axis labels, …)
    # raw attrs kept for round-tripping debugging only; not a model input.
    raw_attrs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NodeFeatures":
        return cls(
            id=d["id"],
            type=d["type"] if d["type"] in NODE_TYPES else "other",
            bbox=tuple(d["bbox"]),  # type: ignore[arg-type]
            text=d.get("text", ""),
            font_size=float(d.get("font_size", 0.0)),
            stroke_width=float(d.get("stroke_width", 0.0)),
            parent_id=d.get("parent_id"),
            top_level_group_id=d.get("top_level_group_id"),
            is_narration_anchor=bool(d.get("is_narration_anchor", False)),
            is_caption=bool(d.get("is_caption", False)),
            is_protected=bool(d.get("is_protected", False)),
            raw_attrs=dict(d.get("raw_attrs") or {}),
        )


@dataclass
class EdgeFeatures:
    """A directed relation between two nodes."""
    src_id: str
    dst_id: str
    relation: str  # one of EDGE_RELATIONS.

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EdgeFeatures":
        rel = d["relation"] if d["relation"] in EDGE_RELATIONS \
            else "semantic_other"
        return cls(src_id=d["src_id"], dst_id=d["dst_id"], relation=rel)


@dataclass
class SceneGraph:
    """The structured representation we train on."""
    nodes: list[NodeFeatures]
    edges: list[EdgeFeatures]
    viewbox: tuple[float, float, float, float]  # x0, y0, w, h
    canvas_w: int
    canvas_h: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "viewbox": list(self.viewbox),
            "canvas_w": self.canvas_w,
            "canvas_h": self.canvas_h,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SceneGraph":
        return cls(
            nodes=[NodeFeatures.from_dict(n) for n in d["nodes"]],
            edges=[EdgeFeatures.from_dict(e) for e in d["edges"]],
            viewbox=tuple(d["viewbox"]),  # type: ignore[arg-type]
            canvas_w=int(d["canvas_w"]),
            canvas_h=int(d["canvas_h"]),
        )


@dataclass
class TrainingPair:
    """One (broken → fixed) training example."""
    pair_id: str
    prompt: str
    source: SceneGraph  # broken layout
    target: SceneGraph  # fixed layout
    viewport_kind: str  # one of VIEWPORT_KINDS
    math_bucket: str    # one of MATH_BUCKETS
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pair_id": self.pair_id,
            "prompt": self.prompt,
            "viewport_kind": self.viewport_kind,
            "math_bucket": self.math_bucket,
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TrainingPair":
        sv = int(d.get("schema_version", 1))
        if sv != SCHEMA_VERSION:
            raise ValueError(
                f"TrainingPair schema_version={sv} "
                f"!= expected {SCHEMA_VERSION}; corpus needs migration"
            )
        vp = d["viewport_kind"]
        if vp not in VIEWPORT_KINDS:
            vp = "desktop"
        mb = d["math_bucket"]
        if mb not in MATH_BUCKETS:
            mb = "other"
        return cls(
            pair_id=d["pair_id"],
            prompt=d["prompt"],
            source=SceneGraph.from_dict(d["source"]),
            target=SceneGraph.from_dict(d["target"]),
            viewport_kind=vp,
            math_bucket=mb,
            metadata=dict(d.get("metadata") or {}),
            schema_version=sv,
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def write_jsonl(pairs: Iterable[TrainingPair], path: str) -> int:
    """Append-only write. Returns the number of pairs written."""
    n = 0
    with open(path, "a", encoding="utf-8") as fh:
        for pair in pairs:
            fh.write(pair.to_json() + "\n")
            n += 1
    return n


def read_jsonl(path: str) -> Iterator[TrainingPair]:
    """Streaming reader, one pair per line. Bad lines raise."""
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield TrainingPair.from_dict(json.loads(line))


_BUCKET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "geometry": (
        "triangle", "polygon", "circle", "angle", "euclid", "geometry",
        "perpendicular", "parallel line", "tangent", "inscribed",
        "circumscribed", "trapezoid", "rhombus", "hexagon", "pentagon",
        "ellipse", "parabola", "hyperbola", "law of cosines",
        "law of sines", "thales", "pythagorean", "centroid", "incircle",
        "circumcircle", "conic", "polar coord", "polar coordinates",
        "spiral", "locus", "midpoint", "bisector", "altitude",
        "median of a triangle", "cylinder", "sphere", "cone",
        "surface area", "volume of a", "perimeter",
    ),
    "calculus": (
        "derivative", "integral", "riemann", "taylor", "limit",
        "fundamental theorem", "calculus", "differentiat",
        "l'hopital", "l'hôpital", "concavity", "implicit",
        "volume of revolution", "chain rule", "secant slope",
        "tangent line", "asymptote",
    ),
    "linear_algebra": (
        "matrix", "matrices", "vector", "eigen", "determinant",
        "gaussian elimination", "svd", "singular value", "rank",
        "nullity", "gram-schmidt", "gram–schmidt", "basis",
        "linear transformation", "rotation matrix", "shear matrix",
        "scaling matrix", "dot product", "cross product",
        "orthogonal", "span", "subspace", "diagonaliz", "trace",
    ),
    "set_theory_logic": (
        "venn", "union", "intersection", "subset",
        "de morgan", "truth table", "implication", "boolean",
        "hasse", "power set", "cartesian product",
        "injective", "surjective", "bijective", "predicate",
        "quantifier",
    ),
    "combinatorics": (
        "pigeonhole", "combination", "permutation", "binomial",
        "pascal", "factorial", "stars and bars", "combinatori",
        "catalan", "stirling", "partitions",
        "graph colouring", "graph coloring", "graph color",
        "generating function",
    ),
    "probability": (
        "probability", "random walk", "bayes", "expectation",
        "variance", "stochastic", "monte carlo",
        "normal distribution", "binomial distribution",
        "law of large numbers",
    ),
    "statistics_ml": (
        "pmf", "pdf of", "cdf of", "roc", "auc",
        "precision-recall", "k-nn", "knn",
        "hypothesis test", "confidence interval",
        "poisson", "gaussian", "mcmc", "posterior",
        "regression", "classifier", "decision boundary",
        "svm", " test set", "rejection region",
        "significance level", "rejection sampling",
        "hypothesis", "p-value",
    ),
    "real_analysis": (
        "cauchy sequence", "epsilon-delta", "epsilon delta",
        "continuity", "uniform convergence", "compactness",
        "supremum", "infimum", "real analysis", "lebesgue",
        "measure theory",
    ),
    "topology": (
        "topology", "homeomorphism", "open set", "closed set",
        "metric space", "manifold", "homotopy", "fundamental group",
    ),
    "group_theory": (
        "subgroup", "cyclic group", "abelian", "homomorphism",
        "isomorphism", "permutation group", "lagrange's theorem",
        "galois", "cosets",
    ),
    "number_theory": (
        "prime", "modular arithmetic", "gcd", "lcm", "fermat",
        "euler totient", "chinese remainder", "number theory",
        "rsa", "diffie-hellman", "diffie hellman", "elliptic curve",
        "modular",
    ),
    "complexity": (
        "np-complete", "np-hard", "polynomial time", "turing machine",
        "complexity", "halting problem", "reduction to",
        "3sat", "3-sat", " sat ", "vertex cover", "clique", "tsp",
        "hamiltonian", "pumping lemma", "automaton", "automata",
        "dfa", "nfa", "regular language", "pushdown",
    ),
    "differential_equations": (
        "differential equation", "slope field", "solution curve",
        "dy/dx", "dy/dt", "ode ", "euler method",
        "newton's method", "lotka-volterra", "lotka volterra",
        "logistic growth", "rc circuit", "mass-spring",
        "mass spring", "oscillat", "decay constant",
    ),
    "optimization": (
        "gradient descent", "newton's method", "kkt",
        "convex optimization", "linear programming",
        "quadratic programming", "simplex", "lagrange multiplier",
        "polynomial regression", "step size", "proximal operator",
    ),
    "signal_processing": (
        "fourier series", "fourier transform", "nyquist",
        "convolv", "convolution", "sampling rate",
        "diffraction", "wavelet", "spectrum", "filter response",
    ),
    "complex_analysis": (
        "branch cut", "winding number", "complex plane",
        "ahlfors", "residue theorem", "analytic function",
        "contour integral", "log(z)", "e^(ix", "e^(it",
        "riemann sphere",
    ),
    "physics": (
        "electric field", "magnetic field", "ray diagram",
        "lens", "particle-in-a-box", "particle in a box",
        "wavefunction", "single-slit", "single slit",
        "circuit", "capacitor", "ohm",
    ),
    "function_plot": (
        "graph y =", "graph y=", "plot the function",
        "plot y =", "plot y=", "sketch y =", "sketch y=",
        "draw y =", "draw y=", "graph the function",
        "graph the line", "draw the line", "sketch the line",
        "graph of",
    ),
    "proof": (
        "prove", "proof", "lemma", "theorem", "corollary",
        "induction", "contradiction", "contrapositive",
    ),
}


def classify_math_bucket(prompt: str) -> str:
    """Best-effort keyword-based bucketing for stratified sampling.

    Order-of-precedence matters: more-specific buckets win over generic
    ones ("proof" must lose to "complexity"; "function_plot" must lose
    to the topical buckets so "graph y = sin x" routes to function_plot
    only if no calculus / probability keyword matched first).
    """
    p = (prompt or "").lower()
    priority = (
        # most-specific first
        "complexity", "differential_equations", "optimization",
        "signal_processing", "complex_analysis", "physics",
        "statistics_ml",
        # core math
        "linear_algebra", "calculus", "geometry",
        "set_theory_logic", "combinatorics", "probability",
        "topology", "group_theory", "number_theory",
        "real_analysis",
        # fallthrough generic last
        "function_plot", "proof",
    )
    for bucket in priority:
        for kw in _BUCKET_KEYWORDS[bucket]:
            if kw in p:
                return bucket
    return "other"
