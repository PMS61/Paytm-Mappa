# Computational Brutalism

## Locked Visual Theme Specification

**Status:** Locked
**Purpose:** Canonical visual direction for the AI/ML hackathon project
**Design principle:** Structured software disrupted by gravity.

------------------------------------------------------------------------

## 1. Creative Direction

Computational Brutalism combines editorial typography, high-contrast
software surfaces, computational data visualizations, and deliberately
deformed geometry.

The interface should feel like powerful analytical software whose visual
elements are being pulled, skewed, or displaced by an underlying force.
It is expressive but not chaotic; it is technical without looking like a
science-fiction prop; it is brutalist without making every interaction
hostile.

**The defining balance: 80% structure and whitespace, 20% visual
disruption.**

The geometry---not just the colors---must make the interface
recognizable.

### Experience keywords

-   Gravitational
-   Editorial
-   Computational
-   Tactile
-   Asymmetric
-   Precise
-   Restrained
-   High-contrast

### Avoid

-   Generic SaaS dashboards
-   Purple AI gradients and glowing orbs
-   Glassmorphism
-   Endless grids of identical rounded cards
-   Decorative AI sparkles
-   Randomly tilted components with no hierarchy
-   Excessive distressed textures, scribbles, and technical annotations
-   Fake metrics or decorative charts pretending to be real analysis

------------------------------------------------------------------------

## 2. Core Composition Rules

### The 80/20 rule

-   **80% structured:** readable layouts, consistent alignment, generous
    whitespace, stable controls, clear navigation.
-   **20% disrupted:** tilted panels, skewed silhouettes, clipped
    corners, displaced edges, selective grain, and striking data
    visualizations.

Treat this as a visual heuristic, not a literal pixel calculation.

### One dominant disruption per view

Every screen should have one main geometric event, supported by at most
one or two smaller disruptions.

Examples: - A large tilted visualization with otherwise stable
controls. - A broad, irregular results sheet overlapping a dark backing
plane. - One lime insight panel with a skewed edge. - A headline whose
surrounding frame is subtly displaced.

Do not tilt every panel. If everything is unstable, nothing feels
unstable.

### Editorial asymmetry

Use a consistent underlying grid, but vary the column widths and panel
proportions. Align important text and controls to the grid even when the
surrounding silhouettes are deformed.

Preferred composition: - A compact, stable navigation rail. - A large
editorial headline or task context. - One dominant data visualization. -
A spacious configuration panel. - A results area with a clear
hierarchy. - One selective accent panel for the most important finding.

Avoid making every region equally prominent.

------------------------------------------------------------------------

## 3. Geometry and Gravity

Geometry is the signature of the theme.

### Approved shapes

-   Slightly skewed quadrilaterals
-   Rectangles with clipped or uneven corners
-   Panels with a subtle rotation
-   Offset backing planes and hard shadows
-   Irregular polygonal insight sheets
-   Large cropped shapes extending beyond a panel boundary
-   Uneven border angles
-   Deformed frames around data visualizations

### Gravity behavior

Geometry should imply weight and direction: - Heavy, dark planes anchor
the composition. - Lighter panels appear to slide, lean, or drift around
those anchors. - A few edges align; others visibly resist the grid. -
The dominant shape should suggest a consistent direction of force within
a view. - Lime geometry marks an important action, finding, or active
state.

Prefer CSS transforms, `clip-path`, and SVG paths for controlled
deformation. Preserve rectangular hit targets and stable layout boxes
wherever possible.

### Restraint

-   Keep body copy horizontal and easy to read.
-   Do not distort form fields, labels, or navigation text.
-   Avoid overlapping interactive controls.
-   Never let decoration hide data or reduce click targets.
-   Do not use a different random angle for every element.
-   Keep mobile layouts mostly upright; use cropping and asymmetric
    spacing instead of excessive rotation.

------------------------------------------------------------------------

## 4. Color System

The theme must remain compelling in monochrome. Shape, contrast, type,
and composition carry the identity; color is a secondary signal.

  -----------------------------------------------------------------------
  Token                   Hex                     Role
  ----------------------- ----------------------- -----------------------
  Paper                   `#F0EDE3`               Primary light surface

  Ink                     `#171914`               Primary text, dark
                                                  planes, borders

  Signal lime             `#B9F438`               Primary action, active
                                                  state, key finding

  Graphite                `#77796F`               Secondary text, subtle
                                                  diagrams

  Blueprint blue          `#416A80`               Optional analytical
                                                  series or secondary
                                                  signal

  Warning orange          `#E76F3C`               Errors, warnings,
                                                  exceptional states

  Grid                    `#D3D0C5`               Quiet rules and
                                                  background grids
  -----------------------------------------------------------------------

### Usage rules

-   Paper and ink dominate.
-   Lime is selective. It is not a general-purpose background color.
-   Blue and orange have semantic roles; do not use them as decoration.
-   Maintain accessible text contrast.
-   Color must never be the only way a state or result is communicated.

------------------------------------------------------------------------

## 5. Typography

Use three functional typographic voices.

### Display

**Preferred:** Space Grotesk or a similar heavy, contemporary grotesk.

Use for: - Landing-page headlines - Major section headings - High-value
findings - Oversized numbers

Characteristics: bold, tight, editorial, confident. Avoid decorative
sci-fi fonts.

### Interface

**Preferred:** IBM Plex Sans or a comparable highly readable sans-serif.

Use for: - Navigation - Form labels - Explanatory copy - Buttons - Tabs
and controls

### Technical

**Preferred:** IBM Plex Mono or a comparable monospaced font.

Use for: - Dataset and model identifiers - Timestamps - Metric labels -
Coordinates and chart axes - Run status - Logs and provenance

Use uppercase micro-labels sparingly. Tiny text must remain legible and
should never carry essential instructions alone.

External fonts are optional; provide reliable local/system fallbacks.

------------------------------------------------------------------------

## 6. Material and Texture

Texture should establish material contrast, not fill empty space.

### Paper

-   Warm off-white surfaces
-   Very subtle grain
-   Sparse, fine graph lines where useful
-   Mostly clean reading and form areas

### Ink

-   Solid near-black surfaces
-   Crisp light typography
-   Selective monochrome halftone or grain
-   Strong contrast against paper

### Technical diagrams

-   Fine grid lines
-   Sparse crosshairs and coordinate ticks
-   Node fields, plotted points, connecting paths, or decision
    boundaries
-   Annotation labels only where they help explain real data

### Texture budget

Use texture in one or two major regions per view. Keep form areas, body
text, and dense result tables clean. Do not put grain, grid lines,
halftone, and scribbles on every surface.

Prefer lightweight CSS gradients, SVG patterns, and pseudo-elements.
Avoid heavy image filters and large decorative raster assets.

------------------------------------------------------------------------

## 7. Layout and Component Rules

### Navigation

-   Compact, stable, and easy to scan.
-   Clear active state, preferably using a lime block or strong ink
    treatment.
-   Do not deform labels or icons.
-   On small screens, collapse into an accessible menu or compact top
    navigation.

### Panels

Panels should vary by purpose rather than share one universal card
style.

Use a small set of structural types: 1. **Anchor plane:** dark,
high-contrast, visually heavy. 2. **Paper sheet:** primary readable work
surface, occasionally skewed or clipped. 3. **Data stage:** dominant
visualization area with technical plotting details. 4. **Signal panel:**
selective lime treatment for a key insight or primary action. 5. **Quiet
strip:** metadata, status, tabs, or secondary controls.

Use mostly square corners. Avoid pill-shaped cards and excessive rounded
containers. Borders should be intentional, not applied indiscriminately.

### Buttons

-   Rectangular and high contrast.
-   One obvious primary action per major task.
-   Clear labels and simple directional icons where useful.
-   Visible hover, focus, pressed, loading, success, and disabled
    states.
-   Hover transitions may shift the element slightly or change its
    border; keep motion fast and restrained.

### Inputs

-   Explicit, persistent labels.
-   Clear borders and focus indicators.
-   Stable, undistorted input surfaces.
-   Monospaced styling only for technical values and identifiers.
-   Proper validation and error messages.

### Data visualization

The visualization should reflect what the product actually computes.

Examples, depending on the task: - Embedding or cluster plot - Knowledge
graph - Feature-attribution chart - Prediction comparison - Model
decision boundary - Time series - Confusion matrix - Evidence/provenance
view

Use real data and honest labels. Do not add arbitrary charts or invented
metrics to make a screen look technical. Provide accessible labels and a
readable alternative when the chart conveys essential information.

------------------------------------------------------------------------

## 8. Landing Page

The landing page introduces the product's actual problem and capability,
not generic AI claims.

Recommended structure: 1. Compact brand/navigation header. 2. Large
editorial headline with asymmetric alignment. 3. One dominant geometric
data visualization or project-specific visual. 4. One concise
explanation and one primary action. 5. A short workflow sequence that
communicates how the product works. 6. A real product preview or
meaningful result. 7. A compact footer with version or project metadata
if relevant.

Avoid: - Centered headline plus three identical feature cards - Multiple
competing hero illustrations - Decorative dashboards filled with
fictional values - Large amounts of technical microcopy that add no
meaning

The first screen should have one clear focal point and enough whitespace
to let it dominate.

------------------------------------------------------------------------

## 9. Application Workspace

The working interface is more important than the marketing page. It must
feel like real analytical software.

Recommended hierarchy: - **Navigation:** stable and compact. -
**Context:** current project, dataset, model, or task. -
**Configuration:** inputs and options relevant to the operation. -
**Primary work area:** the visualization or output that best explains
the computation. - **Findings:** concise results supported by actual
evidence. - **Status:** real execution state, errors, and provenance.

Use asymmetric proportions to establish importance. Do not force the
workspace into a conventional sidebar-plus-equal-cards template.

Keep critical actions and controls stable even when surrounding panels
are skewed.

### Status language

Use clear text such as: - `SYSTEM / READY` - `MODEL / RUNNING` -
`DATA / VALIDATED` - `OUTPUT / REVIEW REQUIRED`

Show actual states only. Never report completion before the operation
completes or fabricate progress values.

### Loading and error states

Represent real stages where the backend exposes them. If progress is
unavailable, show an honest indeterminate state rather than inventing
percentages. Errors should be explicit, recoverable where possible, and
visually distinct from successful results.

------------------------------------------------------------------------

## 10. Motion and Interaction

Motion should reinforce gravity and computational feedback.

Allowed: - Small directional shifts on hover - Brief panel settling or
displacement transitions - A plotted point or graph updating when new
data arrives - A restrained indicator for a genuinely running
operation - Clear pressed and focus states

Avoid: - Constant decorative movement - Bounce-heavy easing - Parallax
that interferes with reading - Animations that delay access to results -
Simulated live data or fake processing

Honor `prefers-reduced-motion`. All functionality must remain available
without animation.

------------------------------------------------------------------------

## 11. Responsive Behavior

### Desktop

Use the full asymmetric composition. Let the primary visualization
occupy the most important region. Permit a small number of deliberate
overlaps or angled silhouettes.

### Tablet

Reduce overlap and simplify the geometry. Preserve the main hierarchy,
not every decorative detail.

### Mobile

Recompose rather than scale down: - Stack sections in task order. - Keep
text and interactive surfaces upright. - Reduce decorative
deformation. - Prevent horizontal overflow. - Keep the primary action
easy to reach. - Preserve readable chart labels and accessible controls.

Do not let visual effects compromise navigation, touch targets, or
content order.

------------------------------------------------------------------------

## 12. Accessibility and Engineering

-   Use semantic HTML and logical heading order.
-   Ensure keyboard navigation and visible focus.
-   Maintain adequate contrast and readable text sizes.
-   Give charts accessible names and summaries.
-   Pair color-coded states with text or icons.
-   Respect reduced-motion preferences.
-   Keep controls functional and connected to actual application
    behavior.
-   Preserve existing framework conventions and backend contracts.
-   Prefer reusable design tokens and components over one-off styling.
-   Avoid new dependencies unless they provide clear value.
-   Test responsive behavior, loading, empty, error, and success states.

### Suggested design tokens

``` css
:root {
  --color-paper: #F0EDE3;
  --color-ink: #171914;
  --color-signal: #B9F438;
  --color-graphite: #77796F;
  --color-blueprint: #416A80;
  --color-warning: #E76F3C;
  --color-grid: #D3D0C5;

  --border-strong: 2px solid var(--color-ink);
  --border-fine: 1px solid var(--color-grid);

  --font-display: "Space Grotesk", "Arial", sans-serif;
  --font-interface: "IBM Plex Sans", "Arial", sans-serif;
  --font-technical: "IBM Plex Mono", monospace;

  --space-unit: 4px;
}
```

### Example geometric treatment

``` css
/* Decorative geometry only; keep the interactive layout box stable. */
.panel--gravity {
  position: relative;
  isolation: isolate;
}

.panel--gravity::before {
  content: "";
  position: absolute;
  inset: 8px -7px -7px 8px;
  background: var(--color-ink);
  z-index: -1;
  clip-path: polygon(2% 0, 100% 4%, 96% 100%, 0 94%);
}

.panel--gravity .panel__surface {
  background: var(--color-paper);
  border: var(--border-strong);
  clip-path: polygon(0 2%, 98% 0, 100% 96%, 3% 100%);
}

@media (prefers-reduced-motion: no-preference) {
  .panel--gravity {
    transition: transform 160ms ease-out;
  }

  .panel--gravity:hover {
    transform: translate(-2px, -1px);
  }
}
```

Use clipped surfaces only when their content and interaction bounds
remain safe. A separate decorative pseudo-element is often preferable
for important controls.

------------------------------------------------------------------------

## 13. Quality Gate

Before accepting a screen, check every item:

-   [ ] It remains visually distinctive in grayscale.
-   [ ] There is one dominant geometric event, not visual noise
    everywhere.
-   [ ] Whitespace and hierarchy survive without accent colors.
-   [ ] Deformation suggests direction, weight, or importance.
-   [ ] Text, forms, navigation, and click targets remain stable.
-   [ ] The main visualization represents real product behavior.
-   [ ] No metrics, activity, or progress have been fabricated.
-   [ ] Texture is selective and does not impair readability.
-   [ ] The layout is deliberately recomposed for mobile.
-   [ ] Keyboard, focus, loading, empty, error, and success states work.
-   [ ] The core workflow is obvious without reading decorative
    annotations.

------------------------------------------------------------------------

## 14. Final Art-Direction Rule

**Make the structure precise. Let the geometry lose its balance.**

The product should look like a computational system exerting force on
its own interface: a few heavy planes, a clear analytical focal point,
and carefully chosen deformations. Keep the surrounding space quiet
enough for those decisions to matter.

This specification is the canonical visual direction. New screens and
components should follow it unless the product's actual workflow
provides a concrete reason to diverge.
