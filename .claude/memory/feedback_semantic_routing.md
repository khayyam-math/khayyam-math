---
name: feedback_semantic_routing
description: Route prompts to a template only when the SEMANTICS match exactly — syntactic/keyword similarity is not enough
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 99d729af-e760-4960-a041-8a1eccc50fb2
---

2026-06-17: user asked the live system for "the area of a sphere" and it returned the VOLUME figure — the gpt-4o-mini template router matched the token "sphere" and picked `volume_of_sphere` without distinguishing area (4πr²) from volume (4/3·πr³). User: "it simply matched it to the template without thinking. there may be questions which are syntactically similar but semantically different. we use the same template if the semantics are the same; exactly the same."

**Why:** keyword/syntactic overlap routes confidently to the wrong figure and ships a wrong answer; quantities that share a noun are NOT interchangeable.

**How to apply:** a template/route fires only when the requested QUANTITY/MEANING is exactly what it computes. Distinct, never-interchangeable quantities to guard: area / surface area vs volume; perimeter / circumference vs area; derivative vs integral. When building or reviewing any router/predicate, add an explicit negative guard for the semantically-near-but-different sibling, and prefer returning null (fall through) over substituting a same-keyword template.

**Fix shipped** (commit `fa58b0a`, deployed): `studio/templates/sphere_area.py` deterministic surface-area renderer (A=4πr², asserted worked example r=3→36π≈113.10, "not the volume" contrast box), routed BEFORE the template router via `is_sphere_surface_area_prompt` (fires on area/surface-area of a sphere, NOT when "volume" present), flag `SEVIM_SPHERE_AREA_ROUTE`. Also hardened `studio/templates/router.py` `_ROUTER_SYSTEM`: volume_of_sphere is volume-ONLY (return null for surface area) + a general Rule 5 "match on MEANING, not shared keywords." See [[project_svd_route_2026_06_15]], [[feedback_deterministic_routes_no_fallback]].
