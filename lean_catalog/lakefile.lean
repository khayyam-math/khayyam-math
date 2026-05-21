-- lakefile.lean — Lake project for the offline Khayyam Math catalog
-- verifier (studio/catalog_verifier.py).
--
-- Depends on Mathlib4 to access norm_num / ring / decide tactics
-- and the special-functions library (Real.sin, Real.cos, …) needed
-- to formalise the math_claims the express LLM emits.
--
-- One-time setup:
--   cd lean_catalog
--   lake update   # downloads mathlib ~3 GB
--   lake build    # compiles ~30 min
--
-- After that, the catalog verifier writes Catalog/Probe.lean and
-- runs `lake env lean Catalog/Probe.lean` per claim (~1-5 s each).

import Lake
open Lake DSL

package «catalog» where
  -- Speed up the verifier by precompiling Mathlib deps
  precompileModules := false

@[default_target]
lean_lib «Catalog» where

require mathlib from git
  "https://github.com/leanprover-community/mathlib4.git" @ "v4.29.0"
