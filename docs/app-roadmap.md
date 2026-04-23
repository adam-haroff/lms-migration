# App + Reviewer UI Roadmap

## Phase 0: Pilot tooling (now)

- CLI processing pipeline on local workstation.
- Rule file controls transformations and manual-review triggers.
- Reports drive deterministic review workflow.

## Phase 1: Institutional rulepack

- Move `rules/default_rules.json` to a versioned institutional rulepack.
- Add change control: owner, effective date, approval notes.
- Add per-department overrides when needed.

## Phase 2: Reviewer web UI

- Upload export package.
- Show grouped issues by module/page type.
- Allow reviewer decisions (`accept`, `replace`, `skip`) with audit trail.
- Rebuild package after reviewer decisions.

## Phase 3: Integration and automation

- Pull best-practices source from SharePoint/Teams export.
- Scheduled audit for drift/conflicts.
- Optional API integration with Canvas for post-import validation.

## Data model recommendation

- `rules`: transformation + validation rules with version IDs.
- `findings`: issue records with severity and auto/manual status.
- `decisions`: reviewer actions tied to findings and user identity.
- `runs`: immutable migration run metadata and artifacts.

## Phase 4: Post-migration automation (from coworker feedback, 2026-03-18)

Items sourced from documented manual pain points gathered from the migration team.
Priority order reflects frequency and full-automation potential.

- **Gradebook group + drop rules** — Read D2L category XML (drop-lowest/drop-highest
  settings) and recreate as Canvas assignment groups with correct drop configurations.
  Full automation possible; direct API mapping.

- **Prerequisite gating (syllabus quiz)** — Detect D2L "Not in Gradebook" flag and
  recreate assignment in Canvas with "Do not count towards final grade" checked while
  preserving point value for module prerequisite logic.

- **Extra credit / bonus assignments** — Detect D2L bonus flag → 0-point Canvas
  assignment placed in an appropriate group with a capping rubric. Flag for instructor
  verification.

- **Rubric migration and configuration** — Detect when D2L rubric ratings represent
  ranges rather than fixed values and enable the Range checkbox in Canvas. Auto-attach
  rubrics to corresponding assignments. Verify point totals match and flag discrepancies.

- **Faculty-only content** — Detect role-restricted D2L content; recreate as unpublished
  Canvas pages with `[FACULTY]` naming convention and an "INSTRUCTOR ONLY" warning header.

- **Discussion / assignment submission types** — Detect graded D2L content pages and
  recreate as Canvas Assignments (pages cannot be graded). Flag email-based workflows for
  conversion to online submission assignments with correct submission type.

- **LTI tool references** — Detect Panopto/Studio embeds and LTI links; map to Canvas
  equivalents using a configurable org-level lookup table; flag unresolvable references.

- **Panopto quickLink iframe conversion** — Some D2L courses embed Panopto videos as
  D2L quickLink LTI iframes inside HTML pages instead of using direct Panopto embed
  markup. The export preserves the D2L `rCode`, LTI XML, title, and Panopto launch
  metadata, but not a clean Canvas-ready `Embed.aspx?id=...` URL. Add a conversion step
  that inventories every Panopto quickLink in page HTML, accepts a resolver table
  (`rCode` → Panopto embed URL or UUID), and rewrites those iframes into the standard
  responsive Panopto embed wrapper during package generation or page-replacement export.

- **Blueprint-specific audit** — Before sync: flag ghost records in discussions (deleted
  replies), verify unpublished page state won't be overwritten, confirm gradebook
  structure and module prerequisites survive sync to child courses.

- **Item bank sharing** — After migration to Blueprint, auto-share all item banks at the
  course level so any enrolled instructor has edit access without manual per-bank sharing.

- **Accessibility (image alt text + post-import check)** — Improve round-trip alt text
  preservation. Run Canvas a11y checker via API post-import and surface results in the
  migration report.

---

## Phase 4 additions (from external research + codebase gap audit, 2026-03-21)

Items below were identified by cross-referencing (a) universally-reported D2L→Canvas
migration failure points, (b) codebase audit of what is and is not yet implemented,
and (c) the lms-migration-custom-instructions-draft and pdf-best-practices docs.
Confirmed non-redundant against existing pipeline capabilities before adding.

Each item feeds the existing `fix_checklist.py` instruction engine — every detected
condition emits a checklist item with `priority`, `owner`, `description`, and a
step-by-step `action` field that appears in the generated Markdown and CSV reports.

- **New Quizzes settings audit (pre-import)** — All quizzes are required to be Canvas
  New Quizzes. D2L QTI exports through Canvas import but loses per-quiz settings:
  timing limits, attempt counts, availability windows, and question randomization require
  manual re-entry. Parse D2L gradebook/quiz XML pre-import to produce a per-quiz
  settings inventory (name, time limit, attempts, shuffling, availability window).
  The fix checklist emits a P1 item per quiz with specific instructions for recreating
  each setting in New Quizzes — replacing the current unconditional boilerplate reminder.

- **New Quizzes compatibility review (per-quiz)** — Certain D2L question types behave
  differently or are unsupported in Canvas New Quizzes (e.g., ordering, multi-select
  with partial credit, calculated formula questions). Parse QTI XML to inventory question
  types per quiz and flag quizzes that contain at-risk types, with a P1 checklist item
  describing how to rebuild or substitute in New Quizzes before release.

- **Assignment / quiz / discussion availability window audit** — Due dates and
  availability windows are stored in D2L manifest and grade item XML, not in HTML.
  Nothing currently reads or reports on them. Parse these pre-import and emit a
  per-item checklist entry for every date-bearing object, giving the ID the exact
  original windows so they can be re-entered in Canvas. Feeds directly into the Canvas
  import date-shift workflow.

- **Course start-date and global date-shift report** — Canvas's import tool offers a
  bulk date shift, but requires knowing the original course start date. Read the D2L
  course offering XML for the official start date, surface it in the preflight report,
  and emit a P1 checklist item with exact Canvas import date-shift instructions. Also
  list every item that has explicit date windows so the ID can verify them after shift.

- **D2L media library URL detection (distinct category)** — The existing `/d2l/` link
  neutralizer catches media library URLs incidentally but treats them as generic
  "D2L link needs review." Media library URLs (patterns: `ouFileId=`,
  `/d2l/lp/media/`, `/d2l/tools/mediaLibrary/`) point to files that will be missing
  after import. These must be identified as a separate P1 category with a checklist
  action: move the file to Canvas Studio or course Files, update the embed/link.

- **Graded discussion detection** — D2L graded discussions sometimes import into
  Canvas as ungraded discussions, silently losing the gradebook connection. Parse
  `*_d2l.xml` discussion XML for grade category associations; emit a P1 checklist item
  per affected discussion with instructions to enable grading and reconnect to the
  gradebook in Canvas.

---

## Phase 4 additions (from BIS 1400 + ACC 2321 post-import cleanup, 2026-04-08)

These came from live-course cleanup after import, not just pre-import analysis. The
goal is to reduce the manual Canvas-side cleanup that still happened even when the
package imported successfully.

- **Canonical starter-template asset mapping** — Stop treating starter-template icons,
  banners, and shared support files as disposable package-local duplicates when a full
  starter shell is already being injected. Add a canonical asset manifest for the
  approved template course and rewrite generated page HTML to that one approved asset
  set. This should prevent duplicate `template-images`, duplicate `course-card` files,
  and later broken references caused by deleting the wrong copy in Canvas Files.

- **Clean output file structure matching live Blueprint practice** — Add a post-build
  package organization pass so generated files land in a predictable Sinclair-style
  layout from the start (for example `template-images`, `course_image`, course-specific
  content folders, and PowerPoints) instead of mixing template artifacts, D2L remnants,
  and generated content under `web_resources/` and scattered import folders. Preserve
  path safety, but emit the cleaner final structure by default when no collisions exist.

- **Template-aware description injection for assignments, discussions, and quizzes** —
  Extend the existing page templating logic so supported D2L discussions, assignments,
  and quiz descriptions are converted directly into the Canvas template wrappers during
  package generation. This should eliminate the current manual copy/paste step from
  generated snippets into the live course for common overview/instructions/technical
  support blocks.

- **Post-import duplicate/orphan audit** — Add an API-backed validation pass that flags:
  duplicate modules from accidental re-imports, duplicate pages by title/slug,
  published-but-unlinked pages, empty folders after file cleanup, duplicate template
  assets where only one is referenced, and legacy D2L carryover content that is still
  published. This should generate a cleanup report before manual review begins.

- **Course-image and template reserve handling** — Add a rule for course image files
  and template reserve assets (alternate banners, optional sample images) so the tool
  distinguishes between "unused but intentionally kept" and "safe to delete". This
  avoids over-cleaning template reserves while still identifying real leftovers.

- **Template shell course-number hydration** — When starting from the clean Canvas
  starter template, hydrate course-specific links and known template references with the
  destination course number before downstream content injection. Generated HTML should
  then reuse those resolved references consistently instead of introducing a second
  layer of template-file IDs later.

- **T-prefixed template shell naming for example items** — When starter-template
  example pages, assignments, discussions, and quizzes are intentionally retained in a
  migrated course for faculty reference, rename them consistently with a `T` marker so
  they are clearly distinguishable from real course content. Examples:
  `Module T1: Introduction and Checklist`, `Module T1: Assignment [Title Here]`,
  `Module T1: Discussion [Title Here]`, and similar course-conclusion/template items.
  This should make template leftovers easier to identify, discuss with faculty, and
  remove during final cleanup.

- **Unique manifest/resource namespaces for saved-LOR recovery imports** — The
  ad hoc saved-LOR recovery builder currently reuses fixed manifest, item, and
  resource identifiers across separate recovery packages. When multiple recovery zips
  are imported into the same Canvas course, Canvas can treat later imports as updates
  to earlier imported objects instead of distinct new pages. Add a package-specific
  namespace or deterministic unique suffix to the manifest identifier and all generated
  item/resource IDs so repeated recovery imports do not overwrite one another.

- **Section-level Introduction/Objectives → Introduction/Checklist synthesis** —
  Some D2L courses, including MAT 0200, use a section landing-page pattern where the
  first page in the module is a checklist/overview page and the second page is
  `Introduction and Objectives`. The migration should automatically:
  1. rename the second page to `Introduction and Checklist`,
  2. preserve the Introduction text from the original page,
  3. carry forward `Learning Objectives` into the `Module Objectives` section even when
     the source heading is not already template-shaped, and
  4. pull the checklist items from the module's first overview/checklist page into the
     `Module Checklist` section.
  This should prevent the current failure mode where the Canvas page keeps the old
  `Introduction and Objectives` title, loses the objectives list, and falls back to a
  generic checklist instead of the section-specific checklist from D2L.

- **Semantic HTML simplification pass for page bodies** — The current pipeline still
  preserves too many layout wrappers and inline typography styles from D2L, especially
  on content pages like MAT 0200 video lessons. Add a late normalization pass that:
  1. unwraps non-semantic `div`/`span` wrappers when they do not carry meaningful
     structure,
  2. removes inline `font-size`, `font-family`, and similar text-presentational styles
     from ordinary paragraphs and headings so Canvas theme defaults can apply,
  3. keeps only high-value inline styles such as image alignment, intentional table
     cell emphasis, and canonical template divider/header treatments, and
  4. prefers semantic blocks (`h2`/`h3`, `p`, `ul`/`ol`, `table`, `figure`) over
     deeply nested wrapper markup.
  The goal is to preserve structure and accessibility without carrying forward D2L's
  presentational HTML debt or overriding Canvas's default handling of headings and
  paragraph text.

- **Equation-image quiz stem conversion for math courses** — Some D2L quizzes,
  including MAT 0200, store the entire question stem as an image of an equation
  instead of text or MathML. After Canvas import, these stems can remain image-only
  or break entirely if the file reference fails. Add a math-course recovery pass that:
  1. inventories New Quiz items whose `item_body` is only an image,
  2. attempts to recover a semantic text or MathML version from source data,
  3. supports a reviewer-assisted transcription map when no hidden equation text
     exists in the export, and
  4. patches the live New Quiz item bodies through the New Quizzes API with
     accessible HTML math.
  The goal is to eliminate inaccessible image-only equation stems and avoid the
  current manual transcription path for math-heavy courses.

- **Course-file reachability before pruning / relink neutralization** — MAT 0200
  exposed a failure mode where page bodies still referenced valid D2L course files
  (for example `../Notes and Handouts/*.pdf` in Video Lessons pages), but the later
  pipeline stages neutralized those links to `href="#"` before package pruning and
  post-import relinking were complete. That caused the actual PDFs to be dropped from
  the canvas-ready package and left the live Canvas pages with broken placeholder links.
  Fix this by:
  1. computing file reachability from the original pre-neutralized local href/src values,
  2. preserving all D2L course files referenced through `data-migration-original-href`
     or equivalent migration metadata,
  3. only pruning after that course-file graph is stable, and
  4. teaching the post-import relinker to restore course-file links from those preserved
     original paths instead of leaving them as unresolved `#` placeholders.

## Phase 4 additions (from MAT 0200 + MAT 0470 post-import cleanup, 2026-04-21)

These items came from repeated live-course cleanup after import, especially in math
courses where New Quizzes, template pages, and course files still needed targeted
Canvas-side repair after the package imported successfully.

- **New Quizzes asset reconciliation after import** — MAT 0200 exposed a separate
  failure mode from ordinary page/file relinking: some quiz image assets were not
  available in Canvas after import even though the D2L source still had the files.
  Add a post-import API step that:
  1. inventories New Quiz item bodies for local image/file references,
  2. compares those references against live Canvas Files,
  3. uploads any missing source assets from the original D2L package into a canonical
     course folder (preferably `course-content/course-images` for reusable quiz images),
  4. rewrites the live New Quiz item bodies to the canonical Canvas file URLs, and
  5. emits a repair report so the reviewer can see what was restored automatically.
  This should prevent the current manual recovery path for missing quiz images/files
  that do not participate cleanly in the normal package-manifest relink flow.

- **Template-page accessibility preset pack (Canvas API post-import)** — The generic
  accessibility fixer now handles many common template issues, but MAT 0200 and
  MAT 0470 still surfaced residual Canvas-checker findings on live template pages
  such as syllabus table contrast issues, nested span color conflicts, and stray
  heading-level problems in template reference pages. Add a template-specific
  post-import repair pass that:
  1. targets known Sinclair template pages by title/pattern,
  2. applies the verified heading/color/contrast fixes that have already worked in
     live courses,
  3. preserves intentional visual treatments such as white text on black table headers
     where that is the approved design, and
  4. runs as part of the existing Canvas cleanup workflow so template pages are clean
     in the Canvas Accessibility Checker immediately after upload.

- **Pattern-driven bulk Canvas API operations** — Several of the highest-value time
  savings in recent courses came from one-off API scripts that updated multiple pages,
  assignments, or quizzes at once after the reviewer identified a clear pattern.
  Formalize those into reusable app features, including:
  1. bulk page body replacement by title/path pattern,
  2. bulk assignment setting updates by naming convention or module membership,
  3. bulk quiz item/body updates by quiz title and item pattern,
  4. bulk publish/unpublish and safe cleanup helpers, and
  5. bulk module/header scaffolding from a simple CSV/JSON input.
  The goal is to turn repeated ad hoc Canvas API fixes into supported reviewer tools
  instead of requiring course-specific scripts each time.

- **Equation-image transcription assistant (LaTeX first, MathML optional)** — Math
  courses still encounter quiz and page content where equations exist only as images.
  Add an assisted workflow that:
  1. inventories equation-only images in pages and New Quiz items,
  2. attempts OCR/transcription into a reviewer-editable LaTeX candidate,
  3. optionally converts approved LaTeX into MathML for Canvas insertion,
  4. supports patching either package HTML or live Canvas/New Quiz bodies, and
  5. keeps the original image as fallback evidence until the reviewer approves the
     semantic replacement.
  The primary output should be trustworthy LaTeX first; MathML should remain an
  optional derived format rather than the only representation.

- **Canvas API opportunity inventory for reviewer acceleration** — Start a formal
  catalog of safe, repeatable post-import API actions that can save reviewer time.
  Candidate operations from recent migrations include:
  1. creating module shells and repeated headers/checklists in bulk,
  2. replacing or standardizing assignment/discussion/quiz descriptions at scale,
  3. applying consistent naming conventions across modules, pages, and assignments,
  4. mass-updating submission settings, point values, and due-date scaffolds,
  5. rewriting broken file links to Canvas preview links in bulk, and
  6. generating live inventories such as instructor notes, unused pages, and cleanup
     candidates directly from the course API.
  This should become a small library of trusted operations that the reviewer can run
  from the app without needing custom scripts for each course.

- **Post-import checklist title sync to final Canvas item names** — Generated
  `Introduction and Checklist` pages can carry the correct checklist structure before
  upload, but the final page/assignment/discussion names sometimes still change after
  the reviewer or course coordinator adjusts the live module. Add a post-import API
  step that:
  1. reads the live module item titles in Canvas,
  2. matches each `Module Checklist` item to its corresponding page/assignment/discussion
     when the mapping is unambiguous,
  3. rewrites checklist labels to the final live Canvas names, and
  4. leaves ambiguous items untouched for reviewer confirmation.
  This should avoid stale checklist wording without forcing the package builder to
  guess final names before the course structure is settled.

- **Post-import import-artifact cleanup for package source files** — The import
  package still needs HTML/XML/manifest files in order for Canvas to create pages,
  modules, quizzes, and settings, but many of those source artifacts do not need to
  remain in the live course Files area afterward. Add a post-import API cleanup step
  that can identify and optionally remove:
  1. package-source HTML files that were only used to create Canvas pages,
  2. manifest / metadata XML files and folders that are not student-facing assets,
  3. other known import-only cartridge artifacts, while
  4. explicitly preserving real downloadable course files and anything still linked
     from page bodies, assignments, discussions, quizzes, or modules.
  This should be conservative by default and produce a reviewable deletion plan
  before removing anything live.

- **Operator-facing UI language cleanup + embedded workflow help** — The app wording
  has grown around internal migration terminology and is now too dependent on expert
  interpretation. Add a user-facing terminology pass that:
  1. replaces internal labels with clearer operator language,
  2. groups settings by the real decisions an ID/CC is making,
  3. adds inline help for the most confusing toggles,
  4. provides built-in workflow presets such as "clean Canvas course" vs "template
     already present", and
  5. keeps the UI language aligned with the operator guide so reviewers do not have
     to ask for the recommended settings on each course.

- **Overall UI/UX redesign with responsive layout** — Beyond terminology cleanup, the
  app still needs a stronger operator experience. Improve the interface so it is more
  usable for people beyond the current primary operator by:
  1. modernizing the overall layout and visual hierarchy,
  2. reducing dense option clusters and surfacing the most important decisions first,
  3. improving spacing, readability, and sectioning across the full workflow,
  4. making the interface resilient at smaller window sizes instead of assuming a large
     desktop layout at all times, and
  5. treating responsive behavior as a first-class requirement for both the main app
     interface and the page-review workbench.
  The goal is not just clearer labels, but a noticeably better operator experience.

- **Page Review workbench usability improvements** — The page review interface is
  already useful, but it still takes too much interpretation for routine reviewer
  work. Continue improving it with:
  1. clearer reviewer-facing language instead of internal scoring terminology,
  2. better triage grouping for the highest-value pages first,
  3. easier side-by-side comparison and approval flows,
  4. stronger cues for what changed automatically versus what still needs a person,
  5. simpler filtering for common reviewer tasks, and
  6. documentation/help that matches how IDs and CCs actually use the workbench.
  The goal is to make page review faster, more legible, and less dependent on expert
  interpretation.

### Recently completed before MAT 0470 (2026-04-16)

- Implemented unique manifest/resource namespaces for saved-LOR recovery imports so
  multiple recovery packages do not overwrite one another in Canvas.
- Implemented section-level `Introduction and Checklist` title synthesis in the
  template merge flow and synced the corrected title into the generated manifest.
- Implemented a conservative semantic HTML simplification pass that strips ordinary
  text typography styles and unwraps now-meaningless spans.
- Extended the template accessibility fixer to handle black header cells with nested
  black text overrides and styled heading blocks with conflicting descendant colors.
- Implemented course-file reachability retention from `data-migration-original-href`
  so package pruning no longer drops files that the post-import relinker can recover.

---

## Non-negotiable engineering controls

- Deterministic transforms (same input + rules => same output).
- Full run artifact retention.
- Idempotent re-runs.
- Signed releases for rulepack versions.
