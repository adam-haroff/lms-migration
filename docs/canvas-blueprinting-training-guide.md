# Canvas Blueprinting Training Guide

Last updated: `2026-05-03`

This guide is for practical Canvas Blueprint use at Sinclair, from basic setup through higher-risk sync scenarios.

It is based on:
- official Instructure Blueprint documentation
- the local migration workflow used for `COM 2220`, `MAT 0200`, `MAT 0470`, and `PSY 2180`
- repeated cleanup issues observed during summer-course migrations

## What a Blueprint Course Is

A Blueprint course is a master course that syncs content and selected settings to one or more associated Canvas courses.

Use a Blueprint when you need:
- a shared course structure across multiple teaching sections
- centralized updates to common content
- controlled consistency for pages, assignments, discussions, quizzes, files, modules, and some course settings

Do not treat a Blueprint as:
- a live teaching shell
- a place for section-specific dates unless you intend to lock and control those dates centrally
- a substitute for every local course customization

## What Syncs and What Does Not

At a high level:
- Blueprint syncs course content and many course settings.
- Locked attributes on locked objects overwrite associated courses.
- Unlocked Blueprint content can be edited in associated courses without being overwritten by later Blueprint syncs.
- New content created only in an associated course is not part of the Blueprint relationship.

Examples that do sync:
- homepage selection
- course navigation
- assignment groups and weights
- module requirements and prerequisites
- many assignment, discussion, page, quiz, and file attributes
- grading scheme enablement and grading scheme selection

Examples that do not fully sync or require caution:
- course name
- course code
- term
- SIS ID
- course format
- some quiz feedback timing options
- some gradebook display preferences
- section enrollments and section structure

Important New Quizzes note:
- Item banks associated with a New Quiz sync with the quiz.
- Item banks not associated with a New Quiz do not simply appear as normal course item banks in the associated course.

## Core Blueprint Rules

1. Association creates an initial sync.
2. Later changes must be synced manually.
3. All course content is included in a sync, even if not locked.
4. Locked attributes are the part that actively overwrite associated courses.
5. Modules themselves are not “locked” like assignments/pages, but Blueprint sync still controls module structure behavior.

## Basic Scenarios

### Scenario 1: One Blueprint, many teaching sections

Use this when:
- the CC wants all sections to share the same pages, modules, assignments, and quizzes
- instructors should not rebuild the course structure from scratch

Recommended setup:
- keep course structure, navigation, core pages, and assignment shells in the Blueprint
- leave section-specific dates unlocked unless the CC truly wants centralized date control
- keep section-specific announcements and instructor bio content local to associated courses unless they should be standardized

Best practice:
- sync after meaningful batches, not after every tiny edit
- include a clear sync message so instructors can see what changed

### Scenario 2: Blueprint with local instructor customization

Use this when:
- the department wants a shared baseline but instructors need some freedom

Recommended lock strategy:
- lock content for pages that must remain standardized
- do not lock items that instructors need to adapt heavily
- be deliberate about whether points, due dates, and availability dates should be locked

Practical example:
- standardize the syllabus shell, module landing pages, quiz instructions, and assignment shells
- leave room for instructors to add local announcements, optional resources, or instructor-specific examples

### Scenario 3: Blueprint for a clean course migration

Use this when:
- migrating D2L/Brightspace content into a new Canvas Blueprint shell

Recommended order:
1. import the starter template into the clean Blueprint shell
2. import the converted course package
3. run post-import cleanup
4. fix accessibility, links, files, and naming
5. only then associate or sync to downstream courses

Reason:
- it is safer to stabilize the Blueprint first than to push imperfect content into associated courses and clean up later

## Intermediate Scenarios

### Scenario 4: Locking content but not dates

This is one of the most useful real-world patterns.

Use this when:
- the CC wants wording, structure, and grading logic to stay aligned
- instructors still need their own calendars

Recommended approach:
- lock content and points where consistency matters
- leave due dates and availability dates unlocked unless all sections truly run on one schedule

Why:
- date locking creates more downstream friction than most coordinators expect
- if course pace differs by section, locked dates become a maintenance burden

### Scenario 5: Locking dates centrally

Use this only when:
- all associated courses truly follow the same schedule
- the CC is willing to own the timing changes for all downstream sections

Risks:
- section instructors lose date flexibility
- late schedule changes become more disruptive
- date mismatches create confusion if instructors think they can edit locally

If you do this:
- document it clearly for instructors
- keep sync messages explicit

### Scenario 6: Centralized module structure with local extras

Canvas Blueprint module behavior needs care.

Officially relevant behaviors:
- new Blueprint modules are added to the bottom of Modules in associated courses
- if a module item is moved in an associated course, the next sync may cause duplication behavior
- modules deleted from an associated course are not restored by later syncs
- new local module items in the associated course remain, but appear above Blueprint-synced items within that module

Practical takeaway:
- treat module order and placement as centrally managed if you are relying on Blueprint consistency
- do not assume local module rearrangements will remain stable after future syncs

## Advanced Scenarios

### Scenario 7: Mid-semester Blueprint revisions

Use caution.

Good candidates for mid-semester sync:
- typo fixes
- accessibility fixes
- broken file/link repairs
- clarified assignment instructions
- resource replacements

High-risk mid-semester syncs:
- changing dates globally
- changing point structures
- changing quiz behavior
- altering module order after sections are active

Recommended approach:
1. make a small, explicit batch of changes
2. review unsynced changes carefully
3. send a notification with a plain-language message
4. spot-check one associated course after sync

### Scenario 8: Blueprint plus external tools

Use caution with LTIs and external tool assignments.

From the migration side, these are often the least portable items.

Practical rule:
- Blueprint the shell, naming, instructions, and placement
- do not assume the external tool connection is fully portable or ready without verification
- confirm whether the CC or instructor must finish setup locally

This was a recurring issue in summer migrations.

### Scenario 9: Blueprint plus New Quizzes

Use caution with:
- item banks
- image/file assets in question bodies
- randomized question behavior

Practical rule:
- verify the live quiz behavior after import and after sync
- do not assume D2L-style question-library behavior maps cleanly without review

### Scenario 10: Blueprint with downstream section autonomy

If departments want strong instructor autonomy, Blueprint may still be useful, but only if lock choices are conservative.

Recommended pattern:
- standardize the foundation
- leave instructor-facing teaching choices unlocked
- avoid over-locking content that faculty are expected to personalize

If everything is locked, the Blueprint becomes a compliance tool instead of a reusable teaching foundation.

## Sinclair-Specific Workflow Notes

Based on the recent migration work:

### Use the Blueprint shell as the stabilized master

Recommended sequence:
1. migrate and clean the Blueprint shell first
2. repair files, quizzes, links, accessibility, and template pages
3. confirm module/item naming
4. finalize assignment/discussion/quiz structure
5. only then push content downstream

### Keep a reference import when useful

In some courses, it helped to keep a separate imported master/reference shell so the CC could compare against the raw D2L import if something appeared missing from the Blueprint.

Use this when:
- migrations required heavy cleanup
- the faculty may want to compare against original content

### Treat template pages as part of QA

Repeated issue area:
- template pages can still produce accessibility checker findings after import
- the app is being hardened to reduce this, but Canvas-side validation is still necessary

### Expect link validation noise

The Canvas Link Validator is useful, but not perfect.

Typical outcomes:
- some unreachable external links are truly dead
- some are paywalled
- some are validator false positives caused by bot blocking or dynamic websites

Do not use “not a perfectly clean validator run” as the sole sign that a Blueprint is bad.
Use a triage process instead.

## What Instructors and CCs Should Check After a Sync

In a Blueprint course:
- unsynced change count
- sync history
- lock settings on items that should remain standardized
- sync message content before pushing changes

In associated courses:
- Blueprint sync information in Course Settings
- whether locked items updated as expected
- whether local customizations still behave as intended
- modules for unexpected order/duplication issues
- critical quizzes, assignments, and file links

## Recommended Lock Strategy

Default recommendation:
- lock only what truly needs centralized control

Usually good candidates for locking:
- page content that should remain standardized
- assignment instructions that must stay aligned
- discussion prompts that are meant to be identical
- point values if grading must stay consistent across sections

Usually poor candidates for automatic locking:
- dates, unless the schedule is truly shared
- instructor-specific resource pages
- local announcements
- items faculty are explicitly expected to personalize

## Common Failure Modes

### Over-locking

Problem:
- instructors cannot make needed course-specific adjustments

Fix:
- relock more selectively

### Syncing too early

Problem:
- messy or partially repaired content reaches associated courses

Fix:
- finish migration cleanup and QA in the Blueprint first

### Assuming local deletions will be restored

Problem:
- module deletions in associated courses may not come back on later syncs

Fix:
- do not rely on sync as an automatic “restore all structure” tool

### Ignoring lock behavior in renamed or edited items

Problem:
- downstream users think they can safely edit an item, but a later lock/sync overwrites it

Fix:
- be explicit about what is standardized and what is local

## Basic-to-Advanced Practice Path

### Level 1: Basic operator

Learn to:
- identify a Blueprint vs associated course
- view Blueprint icons
- view Blueprint sync info in associated courses
- sync a simple update from the Blueprint

Practice:
- edit one page in the Blueprint
- sync it
- verify the change in one associated course

### Level 2: Structured coordinator

Learn to:
- set a lock strategy
- decide what should remain editable by section instructors
- use sync messages clearly
- review unsynced changes before syncing

Practice:
- lock content on a page
- leave due dates unlocked on an assignment
- sync both and verify the different behaviors

### Level 3: Advanced coordinator

Learn to:
- manage midstream updates without destabilizing live sections
- troubleshoot module behavior
- coordinate Blueprint content with LTIs, quizzes, and files
- interpret sync history and associated-course sync information

Practice:
- run a staged update on a test associated course
- verify links, accessibility, and quiz behavior before pushing broad changes

## Recommended Training Exercises

1. Create one practice Blueprint and one associated course.
2. Add:
- one page
- one assignment
- one discussion
- one module with a prerequisite or requirement
3. Lock content on the page.
4. Leave the assignment dates unlocked.
5. Sync and verify what changes.
6. Reorder a module item in the associated course.
7. Sync again and observe module behavior.
8. Review sync history and associated-course sync information.

This is the fastest way to understand the real tradeoffs.

## Practical Department Guidance

For department-managed Blueprints:
- standardize naming conventions early
- decide which assessment settings are centrally owned
- avoid leaving placeholder/template notes in published content
- verify all files, quizzes, and external links before first downstream sync
- document for faculty which items they are expected to revise locally

## Official References

Use these as the authoritative product references:

- Blueprint Sync Functionalities  
  https://community.instructure.com/en/kb/articles/628548-unknown

- How do I sync course content in a blueprint course as an instructor?  
  https://community.instructure.com/en/kb/articles/660771-how-do-i-sync-course-content-in-a-blueprint-course-as-an-instructor

- How do I lock course objects in a blueprint course as an instructor?  
  https://community.instructure.com/en/kb/articles/660770-how-do-i-lock-course-objects-in-a-blueprint-course-as-an-instructor

- How do I view the blueprint sync information for a course associated with a blueprint course?  
  https://community.instructure.com/en/kb/articles/660783-how-do-i-view-the-blueprint-sync-information-for-a-course

- How do I view the sync history for a blueprint course as an instructor?  
  https://community.instructure.com/en/kb/articles/660782-how-do-i-view-the-sync-history-for-a-blueprint-course-as-an-instructor

- How do I enable a course as a blueprint course as an admin?  
  https://community.instructure.com/en/kb/articles/660781-how-do-i-enable-a-course-as-a-blueprint-course-as-an-admin

- How do I associate a course with a blueprint course as an admin?  
  https://community.instructure.com/en/kb/articles/660778-how-do-i-associate-a-course-with-a-blueprint-course-as-an-admin

## Recommended Next Training Step

If you want to train practically rather than just read:
- create a sandbox Blueprint pair
- practice locking, syncing, module movement, and date behavior
- then document the local Sinclair conventions you want faculty and CCs to follow

That will teach more than another abstract guide.
