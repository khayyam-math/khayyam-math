# `/learn/<topic>` long-tail content pages — design

## Why

The homepage ranks (at best) for one or two head queries about the *product*
itself ("AI math tutor", "Khayyam Math").  It cannot rank for the things
actual learners type into Google: *"how to draw a DFA"*, *"unit circle sin
cos values"*, *"matrix inverse 3x3 example"*.

For those, Google wants a permanent URL whose H1 + body text + structured
data tell it *this page is about that specific concept*.  The product
already produces the perfect artefact for these queries (a labelled SVG
with a worked-example narration); we just need to bake one per topic into
a crawlable HTML page, link them up, and put them in the sitemap.

A single content page is worth maybe 5–50 visitors a month from organic
search if it ranks; 15–20 pages compound into the project's
not-paying-for-traffic baseline.  This is the highest-ROI SEO move
available, and it does not require any ongoing budget.

## URL scheme

| URL                                | Page                                    |
| ---------------------------------- | --------------------------------------- |
| `/learn/`                          | Index of all topics, grouped by branch  |
| `/learn/<slug>`                    | One topic's worked example              |
| `/learn/<slug>/`                   | 301 → `/learn/<slug>` (canonical fixed) |

Slugs are kebab-case and frozen (URLs are forever — once shipped, never
rename).

## Page anatomy

```
┌─────────────────────────────────────────────────────────────┐
│ <head>                                                      │
│   per-topic title, description, canonical, OG/Twitter       │
│   JSON-LD: WebPage, BreadcrumbList, LearningResource, FAQ   │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Breadcrumb:  Home › Learn › <Topic Name>                    │
│ H1:          <Topic name>                                   │
│ Subtitle:    <one-line elevator pitch>                      │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────┐ │
│ │   pre-rendered inline SVG of the worked example         │ │
│ │   (indexable, accessible, no JS)                        │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ "Try this live"  →  /studio?prompt=<urlencoded>             │
├─────────────────────────────────────────────────────────────┤
│ H2:  What this shows                                        │
│ ~150 words — what the figure demonstrates                   │
├─────────────────────────────────────────────────────────────┤
│ H2:  Common applications  (or: When you'd use this)         │
│ ~100 words                                                  │
├─────────────────────────────────────────────────────────────┤
│ H2:  FAQ                                                    │
│ 3-5 Q&A pairs that mirror real student confusion            │
│ (Each becomes a FAQPage @Question/@Answer pair in JSON-LD)  │
├─────────────────────────────────────────────────────────────┤
│ H2:  Related topics                                         │
│ 3-5 internal links to other /learn/ pages (SEO juice)       │
└─────────────────────────────────────────────────────────────┘
```

All copy lives in the topic registry (next section).  No copy is
generated at build time — it is hand-curated so we never ship
factually-wrong educational content.

## Topic registry

A single YAML file, `service/learn/topics.yaml`, is the source of
truth.  One entry per topic:

```yaml
- slug: unit-circle
  branch: trigonometry            # for grouping on /learn/ index
  title: "The unit circle — sin, cos, and tan at standard angles"
  subtitle: "A single circle that pins down every trig value you'll need."
  prompt: "show the unit circle with sin and cos labelled at 30, 45, 60 degrees"
  meta_description: >
    The unit circle is the circle of radius 1 centred at the origin.
    See sin, cos, and tan values at the standard angles (30°, 45°, 60°)
    laid out on a single live diagram.
  body_what_this_shows: |
    The unit circle is the circle of radius 1 centred at the origin...
    (3-4 paragraphs, hand-written)
  body_applications: |
    Anywhere you see an angle that isn't on a right triangle...
    (2-3 paragraphs)
  faq:
    - q: "Why radius 1?"
      a: "Because then sin and cos are just the y and x coordinates..."
    - q: "What about angles past 90°?"
      a: "..."
    - q: "Do I have to memorise these?"
      a: "..."
  related:
    - pythagorean-theorem
    - trig-identities
    - radians-vs-degrees
```

The registry file is committed to the repo.  Topics are added by editing
the YAML — no code change.

## Pre-render pipeline

A standalone script `scripts/bake_learn_pages.py` walks the registry
and for each entry:

1. Calls the **express path** in-process with `prompt`, captures the
   resulting SVG + narration JSON.
2. Validates the SVG: parses as XML, has a `<svg>` root, ≥1 visible
   element, no overflowing viewBox.  Drops the topic if invalid.
3. Renders the page from `service/templates/learn_topic.html.j2`,
   inlining the SVG and the registry-supplied body copy.
4. Writes to `service/static/learn/<slug>.html`.

Run on demand (when the registry changes), not per-deploy.  The Docker
image just ships the baked HTML files as static assets.

Output is byte-stable as long as the registry doesn't change — no
floating freshness, no per-visit LLM cost.

## Routes (additions to `service/app.py`)

```python
@app.get("/learn/", include_in_schema=False)
def learn_index():
    """List all baked topics, grouped by branch."""
    ...

@app.get("/learn/{slug}", include_in_schema=False)
def learn_topic(slug: str):
    """Serve service/static/learn/<slug>.html if it exists, 404 otherwise."""
    ...
```

Both routes accept `GET` and `HEAD`, set
`Cache-Control: public, max-age=86400`.

## Sitemap integration

`sitemap_xml()` in `service/app.py` reads the registry and emits one
`<url>` per topic:

```xml
<url>
  <loc>https://khayyammath.com/learn/unit-circle</loc>
  <changefreq>monthly</changefreq>
  <priority>0.8</priority>
</url>
```

Plus the `/learn/` index at priority 0.6.

`robots.txt` allows `/learn/` and `/learn/*`.

## Structured data per page

Four JSON-LD blocks per `/learn/<slug>` page:

1. **WebPage** — basic page identity
2. **BreadcrumbList** — Home › Learn › Topic (Google shows breadcrumbs in
   search results)
3. **LearningResource** — Schema.org's first-class educational-content
   type.  `educationalLevel`, `learningResourceType: "WorkedExample"`,
   `inLanguage: "en"`, `isAccessibleForFree: true`
4. **FAQPage** — per-topic FAQ (already proven on the homepage)

## Seed list (15 topics, mix of curriculum + search volume)

| Branch          | Slug                       | Search-intent topic                          |
| --------------- | -------------------------- | -------------------------------------------- |
| Trigonometry    | `unit-circle`              | sin/cos at standard angles                   |
| Trigonometry    | `pythagorean-theorem`      | a² + b² = c²                                 |
| Geometry        | `triangle-area-heron`      | Heron's formula                              |
| Algebra         | `quadratic-formula`        | x = (−b ± √(b²−4ac))/2a                      |
| Algebra         | `polynomial-long-division` | step-by-step                                 |
| Linear algebra  | `matrix-inverse-3x3`       | adjugate / cofactor method                   |
| Linear algebra  | `vector-projection`        | proj_b(a)                                    |
| Linear algebra  | `eigenvalues-2x2`          | det(A − λI) = 0                              |
| Calculus        | `derivative-chain-rule`    | composition                                  |
| Calculus        | `integration-by-parts`     | ∫ u dv = uv − ∫ v du                         |
| Calculus        | `taylor-series-sin-x`      | f(x) = Σ fⁿ(0) xⁿ / n!                       |
| Discrete math   | `dfa-construction`         | DFA accepting (a\|b)* ending in `ab`         |
| Discrete math   | `graph-bfs-vs-dfs`         | traversal orders on a small graph            |
| Statistics      | `normal-distribution-68-95-99` | empirical rule                           |
| Statistics      | `binomial-pmf`             | P(X = k) = C(n,k) pᵏ (1−p)ⁿ⁻ᵏ                |

15 topics is enough to cover one branch each + 2-3 of the most-searched
algebra/geometry queries.  Add more by editing the YAML.

## Anti-spam / quality gate

A topic page is **only published** if:

* Registry copy is complete (no empty `body_what_this_shows`, no zero FAQs)
* SVG validates + has at least 1 path/shape
* H1 text matches the registry `title`
* Internal `related:` links all resolve to other published slugs

Failing a check drops the topic from the sitemap and the index page —
better to ship 12 great pages than 15 broken ones.

## Linking strategy

* **Homepage footer**: add an "Explore math topics" link → `/learn/`
* **Each topic page**: 3-5 internal links to `related:` topics
* **`/learn/` index**: links to every topic, grouped by branch
* **External**: keep the existing GitHub / Zenodo / LinkedIn links in
  the footer (no change)

## Roll-out

Phase 1 (this batch — after design approval):

* Scaffold registry, template, baker script, route, sitemap update
* Hand-write copy for 5 seed topics (`unit-circle`, `pythagorean-theorem`,
  `quadratic-formula`, `dfa-construction`, `matrix-inverse-3x3`)
* Bake, ship, verify in GSC

Phase 2 (follow-up):

* Hand-write copy for the remaining 10 topics
* Add a 16th: `omar-khayyam-cubic-roots` (geometric solution to cubic
  equations — namesake content + ranks for "Omar Khayyam math")

Phase 3 (organic growth):

* Look at GSC Search Console queries the existing pages bring in
* Add a new topic page per real-world query that's already drawing
  impressions

## What this design does NOT do

* No per-visit LLM cost (all pages are static)
* No CMS / database — the YAML registry IS the CMS
* No auto-generated copy (every body paragraph is human-written, so we
  cannot ship a hallucinated false claim about, say, Pythagoras)
* No user-generated content (no comments, no forum, no submission form)
* No A/B testing infra — write once, measure with GSC
