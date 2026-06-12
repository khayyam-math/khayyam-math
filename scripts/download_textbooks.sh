#!/usr/bin/env bash
# Download legally-distributed textbook PDFs for the v4 distillation
# corpus.  Each book here has an explicit free-distribution route via
# the author's site, MIT OCW, or a CC-BY licence.
#
# Sources we DELIBERATELY skip (paid, no free PDF — citation-only):
#   * Sipser "Theory of Computation"
#   * Spivak / Apostol Calculus
#   * Concrete Mathematics (Knuth/Graham/Patashnik)
#   * Rudin "Principles of Mathematical Analysis"
#   * Munkres "Topology"
#   * Dummit & Foote "Abstract Algebra"
#   * Hardy & Wright "Theory of Numbers"
#   * Feller "Probability"
#
# Output: textbook PDFs in $TEXTBOOK_DIR (default ~/.local/share/sevim/textbooks)
#
# Usage:  scripts/download_textbooks.sh
#
# Idempotent: skips files already present.  Run again to fetch any
# additions to the URL list.

set -euo pipefail

TEXTBOOK_DIR="${TEXTBOOK_DIR:-$HOME/.local/share/sevim/textbooks}"
mkdir -p "$TEXTBOOK_DIR"
cd "$TEXTBOOK_DIR"

UA="Khayyam-Math-Distillation/1.0 (+https://khayyammath.com)"

# Each line: filename | url
# Verified URLs as of 2026-05-10.  If a URL rotates, the wget will
# fail with a 404 and we'll see it in the log — easy to fix.
URLS=(
  # Probability & ML
  "Murphy_PML_book1.pdf|https://github.com/probml/pml-book/releases/latest/download/book1.pdf"
  "Murphy_PML_book2.pdf|https://github.com/probml/pml2-book/releases/latest/download/book2.pdf"
  "Bishop_PRML.pdf|https://www.microsoft.com/en-us/research/uploads/prod/2006/01/Bishop-Pattern-Recognition-and-Machine-Learning-2006.pdf"
  "ISLR.pdf|https://www.statlearning.com/s/ISLRSeventhPrinting.pdf"
  "MML.pdf|https://mml-book.github.io/book/mml-book.pdf"
  # Optimisation & linear algebra
  "Boyd_Convex_Optimization.pdf|https://web.stanford.edu/~boyd/cvxbook/bv_cvxbook.pdf"
  "Boyd_VMLS.pdf|https://web.stanford.edu/~boyd/vmls/vmls.pdf"
  # Deep learning
  "DiveIntoDL.pdf|https://d2l.ai/d2l-en.pdf"
)

# Sources I tried but couldn't get direct PDFs from (URLs rotated /
# require account flow / are HTML-only):
#   * ESLII (Hastie/Tibshirani/Friedman) — author's site moved
#   * OpenStax — /details/ pages redirect to login
#   * Strang Linear Algebra — MIT site requires session

for entry in "${URLS[@]}"; do
    fname="${entry%%|*}"
    url="${entry##*|}"
    if [ -f "$fname" ]; then
        echo "[skip] $fname (already present, $(stat -c %s "$fname") bytes)"
        continue
    fi
    echo "[get]  $fname  ←  $url"
    if curl -fsSL --user-agent "$UA" -o "$fname" "$url"; then
        echo "[ok]   $fname  ($(stat -c %s "$fname") bytes)"
    else
        echo "[FAIL] $fname  — non-200 from $url"
        rm -f "$fname"  # don't leave a half-downloaded file
    fi
done

echo
echo "=== inventory ==="
ls -la "$TEXTBOOK_DIR" | grep -v '^d'
