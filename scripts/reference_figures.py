"""Curated list of trusted reference figures used to ground the
fine-tuning corpus.

Each entry pairs a (prompt, reference image URL, citation).  The
generator fetches the reference, shows it to gpt-4o (vision), and
asks for an SVG that visually matches the reference figure plus a
textbook-style narration that names the theorem and follows the
canonical proof / explanation.

Sources are chosen to be:
  * **Free / open-licensed** — Wikimedia Commons images (mostly
    CC-BY-SA), public-domain Elements illustrations, OpenStax open
    textbook figures.  No paywall scraping.
  * **Trusted / canonical** — figures that appear in mainstream
    textbook treatments of the topic.
  * **Visually clean** — diagrams that read well as SVG (lines,
    polygons, labels) rather than dense raster plots.

Adding more entries: pick a famous figure, find a Commons URL with
a stable filename, write a one-line prompt the user might type, add
a citation that gpt-4o can reference in the narration ("Elements I.47",
"Bayes' rule", etc.).
"""
from __future__ import annotations


REFERENCES: list[dict] = [
    # ── Geometry — Euclid ──────────────────────────────────────────
    {
        "prompt": "Draw the Euclidean construction proving the angle sum of a triangle is π (Elements I.32).",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c4/Sum_of_angles_of_triangle.svg/640px-Sum_of_angles_of_triangle.svg.png",
        "citation": "Euclid, Elements, Book I, Proposition 32",
        "domain": "geometry",
    },
    {
        "prompt": "Draw the figure for the Pythagorean theorem with squares on each side, in the style of Elements I.47.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4d/Pythagorean.svg/640px-Pythagorean.svg.png",
        "citation": "Euclid, Elements, Book I, Proposition 47",
        "domain": "geometry",
    },
    {
        "prompt": "Show Thales' theorem — the angle inscribed in a semicircle is a right angle.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8f/Inscribed_angle_theorem.svg/640px-Inscribed_angle_theorem.svg.png",
        "citation": "Euclid, Elements, Book III, Proposition 31",
        "domain": "geometry",
    },
    {
        "prompt": "Draw the inscribed-angle theorem: an inscribed angle is half the central angle subtending the same arc.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8f/Inscribed_angle_theorem.svg/640px-Inscribed_angle_theorem.svg.png",
        "citation": "Euclid, Elements, Book III, Proposition 20",
        "domain": "geometry",
    },
    {
        "prompt": "Draw a triangle with its three medians meeting at the centroid (2:1 division).",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a4/Triangle.Centroid.svg/640px-Triangle.Centroid.svg.png",
        "citation": "Standard centroid theorem (Coxeter, Geometry Revisited §1.4)",
        "domain": "geometry",
    },

    # ── Trigonometry ───────────────────────────────────────────────
    {
        "prompt": "Draw the unit circle with sin θ and cos θ labelled at θ = π/3, in the standard textbook style.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4c/Unit_circle_angles_color.svg/640px-Unit_circle_angles_color.svg.png",
        "citation": "Standard unit-circle figure, Stewart Calculus §A.6",
        "domain": "trigonometry",
    },
    {
        "prompt": "Show sin² θ + cos² θ = 1 as the Pythagorean identity on the unit circle.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9d/Sinus_und_Kosinus_am_Einheitskreis_1.svg/640px-Sinus_und_Kosinus_am_Einheitskreis_1.svg.png",
        "citation": "Pythagorean trigonometric identity",
        "domain": "trigonometry",
    },

    # ── Calculus ───────────────────────────────────────────────────
    {
        "prompt": "Draw a Riemann sum approximating ∫₀² x² dx with rectangles, in the Spivak / Apostol style.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/19/Riemann_sum_convergence.png/640px-Riemann_sum_convergence.png",
        "citation": "Riemann integral, Spivak Calculus Ch. 13",
        "domain": "calculus",
    },
    {
        "prompt": "Show the derivative as the slope of the tangent line — limit of secant slopes.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d2/Tangent-calculus.svg/640px-Tangent-calculus.svg.png",
        "citation": "Definition of the derivative, Spivak Calculus Ch. 9",
        "domain": "calculus",
    },
    {
        "prompt": "Draw the figure for the fundamental theorem of calculus (FTC) — area function and its derivative.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/cb/FTC_geometric.svg/640px-FTC_geometric.svg.png",
        "citation": "Fundamental theorem of calculus, Apostol Vol. I §3",
        "domain": "calculus",
    },
    {
        "prompt": "Show the geometric definition of e as continuous compounding (1 + 1/n)^n → e.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2e/Hyperbola_E.svg/640px-Hyperbola_E.svg.png",
        "citation": "Definition of e, area under 1/x",
        "domain": "calculus",
    },

    # ── Linear algebra ─────────────────────────────────────────────
    {
        "prompt": "Show 2x2 matrix multiplication on a worked example with row × column highlights — Strang style.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/eb/Matrix_multiplication_diagram_2.svg/640px-Matrix_multiplication_diagram_2.svg.png",
        "citation": "Matrix multiplication, Strang Introduction to Linear Algebra §1.3",
        "domain": "linear algebra",
    },
    {
        "prompt": "Show the SVD geometrically — unit circle mapped to an ellipse by a 2x2 matrix.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/bb/Singular-Value-Decomposition.svg/640px-Singular-Value-Decomposition.svg.png",
        "citation": "Singular value decomposition, Strang §6.3",
        "domain": "linear algebra",
    },
    {
        "prompt": "Show a 2D rotation matrix's effect on the unit basis e₁, e₂.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/eb/Rotation_2D_3.svg/640px-Rotation_2D_3.svg.png",
        "citation": "Rotation matrix, Strang §8.1",
        "domain": "linear algebra",
    },

    # ── Set theory & probability ──────────────────────────────────
    {
        "prompt": "Draw a Venn diagram for A ∪ B ∩ C with concrete elements.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/eb/Venn_diagram_chart.svg/640px-Venn_diagram_chart.svg.png",
        "citation": "Venn diagrams, Halmos Naive Set Theory §2",
        "domain": "set theory",
    },
    {
        "prompt": "Visualise Bayes' theorem with a disease-testing example — prior, likelihood, posterior.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/18/Bayes_theorem_visualisation.svg/640px-Bayes_theorem_visualisation.svg.png",
        "citation": "Bayes' theorem, Bertsekas/Tsitsiklis §1.4",
        "domain": "probability",
    },
    {
        "prompt": "Draw the standard normal distribution with ±1σ, ±2σ, ±3σ tails marked (68-95-99.7 rule).",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/25/The_Normal_Distribution.svg/640px-The_Normal_Distribution.svg.png",
        "citation": "Standard normal distribution, Bertsekas §3.3",
        "domain": "probability",
    },

    # ── Discrete / number theory ──────────────────────────────────
    {
        "prompt": "Show the Euclidean algorithm finding gcd(252, 105) step by step.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/fa/Euclidean_algorithm_252_105_animation_step_by_step.gif/640px-Euclidean_algorithm_252_105_animation_step_by_step.gif",
        "citation": "Euclid's algorithm, Hardy & Wright §2.2",
        "domain": "number theory",
    },
    {
        "prompt": "Draw the sieve of Eratosthenes up to 30, with composites greyed out.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/94/Sieve_of_Eratosthenes_animation.gif/640px-Sieve_of_Eratosthenes_animation.gif",
        "citation": "Sieve of Eratosthenes, Hardy & Wright §1.4",
        "domain": "number theory",
    },
    {
        "prompt": "Draw Pascal's triangle, first 7 rows, with the binomial-coefficient labels.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/PascalTriangleAnimated2.gif/640px-PascalTriangleAnimated2.gif",
        "citation": "Pascal's triangle, Concrete Mathematics §5.1",
        "domain": "discrete",
    },

    # ── Algorithms / data structures ──────────────────────────────
    {
        "prompt": "Show BFS traversal on a small graph with the queue state at each step (CLRS style).",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/46/Animated_BFS.gif/640px-Animated_BFS.gif",
        "citation": "Breadth-first search, CLRS §22.2",
        "domain": "algorithms",
    },
    {
        "prompt": "Show binary-search-tree insertion of 5,3,8,1,4 step by step.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/da/Binary_search_tree.svg/640px-Binary_search_tree.svg.png",
        "citation": "Binary search trees, CLRS §12",
        "domain": "data structures",
    },
    {
        "prompt": "Show a min-heap with 7 nodes and the structural / heap-order properties.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/38/Min-heap.png/640px-Min-heap.png",
        "citation": "Heaps, CLRS §6",
        "domain": "data structures",
    },

    # ── Logic ──────────────────────────────────────────────────────
    {
        "prompt": "Draw a truth table for (p ∧ q) → r and the equivalent material-implication form.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/99/Truth_table_for_AND.svg/640px-Truth_table_for_AND.svg.png",
        "citation": "Propositional logic truth tables, Enderton Mathematical Logic §1.2",
        "domain": "logic",
    },

    # ── Math physics ──────────────────────────────────────────────
    {
        "prompt": "Draw a free-body diagram of a block on a frictionless inclined plane (Goldstein / Griffiths style).",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/35/Inclined_plane_force_diagram.svg/640px-Inclined_plane_force_diagram.svg.png",
        "citation": "Free-body diagrams, Halliday/Resnick §5",
        "domain": "physics",
    },
    {
        "prompt": "Show the projectile-motion parabolic trajectory with horizontal and vertical components.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1f/Ferde_hajitas3.svg/640px-Ferde_hajitas3.svg.png",
        "citation": "Projectile motion, Halliday/Resnick §4",
        "domain": "physics",
    },
    {
        "prompt": "Draw a converging-lens ray diagram with object beyond 2F (textbook optics figure).",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/Lens3b.svg/640px-Lens3b.svg.png",
        "citation": "Thin-lens equation, Hecht Optics §5",
        "domain": "physics",
    },

    # ── Statistics / regression ────────────────────────────────────
    {
        "prompt": "Show linear regression — best-fit line through a scatter of points with residuals drawn.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3a/Linear_regression.svg/640px-Linear_regression.svg.png",
        "citation": "Linear regression, ESL §3.2",
        "domain": "statistics",
    },

    # ── Topology ──────────────────────────────────────────────────
    {
        "prompt": "Show a torus as a square with opposite edges identified (Munkres style).",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d5/Torus_from_rectangle.gif/640px-Torus_from_rectangle.gif",
        "citation": "Torus quotient construction, Munkres Topology §54",
        "domain": "topology",
    },

    # ── Complex analysis ──────────────────────────────────────────
    {
        "prompt": "Draw the complex plane with z = 3 + 4i, its modulus, and its argument.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4c/Complex_number_illustration_modarg.svg/640px-Complex_number_illustration_modarg.svg.png",
        "citation": "Argand diagram, Ahlfors Complex Analysis §1.2",
        "domain": "complex analysis",
    },
    {
        "prompt": "Show Euler's identity e^(iπ) + 1 = 0 traced as a 180° rotation on the unit circle.",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/aa/Euler_formula.svg/640px-Euler_formula.svg.png",
        "citation": "Euler's identity, Ahlfors §1.5",
        "domain": "complex analysis",
    },
]


if __name__ == "__main__":
    print(f"{len(REFERENCES)} reference figures across "
          f"{len({r['domain'] for r in REFERENCES})} domains")
    by_domain = {}
    for r in REFERENCES:
        by_domain.setdefault(r["domain"], 0)
        by_domain[r["domain"]] += 1
    for d, n in sorted(by_domain.items()):
        print(f"  {d:20} {n}")
