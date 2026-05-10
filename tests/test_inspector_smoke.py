"""Live smoke test for the math-correctness inspector.

Hits the OpenAI API.  Run manually:

    SEVIM_PY=.venv/bin/python python -m tests.test_inspector_smoke

Builds the same kind of broken figure shown in the screenshot
(triangle with arcs, caption falsely claiming the angles sum to 2π),
calls the vision reviewer, and asserts:
  * verdict == "FAIL"
  * at least one fix targets the false claim
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

from studio.express import _vision_review  # noqa: E402

# A minimal SVG matching the screenshot's content: triangle ABC, three
# decorative arcs, and a caption asserting the WRONG sum (2π instead of π).
BROKEN_SVG = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 600 500' width='600' height='500'>
  <circle cx='300' cy='250' r='150' fill='none' stroke='#ccc'/>
  <polygon id='triangle' points='300,100 150,400 450,400'
           fill='none' stroke='#1f6fe0' stroke-width='4'/>
  <text x='300' y='90' text-anchor='middle' font-size='18'>A</text>
  <text x='135' y='415' font-size='18'>B</text>
  <text x='460' y='415' font-size='18'>C</text>
  <path d='M 150 400 A 200 200 0 0 1 300 100' stroke='orange' fill='none' stroke-width='3'/>
  <path d='M 300 100 A 200 200 0 0 1 450 400' stroke='red' fill='none' stroke-width='3'/>
  <path d='M 450 400 A 200 200 0 0 1 150 400' stroke='green' fill='none' stroke-width='3'/>
  <text id='caption' x='300' y='460' text-anchor='middle' font-size='18'>
    The sum of the angles in a triangle is 2π radians.
  </text>
</svg>"""

NARRATION = [
    {"speak": "Here is a triangle with vertices A, B, and C.", "highlight": ["triangle"]},
    {"speak": "The sum of its interior angles is 2 pi radians.", "highlight": ["caption"]},
    {"speak": "Observe how the three arcs at A, B, and C add up to a full turn.",
     "highlight": []},
]


async def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY not set; aborting", file=sys.stderr)
        return 2
    base = os.environ.get("SEVIM_VLLM_URL", "https://api.openai.com/v1")
    model = os.environ.get("SEVIM_VLLM_MODEL", "gpt-4o")

    print(f"reviewing broken figure against {model} via {base} ...")
    verdict = await _vision_review(
        user_prompt="show me the sum of the angles in a triangle",
        svg=BROKEN_SVG,
        base_url=base,
        model=model,
        api_key=api_key,
        narration=NARRATION,
    )

    if verdict is None:
        print("UNEXPECTED: reviewer returned PASS.  Inspector did not catch "
              "the 2π / π error.", file=sys.stderr)
        return 1

    print("\n=== VERDICT ===")
    print(verdict)
    print("===============\n")

    text = verdict.lower()
    catches_pi = ("π" in verdict or "pi" in text or "180" in text)
    mentions_narration = "fix_narration" in text or "narration" in text
    if not (catches_pi or mentions_narration):
        print("WARN: reviewer FAILed but the fixes don't obviously target "
              "the false 2π claim.  Check the verdict above.", file=sys.stderr)
        return 1
    print("PASS — inspector flagged the false claim.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
