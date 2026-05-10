"""Diverse prompt seed for generating a synthetic teacher corpus.

Each prompt is fired through ``express_figure(model='gpt-4o-mini', ...)``
to produce ``(prompt, svg, narration, repair_pairs)`` triples for
LoRA fine-tuning.  Coverage spans the visual concepts Sevim is meant
to teach: geometry, calculus, linear algebra, discrete maths,
probability, set theory, algorithms, complexity theory, basic physics.

~200 prompts is enough for an initial v3 run; SFT ablations historically
peak around 300-500 examples for the 7B Qwen2.5 base.
"""
from __future__ import annotations

PROMPTS: list[str] = [
    # ── Geometry — basic ──────────────────────────────────────────
    "show how the angles of a triangle sum to π in the style of Euclid's Elements I.32 (parallel-line construction)",
    "draw an equilateral triangle and label all three equal angles, Elements style",
    "show the Pythagorean theorem with a 3-4-5 triangle and squares on each side (Elements I.47 style)",
    "prove the Pythagorean theorem using similar triangles",
    "illustrate the inscribed-angle theorem on a circle",
    "show the inscribed angle vs central angle relationship",
    "draw a regular hexagon inscribed in a circle of radius 1",
    "illustrate Thales' theorem — angle in a semicircle is a right angle",
    "show the perpendicular bisector property of a triangle",
    "show all three medians of a triangle meeting at the centroid",
    "draw a triangle and its incircle",
    "draw a triangle and its circumcircle",
    "illustrate the law of cosines with a labeled triangle",
    "illustrate the law of sines with a labeled triangle",
    "show how a regular n-gon's interior angles sum to (n-2)π",
    "draw a parallelogram and show its diagonals bisect each other",

    # ── Geometry — coordinate ─────────────────────────────────────
    "draw the unit circle with sin and cos labelled at 30°, 45°, 60°",
    "show sin(θ) and cos(θ) on the unit circle for θ = 2π/3",
    "draw the line y = 2x + 1 and mark its slope and intercept",
    "show the distance formula between two points P(1,2) and Q(4,6)",
    "draw a parabola y = x² and mark its focus and directrix",
    "draw an ellipse with foci, label semi-major and semi-minor axes",
    "show the locus of points equidistant from two fixed points",
    "graph y = sin(x) and y = cos(x) on the same axes from 0 to 2π",
    "graph y = e^x and y = ln(x) and the line y = x to show they're inverses",
    "draw the spiral r = θ in polar coordinates",

    # ── Calculus ─────────────────────────────────────────────────
    "derivative of sin(x) shown as the slope of the tangent at x = π/4 (Spivak style)",
    "definite integral of x² from 0 to 2 as area under the curve (Spivak style)",
    "Riemann sum approximating ∫₀² x² dx with 8 rectangles (Spivak / Apostol style)",
    "left vs right vs midpoint Riemann sums on a single concave-up function",
    "fundamental theorem of calculus: derivative undoes integral",
    "Taylor series of e^x around 0, first three terms, plotted vs the true function",
    "Taylor series of sin(x) around 0, first three terms",
    "limit of (sin x) / x as x → 0 visualised with a chord and arc",
    "volume of revolution: rotate y = √x from 0 to 4 around the x-axis",
    "show the derivative of x² is 2x using the limit of secant slopes",
    "chain rule visualised — d/dx of sin(x²)",
    "L'Hôpital's rule on lim x→0 of sin(x)/x",
    "show the integral as a signed area, including a negative region",
    "show the second derivative as concavity on a smooth curve",
    "implicit differentiation of x² + y² = 25 at the point (3,4)",

    # ── Linear algebra ────────────────────────────────────────────
    "matrix multiplication of a 3x5 by a 5x4 matrix with concrete numbers and a worked dot product (Strang style)",
    "show how matrix multiplication encodes function composition (Axler style)",
    "eigenvalue decomposition of a 2x2 symmetric matrix with worked example (Strang)",
    "Gaussian elimination on a 3x3 system, step by step (Strang)",
    "singular value decomposition shown geometrically — circle to ellipse (Strang)",
    "show that det(A) is the area of the parallelogram spanned by columns",
    "rotation matrix in 2D visualised on a unit square",
    "shear matrix in 2D visualised on a unit square",
    "scaling matrix in 2D visualised on a unit square",
    "projection of a vector onto another, with the residual",
    "Gram-Schmidt orthogonalisation on three vectors in R³",
    "rank-nullity theorem with a 3x4 matrix example",
    "change of basis on a 2x2 matrix with both bases drawn",
    "dot product as scalar projection times length",
    "cross product geometric meaning — area, normal, right-hand rule",

    # ── Set theory & logic ────────────────────────────────────────
    "Venn diagram for A ∪ B ∩ C with concrete elements",
    "Venn diagram showing De Morgan's law (A ∪ B)' = A' ∩ B'",
    "Venn diagram for symmetric difference A △ B",
    "show set inclusion and proper subset with two overlapping circles",
    "truth table for (p ∧ q) ∨ (¬p)",
    "truth table for material implication p → q",
    "show that p → q is equivalent to ¬p ∨ q",
    "Cartesian product A × B as a grid of points",
    "power set of {a, b, c} as a Hasse diagram",
    "function vs relation — vertical line test",
    "injective vs surjective vs bijective with three small examples",

    # ── Discrete math / combinatorics ─────────────────────────────
    "pigeonhole principle illustrated with 5 pigeons in 4 holes",
    "combinations vs permutations of 3 items chosen from 5",
    "Pascal's triangle, first 7 rows, with the choose-formula highlighted",
    "binomial theorem expansion of (a+b)⁴ with Pascal's row",
    "stars-and-bars: number of ways to distribute 7 candies among 3 children",
    "inclusion-exclusion principle on three overlapping sets",
    "principle of mathematical induction visualised as toppling dominoes",
    "Fibonacci sequence as a sum of squares (golden spiral)",

    # ── Number theory ─────────────────────────────────────────────
    "Euclidean algorithm to find gcd(252, 105) step by step",
    "extended Euclidean algorithm finding x, y for gcd(35, 15) = 35x + 15y",
    "modular arithmetic: 17 mod 5 = 2 visualised on a number line wrap",
    "modular exponentiation: 3^7 mod 11 by repeated squaring",
    "sieve of Eratosthenes up to 30",
    "Bezout's identity for 18 and 12",
    "Chinese remainder theorem on x ≡ 2 mod 3, x ≡ 3 mod 5",
    "Fermat's little theorem on a small example",

    # ── Probability ───────────────────────────────────────────────
    "Bayes' theorem visualisation with a disease testing example",
    "tree diagram for P(rain) given two cloud-states",
    "binomial distribution histogram for n=10, p=0.5",
    "normal distribution with mean and ±1σ, ±2σ, ±3σ marked",
    "Monty Hall problem: stay vs switch outcomes",
    "law of large numbers — coin-flip averages converging on 0.5",
    "central limit theorem — sample-mean distribution narrowing as n grows",
    "expected value of a fair die roll computed visually",
    "conditional probability tree for two-step process",
    "Poisson distribution for λ=3 with bars",
    "covariance vs correlation with three scatterplots",

    # ── Algorithms / data structures ─────────────────────────────
    "BFS traversal on a small graph step by step (CLRS pseudocode + figure)",
    "DFS traversal on the same graph showing the recursion stack (CLRS)",
    "Dijkstra's shortest path on a 5-node weighted graph (CLRS)",
    "binary search on a sorted array of 16 numbers, target middle-ish",
    "linked list insertion at the head, before vs after",
    "binary search tree insertion of 5,3,8,1,4 step by step",
    "quicksort partition step on an 8-element array",
    "merge step of merge sort on two 4-element sorted arrays",
    "AVL tree single rotation (left rotation) on a small subtree",
    "min-heap sift-down on a small binary heap",
    "topological sort on a 6-node DAG",
    "Kruskal's MST on a 6-node weighted graph",
    "Prim's MST on a 6-node weighted graph",
    "Floyd-Warshall on a 4-node graph, one iteration",
    "longest common subsequence DP table for ABCBDAB and BDCABA",
    "edit distance DP table for KITTEN and SITTING",
    "knapsack 0/1 DP table for items (2,3),(3,4),(4,5),(5,6) capacity 5",

    # ── Complexity / reductions ──────────────────────────────────
    "reduce 3SAT to vertex cover with a small worked example",
    "reduce 3SAT to clique with a worked example",
    "reduce 3SAT to hamiltonian path on a 3-clause example",
    "show that vertex cover is NP-hard via reduction from independent set",
    "subset sum to partition reduction with a small example",
    "P vs NP vs NP-hard vs NP-complete — a Venn-style diagram",
    "show that 2SAT is in P via implication graph + SCC",
    "halting problem self-reference proof, schematic",

    # ── Linear / convex programming ──────────────────────────────
    "linear programming feasible region for a 2-variable LP with 3 constraints",
    "simplex method one pivot on a 2-var LP with the geometry",
    "duality: primal min cT x = dual max bT y on a tiny LP",
    "convex set vs non-convex set — three examples each",
    "supporting hyperplane theorem visualised on a convex set",

    # ── Physics (visualisable) ───────────────────────────────────
    "free-body diagram of a block on an inclined plane with friction",
    "projectile motion of a ball at 45° with the parabolic path",
    "simple harmonic motion x(t) = A cos(ωt) overlaid with the unit-circle phase",
    "centripetal force on a ball in circular motion — vector diagram",
    "lens equation 1/f = 1/v + 1/u with ray diagram for a converging lens",
    "Snell's law — refraction at an interface between two media",
    "RC circuit charging curve with the time constant marked",
    "wave interference — two coherent sources with constructive + destructive nodes",
    "Doppler effect — moving source vs stationary observer wavefronts",

    # ── Statistics ───────────────────────────────────────────────
    "linear regression — best-fit line through 6 scatter points with residuals",
    "histogram of 20 samples with mean and median marked",
    "boxplot showing quartiles, median, IQR, whiskers, outliers",
    "QQ plot of a sample against a theoretical normal",
    "p-value visualisation — observed statistic in the tail of a null distribution",

    # ── Trigonometry identities ─────────────────────────────────
    "show sin² + cos² = 1 on the unit circle",
    "double angle: sin(2θ) = 2 sin θ cos θ visualised",
    "law of cosines: c² = a² + b² − 2ab cos γ on a triangle",
    "tangent as slope of unit-circle radius",

    # ── Geometry — solids ───────────────────────────────────────
    "volume of a sphere shaded with the radius and the (4/3)πr³ formula",
    "volume of a cylinder vs a cone vs a sphere of the same radius and height",
    "Pythagorean theorem in 3D — diagonal of a unit cube is √3",
    "cross-section of a cone — three conic-section types",

    # ── Number visualisation ────────────────────────────────────
    "represent π via a circle's circumference / diameter",
    "represent e via continuous compounding (1 + 1/n)^n as n grows",
    "the golden ratio φ as a self-similar rectangle",
    "complex number 3 + 4i on the Argand plane with magnitude and angle",
    "Euler's identity e^(iπ) + 1 = 0 traced on the complex plane",

    # ── Game theory & decision ──────────────────────────────────
    "Prisoner's dilemma payoff matrix",
    "Nash equilibrium on a 2x2 coordination game",
    "expected utility tree for a two-stage decision",

    # ── Information / entropy ───────────────────────────────────
    "Shannon entropy of a fair coin vs a biased coin",
    "binary tree of a Huffman code for letters a:5, b:9, c:12, d:13, e:16, f:45",

    # ── Misc (visualisable) ──────────────────────────────────────
    "epsilon-delta definition of a limit, visualised",
    "convergent vs divergent series — geometric vs harmonic",
    "arithmetic vs geometric sequence — first 5 terms of each",
    "linear vs exponential vs logistic growth — three curves",
    "function composition (g ∘ f)(x) shown with two function machines",
    "intermediate value theorem — continuous function crossing zero",
    "mean value theorem — tangent parallel to secant on a smooth curve",
    "Taylor's theorem remainder term visualised",
]


if __name__ == "__main__":
    print(f"{len(PROMPTS)} prompts")
