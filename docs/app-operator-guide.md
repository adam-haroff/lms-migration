# Canvas Migration Workbench Operator Guide

Use this guide when you are deciding which app settings to use for a course.

The goal is to make the normal workflow predictable enough that you do not need to
re-decide the same settings every time.

## Default Recommendation

For most Sinclair Blueprint migrations, use this workflow:

1. build a Canvas-ready package from the D2L export
2. import the starter template into a clean Canvas course first
3. import the generated Canvas-ready package second
4. run `Canvas Cleanup + Audit`
5. capture a course snapshot
6. do course-specific cleanup and handoff notes

That is the safest default for courses that start with a clean Canvas shell.

## Quick Decision Table

### If the Canvas course is clean and does not have the starter template yet

Use:

- `Apply template styling overlay` = `On`
- `Use template asset alias map` = `On`
- `Apply template page merge` = `On`
- `Include starter template shell in generated package` = `Off`
- `Course already has starter template (reuse template assets after import)` = `On`
- `Import starter template first (for clean Canvas courses)` = `On`

Why:

- the course gets the official starter template directly from the template package
- the generated D2L conversion can then reuse the template structure and assets
- this avoids carrying a full duplicate starter shell inside the generated zip

### If the Canvas course already has the starter template in it

Use:

- `Apply template styling overlay` = `On`
- `Use template asset alias map` = `On`
- `Apply template page merge` = `On`
- `Include starter template shell in generated package` = `Off`
- `Course already has starter template (reuse template assets after import)` = `On`
- `Import starter template first (for clean Canvas courses)` = `Off`

Why:

- the live course already has the shell
- importing the template again would create avoidable duplicates

### If you intentionally want the generated zip to contain the full starter shell

Use only when you specifically want the generated package itself to carry the official
starter template shell.

Use:

- `Include starter template shell in generated package` = `On`
- `Course already has starter template (reuse template assets after import)` = `Off`

Do not combine those two settings. The app treats them as mutually exclusive because
they represent different workflows.

## Recommended Conversion Defaults

Unless the course gives you a specific reason to change them, use:

- `Conversion policy` = `strict`
- `Accordion handling` = `smart`
- `Accordion title align` = `left`
- `Image layout mode` = `preserve-wrap`
- `Math handling` = `preserve-semantic`
- `Intro/Checklist handling` = `rebuild-when-confident`
- `Learning Activities handling` = `preserve`

These are the current recommended defaults for Sinclair courses because they
preserve the original reading flow more faithfully while still staying within
the safer Canvas template structure. Use `safe-block` only when a course proves
that wrapped images are rendering badly in Canvas.

## Recommended Post-Import Defaults

Leave these on for most courses:

- `Apply Safe Fixes`
- `Use template alias map during auto-relink`
- `Apply template wrappers to assignments, discussions, and quizzes`
- `Fix template accessibility markup in Canvas content`
- `Organize Canvas files and prune empty folders`
- `Set the correct division Home Page as Front Page`

Why:

- these are the highest-value cleanup steps that consistently remove manual work after import

## What The Most Confusing Settings Actually Mean

### `Apply template styling overlay`

Use template-aware styling and asset mapping when converting D2L pages.

### `Use template asset alias map`

When the source refers to the wrong or older template file name, try to map it to the
correct approved template asset.

### `Apply template page merge`

Use Sinclair template page structure when the conversion has enough confidence to do so.

### `Include starter template shell in generated package`

Put the official starter-template modules/pages/resources directly into the generated
Canvas package.

### `Course already has starter template (reuse template assets after import)`

Assume the Canvas course will already contain the starter template. Generate the course
so it can reuse those live template assets instead of carrying a second copy.

### `Import starter template first (for clean Canvas courses)`

During upload, import the template package before importing the generated D2L conversion.
Use this for a clean Canvas course that does not already contain the template.

## Practical Stop Rules

Do not keep changing settings course by course unless the course actually proves the
default is wrong.

Change settings only when:

- the course contains unusual math content
- the course depends heavily on accordion behavior
- the course was built around a special template workflow
- a previous run showed a repeatable failure that a different setting fixes

If none of those apply, use the defaults and move on.
