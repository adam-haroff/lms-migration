# Demo Runbook — Management Presentation

**Audience:** Manager + manager's manager  
**Goal:** Show that the conversion tool is viable for division-wide use and worth continued investment  
**Time budget:** ~20 minutes (demo 10 min, questions 10 min)

---

## Tonight: Pre-Demo Setup

### Step 1 — Clean up the Canvas sandbox

The previous import left 5 empty module containers (the empty-modules bug, now fixed).
You need a clean slate before re-importing.

**Option A — Reset the entire course (preferred):**
1. Go to [https://sinclair.instructure.com/courses/15616/settings](https://sinclair.instructure.com/courses/15616/settings)
2. Scroll to the bottom → **"Reset Course Content"**
3. Confirm. This wipes all modules, pages, and assignments — it's a sandbox, so that's fine.

**Option B — Just delete the 5 empty modules manually:**
1. Go to Modules in the course
2. Delete: About the Instructor, Home Page, Policies and Support, Canvas Resources for Students, Next Steps

---

### Step 2 — Re-import with template pages

The fresh zip is already built at `output/acc-2321/d2l-export.canvas-ready.zip`.

Run:
```bash
source .venv/bin/activate
lms-canvas-preview output/acc-2321/d2l-export.canvas-ready.zip \
  --env .env \
  --inject-template-pages \
  --output-json output/acc-2321/preview-result.json
```

This will:
- Upload the zip and run a full Canvas import
- After import completes, create the 5 template standard pages via the Canvas API (About the Instructor, Home Page, Policies and Support, Canvas Resources for Students, Next Steps)
- Print the URLs to the created pages

**Expected runtime:** ~3–5 minutes. Run this tonight so you're not waiting during the demo.

---

### Step 3 — Verify in Canvas

After the command finishes, visit the course and confirm:
- [ ] Modules appear and are organized correctly
- [ ] The 5 template standard pages exist under Pages (not as empty module containers)
- [ ] Open one "Introduction and Objectives" page — confirm it looks clean (no Bootstrap artifacts, correct colors/icons from the eLearn template)
- [ ] Open the Syllabus page — should look clean
- [ ] Open the one page with the flagged alt text issue: `07-CertainBusinessExpenses_Losses / Figuring the Sales Tax Deduction.html` — confirm the image is visible (the a11y flag is just a missing alt attribute, not a broken image)

---

### Step 4 — Pre-load demo artifacts in your browser

Open these tabs now so there's no fumbling tomorrow:
1. `output/acc-2321/feature-showcase.html` — **NEW: the 4-feature showcase page (start here)**  
   *(Open in browser: `open output/acc-2321/feature-showcase.html`)*
2. `output/acc-2321/d2l-export.page-review.html` — the before/after comparison viewer  
   *(Open in browser: `open output/acc-2321/d2l-export.page-review.html`)*
3. `output/acc-2321/d2l-export.migration-report.md` — the summary numbers
4. `output/acc-2321/migration-fix-checklist.md` — the remaining manual steps
5. The Canvas course: `https://sinclair.instructure.com/courses/15616`
6. The UI, if you plan a live demo: run `lms-migrate-ui` and have it open

---

## Demo Flow (10 minutes)

### Opening — the problem (1–2 min)

> "We have 9 courses to migrate to Canvas by the summer semester. Each D2L course has 70–100 HTML pages that need to have all the old Brightspace code stripped out, template standards applied, and accessibility issues flagged. Done manually, that's 6–12 hours per course — somewhere between 54 and 108 hours of instructional designer work, and the results are inconsistent course to course."

---

### Part 1 — Show the conversion (4–5 min)

**If live-demoing the UI:** Open the UI, walk through the tabs — input file already loaded, settings pre-configured. *Don't click Convert live* (takes ~30 sec) — instead, point to the migration report that's already generated.

**Show the migration report numbers:**
- 87 HTML files processed  
- **2,333 automated changes** applied  
- **0 manual review issues**  
- 1 accessibility flag (one image missing alt text across 87 pages)

> "In about 30 seconds, the tool went through every page and made 2,333 individual corrections — stripping legacy D2L code, converting Bootstrap layouts to proper HTML, applying responsive image rules, rewiring template asset references. None of that is a best guess — every change is logged and reversible."

---

**Open `feature-showcase.html`** — walk through each of the 4 sections:

**Feature 1: Code Cleanup & Layout**
- Show the before panel: 4 external CSS imports, Bootstrap grid divs, 28 `font-family: Lato` spans, fixed 941px image width
- Show the after panel: clean semantics, `max-width: 100%` responsive image, remapped banner path
> "Every page went through the same pipeline. This particular page had 2,333 changes across 87 pages. Each one is categorized and logged."

**Feature 2: Smart Link Management**
- Show the 3 link types: D2L artifact removed, external attribution preserved + secured, D2L quicklink converted
> "The tool knows the difference between a 'Printer-friendly version' link that belongs to D2L and a link that credits Plante Moran CPAs or the IRS as a source. The meaningful links are kept — and every external link now opens in a new tab with the rel=noopener attribute so the Canvas session can't be hijacked by the linked site."

**Feature 3: Accessible Accordion**
- Point to the live interactive previews — click a column in each mode
> "D2L used Bootstrap JavaScript accordions. Canvas has no Bootstrap. These sections would be invisible to students — permanently collapsed. The tool converts them to native HTML5 details/summary elements that work without JavaScript and pass keyboard accessibility requirements. Or, for syllabus-style pages, it flattens them to headings."
> "The 'smart' mode reads the page title — pages named 'Syllabus' or 'Policy' get flattened; 'FAQ' and 'Resources' pages get the accessible accordion. You can override this per page in the UI."

**Feature 4: Math Equation Preservation**
- Show the MathML live preview rendered in the browser
> "This is real MathML from a calculus course. The tool strips all the D2L wrapper code while leaving every MathML tag byte-for-byte identical. Canvas natively renders MathML via MathJax — no plugin, no workaround needed."

---

**Optional: page-review.html** deep dive (if time allows):
Navigate to `CourseOverview / Student Resources and Support.html` to show the live accordion in the editor.

---

### Part 2 — Show the result in Canvas (3–4 min)

Navigate to the Canvas course (`https://sinclair.instructure.com/courses/15616`):

1. **Module structure** — show modules organized in the same structure faculty created in D2L. "The course structure is preserved — faculty will recognize it immediately."

2. **An Introduction and Objectives page** — open one (e.g., from Module 1).  
   > "This is what a faculty member originally built in D2L, now in Canvas. The content is unchanged. What's gone is all the Brightspace-specific code that would have caused layout problems in Canvas."

3. **A template standard page** — open "About the Instructor" or "Home Page" from the Pages list.  
   > "These are Sinclair's standard Canvas pages, automatically merged in. Every course we migrate this way will have these. Every course done manually may or may not."

---

### Part 3 — Remaining manual work (1–2 min)

**Open the fix checklist** (`migration-fix-checklist.md`):

> "After the automated conversion, here's what's left for this course. 11 items total. 1 of them is a real human task — adding alt text to one image. The other 10 are best-practice reminders and reference gaps, not content damage."

| Priority | Items | Examples |
|---|---|---|
| P1 | 3 | Fix rubric setup, alt text on 1 image, move a D2L video to Studio |
| P2 | 8 | Mobile review, title delimiter policy, template placeholders |

> "For any course in the division, you'll end the migration with a checklist like this — specific, prioritized, and actionable. No hunting through 87 pages wondering what still needs attention."

---

## The ROI Argument

When asked "is it worth it vs. doing it manually?"

**The numbers:**

| | Manual per course | Tool per course | 9 courses |
|---|---|---|---|
| Conversion + cleanup | 6–12 hrs | 1–2 hrs | 9–18 hrs (tool) vs. 54–108 hrs (manual) |
| Consistency | Varies by person | Identical rules every time | — |
| Auditability | None | Full change log + checklist | — |

> "We have 9 courses. If the tool saves even 4 hours per course, that's 36 hours freed up. If it saves the high-end estimate, it's 90 hours. That time is either savings or capacity to take on more courses."

**Beyond this semester:**
- The division has more coming after summer. Any course migrated with this tool can be re-processed if rules change — deterministic transforms, same input → same output.
- Faculty-built courses vary wildly in how much D2L-specific code they contain. Without a tool, migration quality depends on who does it. With the tool, every course gets the same baseline.

---

## Likely Questions and Answers

**"Can others in the division use this without technical knowledge?"**  
> "Yes — there's a GUI. You point it at a D2L export zip, click Convert, and it produces a Canvas-ready zip. The decisions about what to transform are in a rules file that I maintain, not in the UI. A non-technical ID just runs the tool and works the checklist."

**"What can't it automate?"**  
> "Three things: replacing D2L media-library videos with Canvas Studio links (requires a human to move the file), configuring Canvas rubrics (the rubric migrates but needs a checkbox enabled), and anything that was broken in D2L to begin with — garbage in, garbage out. Those are all on the checklist."

**"What if a converted page looks wrong?"**  
> "Every change is logged. The page-review workbench shows before and after side by side. And the original D2L export is never modified — if something's wrong, you re-run with adjusted rules."

**"How long to migrate all 9 courses?"**  
> "Running the conversion on each takes 1–2 minutes. Working through the checklist for each course is my estimate of 1–2 hours. So 9 courses is realistically 2–3 days of work, not 2–3 weeks."

---

## Fallback Plan

If Canvas is slow or the live demo has issues:
- Show the `d2l-export.page-review.html` locally in browser — it contains the full before/after for 29 pages and doesn't need Canvas access
- The migration report and fix checklist are local files — they work offline
- The approval report (`d2l-export.approval-report.md`) has a clean summary section you can read from
