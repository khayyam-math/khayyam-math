"""Round-2 expansion of the v4 teacher-prompt pool.

Starts from the existing 1545-prompt PROMPTS_V4 and pushes coverage out
into specialist domains that the v4 pool only touched lightly:

  * probability & statistics (distributions, Bayes, Markov chains, MCMC)
  * optimisation (gradient descent, KKT, Lagrange, simplex)
  * signal processing (Fourier, FFT, filters, convolution)
  * number theory & cryptography (modular arithmetic, RSA, lattices)
  * complex analysis (contour integrals, Möbius, residues)
  * differential equations (slope fields, phase portraits, separation)
  * machine learning visuals (decision boundaries, SVM, PCA, kernels)
  * physics figures (pendulum, spring, EM, quantum density)
  * combinatorics (partitions, generating functions, Stirling)
  * topology beyond the basics (homotopy, fundamental group, knots)

Output: ``scripts/expanded_prompts_v5.py`` exposing PROMPTS_V5 — a
deduplicated superset of PROMPTS_V4 plus the new entries.

Usage:
    .venv/bin/python scripts/expand_prompt_pool_v5.py \\
        --extra-llm-rounds 2 --target-total 3500
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.expanded_prompts import PROMPTS_V4  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Hand-curated specialist additions — direct quality-controlled prompts
# ─────────────────────────────────────────────────────────────────────

HANDCURATED_V5: list[str] = [
    # --- probability & statistics ---
    "PDF and CDF of Normal(0, 1) drawn on the same axes",
    "PDF of Normal(μ, σ²) for σ = 0.5, 1, 2 on a single panel",
    "PDF of an Exponential(λ) distribution for λ = 0.5, 1, 2",
    "PMF of Binomial(n=20, p=0.3) as a stem plot",
    "PMF of Poisson(λ=3) for k = 0..15",
    "uniform vs Gaussian density on the same axes — same mean and variance",
    "geometric distribution PMF for p = 0.3",
    "Beta(2, 5) and Beta(5, 2) densities side by side",
    "Chi-squared density for k = 1, 2, 4, 8 degrees of freedom",
    "two-sample t-test rejection regions on the t-distribution PDF",
    "Bayes' rule update of a Beta-prior to a Beta-posterior after coin flips",
    "posterior of μ for a Gaussian likelihood with a Gaussian prior — conjugate update",
    "law of large numbers — running mean of Bernoulli(0.5) samples converges to 0.5",
    "central limit theorem — sample-mean distribution of n=2, n=10, n=50 dice rolls",
    "Markov chain transition diagram with three states (sunny, cloudy, rainy)",
    "absorbing Markov chain — gambler's ruin from $5 with absorbing $0 and $10",
    "two-step transition probabilities computed from a 3-state transition matrix",
    "hidden Markov model — observations vs hidden states over 6 time steps",
    "MCMC trace plot for a 2D Gaussian target via Metropolis-Hastings",
    "rejection-sampling visual: sampling from p(x) by enclosing it in c·q(x)",
    "importance-sampling reweight: target p(x) over proposal q(x)",
    "confidence interval for a sample mean at 95% — illustrate with one realisation",
    "p-value as the tail area beyond an observed test statistic",
    "ROC curve for a binary classifier with three threshold points labelled",
    "precision vs recall curve at three thresholds",
    "bias-variance decomposition — single chart with bias², variance, total error",

    # --- optimisation ---
    "gradient descent path on the convex quadratic f(x,y) = x² + 4y², 8 steps",
    "gradient descent path on Rosenbrock's f(x,y) = (1-x)² + 100(y-x²)², 30 steps",
    "Newton's method one-step on a 1D parabola — show tangent vs. update",
    "secant method on f(x) = x² - 2 — show how the secant updates",
    "bisection method on f(x) = x³ - x - 2 — three brackets",
    "convex function: y = x² with two points and the chord above the graph",
    "non-convex function: y = x⁴ - 3x² + 1 with two local minima",
    "convex set vs non-convex set side by side, simple shapes",
    "Lagrange multiplier — minimise x² + y² subject to x + y = 1",
    "Lagrange multiplier — maximise xy subject to x² + y² = 1",
    "KKT conditions on min x² + y² subject to x + y ≥ 1, with active constraint",
    "linear program in 2D — feasible polygon with optimal vertex marked",
    "simplex method one pivot — feasible vertex move on a 2D LP",
    "duality in 2D LP — primal feasible region vs dual feasible region",
    "subgradient illustration on f(x) = |x| at x = 0",
    "proximal operator — soft-thresholding on |x| at threshold λ",
    "stochastic gradient descent path vs full-batch gradient descent on a quadratic",
    "Adam vs SGD on a saddle-point function f(x,y) = x² - y²",
    "constraint set x ≥ 0, y ≥ 0, x + y ≤ 1 with a contour of f = xy",
    "trust-region step on a 2D quadratic model — circular trust region",

    # --- signal processing ---
    "Fourier series of a square wave: first 1, 3, 5 harmonics overlayed",
    "Fourier series of a triangle wave: first 5 harmonics",
    "Fourier series of a sawtooth wave: first 5 harmonics",
    "DFT magnitude spectrum of x(n) = cos(2π·5·n/64) sampled at 64 points",
    "FFT bin index → frequency mapping for a 64-point DFT at 1 kHz sampling",
    "convolution of two box functions — moving overlap visual",
    "convolution of an exponential decay with a unit impulse train",
    "low-pass FIR filter: impulse response and magnitude response",
    "high-pass IIR filter: pole-zero plot on the z-plane",
    "Butterworth low-pass — magnitude response for orders 2, 4, 6",
    "z-transform pole-zero plot of a stable second-order IIR filter",
    "spectrogram of a chirp from 100 Hz to 1 kHz over 1 second",
    "Nyquist-Shannon: aliasing of a 7 Hz cosine sampled at 10 Hz",
    "windowing tradeoff: rectangular vs Hann window on a sinusoid spectrum",
    "Hilbert transform: original signal vs analytic signal envelope",
    "decimation by 4: original spectrum vs decimated spectrum",

    # --- number theory & cryptography ---
    "RSA: pick p=11, q=13, e=7 — compute n, φ(n), d step by step",
    "RSA encrypt M=9 with (n=143, e=7) — modular exponentiation steps",
    "Diffie-Hellman key exchange with p=23, g=5, Alice=6, Bob=15",
    "extended Euclidean algorithm for gcd(120, 23) — back-substitution table",
    "Sieve of Eratosthenes for primes up to 30 — cross-out diagram",
    "Fermat's little theorem: 2^10 mod 11 = 1 worked example",
    "Wilson's theorem on n=7: (7-1)! mod 7 = -1",
    "Chinese remainder theorem: x ≡ 2 (mod 3), x ≡ 3 (mod 5), x ≡ 2 (mod 7)",
    "continued fraction expansion of √2 — first 5 convergents",
    "Pell equation x² - 2y² = 1 — first three solutions plotted",
    "elliptic curve y² = x³ - x + 1 over ℝ with two points and their sum",
    "lattice in ℝ² with basis vectors (2,1) and (1,3)",
    "Gauss-Jordan reduction of a 3×3 matrix to row-reduced echelon form",

    # --- complex analysis ---
    "Möbius transformation f(z) = (z - i)/(z + i) — image of the unit circle",
    "Möbius transformation f(z) = 1/z — image of vertical lines",
    "Cauchy contour integral of 1/z around the unit circle",
    "winding number of a contour around a point — three example contours",
    "residue at a simple pole z=1 of f(z) = 1/(z² - 1)",
    "Joukowski transform z + 1/z applied to a unit circle — airfoil shape",
    "branch cut for log(z) along the negative real axis",
    "conformal map: upper half-plane to the unit disc via z → (z-i)/(z+i)",
    "Riemann sphere: stereographic projection from north pole",
    "argument principle: zeros and poles inside a contour",

    # --- differential equations ---
    "slope field of dy/dx = y - x with three solution curves",
    "slope field of dy/dx = -x/y showing concentric circle solutions",
    "phase portrait of the linear system x' = -y, y' = x (centre)",
    "phase portrait of x' = x - y, y' = x + y (unstable spiral)",
    "phase portrait of the damped pendulum θ'' + 0.2θ' + sin(θ) = 0",
    "phase portrait of the Lotka-Volterra predator-prey system",
    "phase portrait of the van der Pol oscillator x'' - μ(1 - x²)x' + x = 0 for μ = 1",
    "logistic equation dy/dt = ry(1 - y/K) — three solution curves with K=1",
    "Euler method on dy/dt = y, y(0)=1, h=0.25, four steps vs exact e^t",
    "RK4 step on dy/dt = -2y, y(0)=1, h=0.5",
    "second-order ODE y'' + y = 0 — sin and cos as basis solutions",
    "wave equation u_tt = c² u_xx — d'Alembert solution from initial data",
    "heat equation on [0, π] with sinusoidal initial data — decay over time",

    # --- machine learning visuals ---
    "linear classifier decision boundary on a 2D dataset with two classes",
    "logistic regression sigmoid output on a 2D dataset",
    "SVM with hard margin: support vectors and margin highlighted",
    "SVM with soft margin: slack variables on misclassified points",
    "kernel trick: 1D non-separable data → 2D separable via φ(x) = (x, x²)",
    "RBF kernel decision boundary on concentric-rings data",
    "k-nearest neighbours classification regions for k=1, 3, 5",
    "k-means clustering on 3 well-separated 2D clusters — converge over 4 iterations",
    "Gaussian mixture model with three components on a 2D dataset",
    "PCA on a 2D elliptical cloud — first and second principal components",
    "linear regression line through 8 noisy points with residuals",
    "polynomial regression of degree 1, 3, 9 on the same 12 points (over/under-fit)",
    "decision tree splitting a 2D dataset into rectangular regions",
    "random forest ensemble — three tree decisions overlaid",
    "single neuron with sigmoid activation — input weights and bias labelled",
    "fully connected network 2-3-2 — neurons and weights",
    "convolution layer: 3×3 kernel sliding over a 5×5 input",
    "ReLU vs sigmoid vs tanh activation functions on the same axes",
    "softmax over a 3-class logit vector — bar chart of probabilities",
    "cross-entropy loss as a function of predicted probability for true class",
    "gradient descent on a 2D loss surface with two minima",
    "bias-variance tradeoff curve — training and test error vs model complexity",
    "train vs validation loss vs epoch with early stopping",
    "ROC curve for two classifiers compared on one panel",
    "confusion matrix for a 3-class classifier — heatmap with row/col labels",

    # --- physics ---
    "simple pendulum free-body diagram with length L and mass m",
    "spring-mass-damper free-body diagram with x = 0 equilibrium",
    "projectile motion: trajectory with initial velocity v₀ at angle θ",
    "uniform circular motion: radius R, angular velocity ω, centripetal acceleration",
    "electric field of a point charge — radial field lines",
    "electric field of a dipole — field lines and equipotentials",
    "magnetic field of a long straight wire — circular field lines",
    "magnetic field of a solenoid — interior near-uniform field",
    "Lenz's law — induced current opposing flux change in a coil",
    "RC circuit charging curve: V(t) = V₀(1 - e^{-t/RC})",
    "RL circuit current rise: I(t) = (V/R)(1 - e^{-Rt/L})",
    "LC oscillation: charge and current sinusoidal vs time",
    "ray diagram of a converging lens for an object beyond 2f",
    "ray diagram of a diverging lens for any object",
    "Snell's law: refraction at a glass-air interface, n=1.5",
    "double-slit interference pattern with fringe spacing Δy = λL/d",
    "single-slit diffraction pattern with first minimum location",
    "standing wave on a string fixed at both ends — first three modes",
    "quantum wavefunction ψ(x) = Ae^{-x²/2} and |ψ|² on the same axes",
    "particle in a box: first three energy eigenfunctions",
    "harmonic oscillator: first three eigenfunctions and energy levels",
    "Bohr model of hydrogen — n=1, n=2, n=3 orbits with photon emission n=3→n=2",

    # --- combinatorics & discrete maths ---
    "Pascal's triangle row 6 — binomial expansion (a+b)⁶ shown alongside",
    "lattice paths from (0,0) to (4,4) — count via C(8,4) = 70",
    "Catalan numbers C₀ to C₅ via balanced-parentheses count",
    "Stirling numbers of the second kind S(4, 2) = 7 — partitions of {1,2,3,4} into 2 blocks",
    "partitions of n=5 — all 7 partitions listed",
    "generating function (1+x)(1+x²)(1+x³) expanded — partition counting",
    "integer compositions of n=4 — all 8 ordered compositions",
    "derangement count !4 = 9 — list all 9 derangements of {1,2,3,4}",
    "Hasse diagram of the divisors of 12 under divisibility",
    "Hasse diagram of the power set 2^{a,b,c} under inclusion",
    "Boolean lattice B₃ — 3D cube with vertices labelled by subsets",
    "Burnside's lemma: count distinct colourings of a square's vertices with 2 colours under rotation",
    "Polya enumeration on a necklace of 5 beads, 2 colours, rotation only",
    "graph colouring: Petersen graph 3-coloured",
    "Eulerian circuit on the bridges of Königsberg — show why none exists",
    "Hamiltonian cycle on the dodecahedron graph",
    "minimum spanning tree of a 6-node weighted graph via Kruskal",
    "minimum spanning tree of a 6-node weighted graph via Prim",
    "max flow / min cut on a 6-node network with capacities",
    "bipartite matching: 4 jobs and 4 workers with compatibility edges",
    "topological sort of a 6-node DAG — one valid ordering",
    "strongly connected components of a 7-node directed graph (Tarjan)",

    # --- topology beyond the basics ---
    "stereographic projection of S² minus the north pole onto the plane",
    "Möbius strip: rectangle with edge identification a ~ a (flipped)",
    "Klein bottle: square with edge identifications a~a, b~b (flipped)",
    "torus: square with edge identifications a~a, b~b — fundamental polygon",
    "real projective plane RP²: square with antipodal identification",
    "fundamental group of the circle as winding number — concentric loops",
    "homotopy of two paths in ℝ² with one obstacle — show one cannot deform to the other",
    "covering space: ℝ → S¹ via t ↦ e^{2πit}, fibres above one point",
    "Euler characteristic of the cube: V - E + F = 8 - 12 + 6 = 2",
    "Euler characteristic of the torus: V - E + F = 0 (1×1 polygon decomposition)",
    "trefoil knot diagram with three crossings",
    "figure-eight knot diagram with four crossings",
    "Reidemeister moves I, II, III on a small knot diagram",

    # --- group theory & abstract algebra ---
    "Cayley table for the cyclic group ℤ/4ℤ",
    "Cayley table for the symmetric group S₃",
    "Cayley graph of ℤ/6ℤ — hexagon with single generator",
    "Cayley graph of S₃ — generated by a transposition and a 3-cycle",
    "subgroup lattice of ℤ/12ℤ",
    "subgroup lattice of D₄ (dihedral order 8)",
    "cosets of 3ℤ in ℤ — three cosets coloured on a number line",
    "quotient group ℤ/4ℤ as cosets of 4ℤ in ℤ",
    "kernel and image of the map φ: ℤ → ℤ/3ℤ — first isomorphism theorem visual",
    "ring of integers mod 6 — addition and multiplication tables",
    "field ℤ/5ℤ — multiplication table excluding 0",
    "Galois lattice of ℚ(√2, √3) over ℚ",
    "permutation as product of disjoint cycles — example (1 2 3)(4 5)",
    "alternating group A₄ — 12 elements as 3-cycles and double transpositions",

    # --- real analysis ---
    "epsilon-delta definition of continuity at x = 1 — picture for f(x) = x²",
    "uniform continuity on [0,1] vs pointwise continuity on (0,1) for 1/x",
    "supremum and infimum of {1/n : n ∈ ℕ} on the number line",
    "monotone bounded sequence converges — visualise a₁ < a₂ < … < L",
    "Cauchy sequence — show pairwise distances shrink to 0",
    "Bolzano-Weierstrass: bounded sequence with two convergent subsequences",
    "open cover of [0,1] by intervals — finite subcover via Heine-Borel",
    "uniform vs pointwise convergence: f_n(x) = x^n on [0,1]",
    "intermediate value theorem on f(x) = x³ - x - 1 on [1, 2]",
    "mean value theorem on f(x) = x² on [0, 2] — point c=1 with matching slope",
    "Rolle's theorem on f(x) = sin(x) on [0, π]",
    "Taylor's theorem with remainder term R_n(x) — pictured error bar",
    "Lipschitz continuity — function bounded between two cones of slope L",

    # --- linear algebra (deeper) ---
    "rank-nullity theorem — null space and column space of a 3×4 matrix",
    "QR decomposition of a 3×3 matrix via Gram-Schmidt — show orthogonalisation",
    "LU decomposition of a 3×3 matrix — lower and upper factors",
    "eigenvalue decomposition of [[2,1],[1,2]] — eigenvectors at 45° and 135°",
    "singular value decomposition of a 2×3 matrix — geometric meaning",
    "Jordan canonical form of a 3×3 defective matrix",
    "diagonalisation of [[3,1],[0,2]] — eigenvectors and eigenvalues",
    "spectral theorem on a 2×2 symmetric matrix — orthogonal eigenvectors",
    "least squares: project b onto the column space of A — residual perpendicular",
    "Gram matrix and inner-product geometry of three vectors in ℝ²",
    "change of basis: same vector in standard and rotated bases",
    "kernel of a linear map T: ℝ³ → ℝ² visualised as a plane through origin",
]


# ─────────────────────────────────────────────────────────────────────
# Parametric templates — additional specialist domains
# ─────────────────────────────────────────────────────────────────────

PARAMETRIC_V5: list[tuple[str, list[dict]]] = [
    ("PDF of Normal(0, σ²) for σ = {s} on a single panel",
     [{"s": s} for s in ["0.25", "0.5", "1.0", "1.5", "2.0", "3.0"]]),
    ("PDF of Beta({a}, {b}) on [0,1]",
     [{"a": a, "b": b}
      for (a, b) in [(2, 5), (5, 2), (1, 1), (2, 2), (0.5, 0.5),
                     (3, 1), (1, 3), (8, 8)]]),
    ("PMF of Binomial(n={n}, p={p}) as a stem plot",
     [{"n": n, "p": p}
      for n in [10, 20, 50]
      for p in ["0.1", "0.3", "0.5", "0.7", "0.9"]]),
    ("PMF of Poisson(λ={lam}) for k=0..{kmax}",
     [{"lam": lam, "kmax": kmax}
      for lam in ["0.5", "1.0", "2.0", "3.0", "5.0", "8.0"]
      for kmax in [10, 15, 20]]),
    ("Markov chain transition diagram with {n} states (uniform random transitions)",
     [{"n": n} for n in [3, 4, 5, 6]]),
    ("gradient descent path on f(x,y) = {a}x² + {b}y², step size {h}, {steps} steps",
     [{"a": a, "b": b, "h": h, "steps": steps}
      for (a, b) in [(1, 1), (1, 4), (1, 10), (2, 1)]
      for h in ["0.1", "0.2", "0.3"]
      for steps in [5, 10, 20]]),
    ("Newton's method on f(x) = {f}, starting at x₀={x0}, {steps} iterations",
     [{"f": f, "x0": x0, "steps": steps}
      for f in ["x² - 2", "x³ - x - 1", "cos(x) - x", "x² - 5"]
      for x0 in ["1", "2", "0.5", "1.5"]
      for steps in [3, 5]]),
    ("Fourier series of a {kind} wave with {n} harmonics",
     [{"kind": k, "n": n}
      for k in ["square", "triangle", "sawtooth"]
      for n in [1, 3, 5, 7, 11]]),
    ("DFT magnitude spectrum of x(n) = cos(2π·{f}·n/{N}) sampled at {N} points",
     [{"f": f, "N": N}
      for f in [3, 5, 7, 11]
      for N in [32, 64, 128]]),
    ("convolution of {f} with {g} — moving overlap",
     [{"f": f, "g": g}
      for f in ["a unit box", "an exponential decay e^{-t}", "a triangle pulse"]
      for g in ["a unit box", "a unit impulse", "an exponential decay e^{-t}"]]),
    ("RSA with primes p={p}, q={q}, public exponent e={e}",
     [{"p": p, "q": q, "e": e}
      for (p, q, e) in [(11, 13, 7), (5, 7, 5), (13, 17, 5),
                        (7, 11, 13), (17, 19, 7)]]),
    ("Diffie-Hellman with p={p}, generator g={g}, Alice's secret {a}, Bob's secret {b}",
     [{"p": p, "g": g, "a": a, "b": b}
      for (p, g, a, b) in [(23, 5, 6, 15), (29, 2, 7, 11),
                           (17, 3, 5, 9), (31, 7, 4, 13)]]),
    ("Chinese remainder theorem with x ≡ {a} (mod {m}), x ≡ {b} (mod {n})",
     [{"a": a, "m": m, "b": b, "n": n}
      for (a, m, b, n) in [(2, 3, 3, 5), (1, 4, 4, 7),
                           (3, 8, 7, 9), (5, 11, 6, 13)]]),
    ("phase portrait of x' = {a}x + {b}y, y' = {c}x + {d}y",
     [{"a": a, "b": b, "c": c, "d": d}
      for (a, b, c, d) in [(0, -1, 1, 0), (-1, 0, 0, -1),
                           (1, -1, 1, 1), (-1, 1, -1, -1),
                           (0, 1, -1, 0), (2, 0, 0, -1),
                           (1, 1, 0, 2)]]),
    ("slope field of dy/dx = {expr}",
     [{"expr": e}
      for e in ["x + y", "y - x", "-x/y", "x*y", "x² - y", "y - x²",
                "sin(x)", "y(1-y)", "x*(1-y)"]]),
    ("Euler method on dy/dt = {f}, y(0)={y0}, h={h}, {steps} steps",
     [{"f": f, "y0": y0, "h": h, "steps": steps}
      for f in ["y", "-2y", "y - t", "t² - y"]
      for y0 in ["1", "0.5", "2"]
      for h in ["0.1", "0.25", "0.5"]
      for steps in [4, 8]]),
    ("ray diagram of a {kind} lens for an object at {pos}",
     [{"kind": k, "pos": p}
      for k in ["converging", "diverging"]
      for p in ["beyond 2f", "between f and 2f", "inside f", "at 2f", "at f"]]),
    ("electric field of a {config}",
     [{"config": c}
      for c in ["point charge +q", "point charge -q", "dipole",
                "uniformly charged ring", "infinite line of charge",
                "infinite plane of charge", "two parallel plates",
                "uniformly charged sphere"]]),
    ("RC circuit charging curve with R={R}Ω, C={C}μF, V₀={V}V",
     [{"R": R, "C": C, "V": V}
      for R in [100, 1000, 10000]
      for C in [1, 10, 100]
      for V in [5, 9, 12]]),
    ("k-means on {n} clusters in 2D after {iters} iterations",
     [{"n": n, "iters": it}
      for n in [2, 3, 4, 5]
      for it in [1, 3, 5, 10]]),
    ("decision boundary of a {clf} on a 2D {data} dataset",
     [{"clf": c, "data": d}
      for c in ["logistic regression", "linear SVM", "RBF-kernel SVM",
                "k-NN (k=5)", "decision tree (depth 3)"]
      for d in ["two-Gaussian", "concentric-rings", "moons", "XOR"]]),
    ("PCA on a {shape} 2D cloud with first and second principal components",
     [{"shape": s}
      for s in ["elliptical", "diagonal-stretched", "horizontally-stretched",
                "two-cluster", "three-cluster"]]),
    ("polynomial regression of degree {d} on {n} noisy points",
     [{"d": d, "n": n}
      for d in [1, 2, 3, 5, 9]
      for n in [10, 15, 25]]),
    ("Hasse diagram of the divisors of {n} under divisibility",
     [{"n": n} for n in [6, 8, 12, 16, 24, 30, 36, 60]]),
    ("Cayley table for the {group}",
     [{"group": g}
      for g in ["cyclic group ℤ/3ℤ", "cyclic group ℤ/4ℤ",
                "cyclic group ℤ/5ℤ", "Klein four-group V₄",
                "symmetric group S₃", "dihedral group D₃",
                "dihedral group D₄"]]),
    ("subgroup lattice of {group}",
     [{"group": g}
      for g in ["ℤ/12ℤ", "ℤ/16ℤ", "ℤ/24ℤ", "S₃", "S₄", "D₄", "D₅",
                "Q₈ (quaternion group)"]]),
    ("Catalan number C_{n} via balanced-parentheses count",
     [{"n": n} for n in [2, 3, 4, 5]]),
    ("Stirling number of the second kind S({n}, {k}) — partitions visualised",
     [{"n": n, "k": k}
      for (n, k) in [(3, 2), (4, 2), (4, 3), (5, 2), (5, 3), (5, 4),
                     (6, 3)]]),
    ("partitions of n={n} listed",
     [{"n": n} for n in [3, 4, 5, 6, 7, 8]]),
    ("eigenvalue decomposition of [[{a},{b}],[{b},{c}]]",
     [{"a": a, "b": b, "c": c}
      for (a, b, c) in [(2, 1, 2), (3, 1, 3), (4, 2, 1), (5, 3, 5),
                        (1, 0, 1), (2, -1, 2), (4, 1, 4)]]),
    ("singular value decomposition of a {m}×{n} matrix",
     [{"m": m, "n": n}
      for (m, n) in [(2, 2), (2, 3), (3, 2), (3, 3), (3, 4), (4, 3)]]),
    ("least squares fit of a line to {n} 2D points",
     [{"n": n} for n in [4, 6, 8, 10, 12, 15]]),
    ("Möbius transformation z → ({a}z + {b})/({c}z + {d}) image of the unit circle",
     [{"a": a, "b": b, "c": c, "d": d}
      for (a, b, c, d) in [(1, "-i", 1, "i"), (1, 0, 0, 1), ("i", 0, 0, 1),
                           (1, 1, 1, "-1"), (2, 1, 1, 1)]]),
    ("contour integral of {f} around |z|={r}",
     [{"f": f, "r": r}
      for f in ["1/z", "1/z²", "1/(z-1)", "1/(z²+1)", "z/(z²-1)", "e^z/z"]
      for r in ["0.5", "1", "2"]]),
    ("standing wave on a string of length L — mode n={n}",
     [{"n": n} for n in [1, 2, 3, 4, 5]]),
    ("particle-in-a-box wavefunction ψ_{n} on [0, L]",
     [{"n": n} for n in [1, 2, 3, 4, 5]]),
    ("harmonic oscillator wavefunction ψ_{n}(x)",
     [{"n": n} for n in [0, 1, 2, 3, 4]]),
    ("bipartite matching between {a} jobs and {b} workers",
     [{"a": a, "b": b}
      for (a, b) in [(3, 3), (4, 4), (4, 5), (5, 4), (5, 5)]]),
    ("max flow / min cut on a network with {n} nodes",
     [{"n": n} for n in [4, 5, 6, 7]]),
    ("graph colouring of {graph} with {k} colours",
     [{"graph": g, "k": k}
      for g in ["K₄", "K₅", "Petersen graph", "the cube graph Q₃",
                "a 5-cycle C₅", "a 6-cycle C₆"]
      for k in [2, 3, 4]]),
    ("knot diagram of the {knot} with all crossings labelled",
     [{"knot": k}
      for k in ["unknot", "trefoil", "figure-eight knot", "Hopf link",
                "Borromean rings", "Solomon's knot"]]),
    ("sieve of Eratosthenes for primes up to {n}",
     [{"n": n} for n in [20, 30, 50, 100]]),
    ("Euclidean algorithm for gcd({a}, {b}) step by step",
     [{"a": a, "b": b}
      for (a, b) in [(360, 84), (420, 144), (1001, 143), (713, 184),
                     (924, 252), (315, 105), (1599, 533)]]),
]


# ─────────────────────────────────────────────────────────────────────
# Specialist LLM rounds — narrower than the v4 LEVELS, more depth
# ─────────────────────────────────────────────────────────────────────

SPECIALIST_DOMAINS = [
    ("probability and statistics",
     "PDFs/CDFs of named distributions, conjugate priors, "
     "Markov chains, MCMC traces, bootstrap, ROC/precision-recall, "
     "central limit theorem visuals, Bayesian updates, hypothesis tests"),
    ("convex optimisation",
     "gradient descent paths, Newton's method, KKT conditions, "
     "Lagrange multipliers, simplex method, duality, "
     "subgradients, proximal operators, trust regions, line search"),
    ("signal processing and Fourier analysis",
     "Fourier series, DFT/FFT spectra, FIR/IIR filters, "
     "z-plane pole-zero plots, convolution visuals, "
     "spectrograms, sampling and aliasing, windowing"),
    ("number theory and cryptography",
     "modular arithmetic, RSA worked examples, Diffie-Hellman, "
     "Chinese remainder theorem, elliptic curves, lattices, "
     "Sieve of Eratosthenes, continued fractions, primality"),
    ("complex analysis",
     "contour integrals, Möbius transforms, residues, branch cuts, "
     "conformal maps, Riemann surfaces, winding numbers, Joukowski"),
    ("ordinary differential equations",
     "slope fields, phase portraits, stability of fixed points, "
     "Euler / RK4 stepping, separation of variables visuals, "
     "linear systems with eigenvalue classification, Lotka-Volterra"),
    ("machine learning visualisations",
     "decision boundaries (linear, SVM, RBF, k-NN, tree), "
     "k-means iterations, PCA, polynomial regression over/under-fit, "
     "neural-network architectures, activation functions, ROC curves, "
     "confusion matrices, gradient descent on loss surfaces"),
    ("classical and modern physics",
     "free-body diagrams, ray optics, electric/magnetic field lines, "
     "RC/RL/LC circuits, projectile motion, pendulums, springs, "
     "double-slit, Snell's law, Bohr model, quantum wavefunctions"),
    ("combinatorics and discrete maths",
     "Pascal's triangle, Catalan numbers, Stirling numbers, "
     "partitions, Hasse diagrams, lattice paths, generating functions, "
     "graph colouring, MST, max flow, bipartite matching, topological sort"),
    ("topology and geometric topology",
     "Möbius strip, Klein bottle, torus, RP², fundamental polygons, "
     "knot diagrams, Reidemeister moves, Euler characteristic, "
     "covering spaces, fundamental group, homotopy"),
    ("group theory and abstract algebra",
     "Cayley tables, Cayley graphs, subgroup lattices, cosets, "
     "quotient groups, kernels and images, Galois lattice, "
     "rings, fields, permutation cycles"),
    ("real and functional analysis",
     "epsilon-delta continuity, uniform vs pointwise convergence, "
     "Cauchy sequences, intermediate value, mean value, Taylor remainder, "
     "Lipschitz, supremum/infimum, Heine-Borel, Banach spaces"),
    ("advanced linear algebra",
     "rank-nullity, QR, LU, eigendecomposition, SVD, "
     "Jordan form, spectral theorem, least squares, projections, "
     "Gram-Schmidt, change of basis"),
]


def _instantiate_parametric() -> list[str]:
    out: list[str] = []
    for template, params in PARAMETRIC_V5:
        for p in params:
            try:
                out.append(template.format(**p))
            except KeyError:
                continue
    return out


async def _generate_for_domain(
    domain: str, topics: str, seeds_sample: list[str], n: int,
    api_key: str, base_url: str, model: str, client: httpx.AsyncClient,
) -> list[str]:
    seeds_block = "\n".join(f"  - {s}" for s in seeds_sample)
    user_text = (
        f"Generate {n} DISTINCT user-questions for a 'live diagram tutor' "
        f"covering depth in: {domain}.\n"
        f"Sub-topics to span: {topics}.\n\n"
        f"Each question must:\n"
        f"  • request a SINGLE figure that can be drawn on a 2D canvas;\n"
        f"  • be specific (concrete numbers, named theorems, or "
        f"    specific objects rather than vague concepts);\n"
        f"  • cite a textbook style when natural — e.g. "
        f"    '(Bishop PRML)', '(Boyd CO 5.5)', '(Strang 4.2)', "
        f"    '(Oppenheim DSP)', '(Ahlfors complex analysis)', "
        f"    '(Bertsekas)', '(Munkres topology)', '(Dummit & Foote)';\n"
        f"  • be one sentence, ≤ 30 words.\n\n"
        f"DO NOT duplicate or paraphrase any of these existing prompts:\n"
        f"{seeds_block}\n\n"
        f"Return JSON {{\"prompts\": [...]}} with exactly {n} new prompts."
    )
    payload = {
        "model": model,
        "max_tokens": 6000,
        "temperature": 0.8,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content":
                "You write diverse, high-quality math/CS/physics "
                "visualisation prompts in the style of a teaching-focused "
                "tutor, drawing on the standard graduate textbooks."},
            {"role": "user", "content": user_text},
        ],
    }
    try:
        r = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload, timeout=120,
        )
        if r.status_code != 200:
            print(f"  ! {domain[:30]}: HTTP {r.status_code} {r.text[:150]}",
                  flush=True)
            return []
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        out = parsed.get("prompts") or []
        return [p.strip() for p in out if isinstance(p, str) and p.strip()]
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {domain[:30]}: {type(exc).__name__}: {exc}", flush=True)
        return []


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--base-url",
                    default=os.environ.get("SEVIM_VLLM_URL",
                                           "https://api.openai.com/v1"))
    ap.add_argument("--out-py", type=Path,
                    default=ROOT / "scripts" / "expanded_prompts_v5.py")
    ap.add_argument("--rounds-per-domain", type=int, default=2,
                    help="LLM rounds per specialist domain")
    ap.add_argument("--per-call", type=int, default=60,
                    help="Prompts per LLM call")
    ap.add_argument("--seeds-per-call", type=int, default=18,
                    help="Existing prompts to anti-dup against per call")
    ap.add_argument("--target-total", type=int, default=3500,
                    help="Stop adding LLM rounds once total ≥ target")
    args = ap.parse_args(argv)

    from service.secrets import bootstrap as _boot
    _boot()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 1

    # Start with what already exists.
    existing = list(PROMPTS_V4)
    print(f"  starting from PROMPTS_V4: {len(existing)} prompts")

    # Hand-curated additions.
    print(f"  hand-curated specialist prompts: {len(HANDCURATED_V5)}")
    # Parametric instantiations.
    parametric = _instantiate_parametric()
    print(f"  parametric instantiations: {len(parametric)}")

    pool = list(existing) + list(HANDCURATED_V5) + list(parametric)
    seen_lower = set()
    deduped: list[str] = []
    for p in pool:
        key = p.strip().lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        deduped.append(p.strip())
    print(f"  after dedupe of seed+curated+parametric: {len(deduped)}")

    # LLM rounds across specialist domains until target hit.
    rng = random.Random(42)
    async with httpx.AsyncClient() as client:
        for round_idx in range(args.rounds_per_domain):
            if len(deduped) >= args.target_total:
                break
            for domain, topics in SPECIALIST_DOMAINS:
                if len(deduped) >= args.target_total:
                    break
                seeds_sample = rng.sample(deduped,
                                          min(args.seeds_per_call,
                                              len(deduped)))
                got = await _generate_for_domain(
                    domain, topics, seeds_sample, args.per_call,
                    api_key, args.base_url, args.model, client)
                added = 0
                for p in got:
                    key = p.lower().strip()
                    if key in seen_lower:
                        continue
                    seen_lower.add(key)
                    deduped.append(p.strip())
                    added += 1
                print(f"  [round {round_idx+1}] {domain[:35]:35s}  "
                      f"+{added:3d}  total={len(deduped)}",
                      flush=True)

    # Write expanded_prompts_v5.py.
    args.out_py.parent.mkdir(parents=True, exist_ok=True)
    header = (
        '"""Round-2 v5 prompt pool — extends PROMPTS_V4 with specialist '
        'domain prompts.\n\n'
        f'  • starting v4 pool size: {len(PROMPTS_V4)}\n'
        f'  • hand-curated specialist additions: {len(HANDCURATED_V5)}\n'
        f'  • new parametric instantiations: {len(parametric)}\n'
        f'  • total after dedupe + LLM rounds: {len(deduped)}\n'
        '"""\n\n'
        'from __future__ import annotations\n\n'
        'PROMPTS_V5: list[str] = [\n'
    )
    body_lines = []
    for p in deduped:
        # Escape backslashes and double-quotes for a Python string literal.
        # Use repr to be safe across odd characters.
        body_lines.append(f"    {p!r},")
    footer = (
        '\n]\n\n\n'
        'if __name__ == "__main__":\n'
        '    print(f"{len(PROMPTS_V5)} prompts")\n'
    )
    args.out_py.write_text(header + "\n".join(body_lines) + footer)
    print(f"\n  wrote {args.out_py} with {len(deduped)} prompts")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
