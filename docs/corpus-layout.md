# Corpus Layout

This repo currently has three different course corpora, and they serve different purposes:

## `resources/incoming/`

Use this for active intake and investigation work.

- These are the raw or in-progress migration courses we are actively learning from.
- They are the best place to look for current edge cases found during real migrations.
- They may have local notes, partial outputs, or one-off validation artifacts.
- These are not the same thing as the curated training/example sets.

Examples:

- `resources/incoming/acc-2321/`
- `resources/incoming/vet-2111/`

## `resources/examples/`

Use this for curated example courses and gold/reference comparisons.

- These courses are structured as reusable examples.
- Many include `before/`, `after/`, `baseline/`, and `notes/`.
- This corpus is what the example-analysis tooling expects by default.
- The template package also lives here under `resources/examples/template/`.

Examples:

- `resources/examples/dev-0035/`
- `resources/examples/his-1105/`
- `resources/examples/template/`

## `resources/training-corpus-v2/`

Use this for the separate training corpus.

- This is a dedicated training/analysis set distinct from `resources/examples/`.
- The pattern-report tooling treats this as its primary training corpus root.
- It is organized under `courses/`.

Examples:

- `resources/training-corpus-v2/courses/ast-1111/`
- `resources/training-corpus-v2/courses/spa-1101/`

## Output Folders

- `output/<course-code>/`:
  working outputs for active/reference courses, usually paired with `resources/incoming/<course-code>/`
- `output/examples/`:
  analysis output for the curated example corpus
- `output/training-corpus-v2/`:
  analysis output for the training corpus

## Practical Rule Of Thumb

- If the course is part of live migration work or current investigation, treat it as `incoming`.
- If the course is a curated reusable example with before/after comparison value, treat it as `examples`.
- If the course belongs to the separate research/training set, treat it as `training-corpus-v2`.

## Possible Future Cleanup

If we later want a clearer naming scheme, the safest likely rename would be:

- `resources/incoming/` -> `resources/intake/` or `resources/reference-courses/`

That should be done only after updating code defaults, docs, tests, and any local workflows that currently assume `resources/incoming/`.
