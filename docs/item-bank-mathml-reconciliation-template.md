# Item Bank MathML Reconciliation Worksheet

Use this worksheet before replacing a live New Quizzes item bank that contains broken MathML or WIRIS payloads. The goal is to avoid overwriting legitimate live edits while still producing a clean replacement bank for future attempts.

## 1. Bank Identity

- Bank name:
- Canvas bank ID:
- Course(s) currently using this bank:
- Course ID(s):
- Shared scope:
  - Current course only
  - Shared to other course(s)
  - Shared to account / sub-account
- Owner / maintainer:
- Last updated in Canvas:

## 2. Live Quiz Usage

List every quiz that currently pulls from this bank.

| Quiz Title | Course ID | Pull Type | Draw Count | Points Per Question | Active Student Use? |
| --- | --- | --- | --- | --- | --- |
|  |  | all / random |  |  | yes / no |

Notes:
- If a quiz uses a random pull, confirm the draw count and point value before replacement.
- If a quiz is still open to students, document whether any active attempts are in progress.

## 3. Source Availability

- Original D2L / Brightspace export available?
  - Yes / No
- Source path:
- Existing Canvas-ready package available?
  - Yes / No
- Existing manual edits documented?
  - Yes / No
- Notes on missing source materials:

## 4. Live Edit Risk

Check for evidence that the bank was edited after migration.

- Faculty / CC confirms edits were made in Canvas:
  - Yes / No
- Canvas item tags / metadata changed:
  - Yes / No
- Question wording differs from source:
  - Yes / No
- Answer keys / distractors differ from source:
  - Yes / No
- Point logic / options differ from source:
  - Yes / No

Describe known live edits:

## 5. MathML Defect Inventory

Summarize the exact equation issues before deciding on replacement.

| Item / Question Title | Question Type | Broken Equation Count | Error Pattern | Fixability |
| --- | --- | --- | --- | --- |
|  |  |  | Unexpected text node / other | API / UI / rebuild |

Notes:
- Distinguish direct quiz items from bank-backed items.
- Record whether the bad payload already contains recoverable MathML.

## 6. Reconciliation Decision

Choose one path for this bank.

- Path A: Keep live bank and fix a small number of items manually in the UI
- Path B: Build corrected replacement bank from source and relink quizzes
- Path C: Build corrected replacement bank from live/exported quiz content
- Path D: Escalate to Instructure and defer replacement

Reasoning:

## 7. Replacement Bank Plan

If creating a replacement bank:

- New bank name:
- Bank creation method:
  - Duplicate existing bank
  - New empty bank + QTI import
  - New empty bank + manual add/copy
- Content source for replacement:
  - D2L export
  - Cleaned Canvas quiz export
  - Mixed / reconciled source
- Math cleanup performed:
  - Deterministic MathML repair
  - Reauthored in LaTeX
  - Equation images retained

## 8. Quiz Relink Plan

Document how each quiz will be pointed to the corrected bank.

| Quiz Title | Existing Bank | New Bank | Draw Count Verified | Points Verified | Completed |
| --- | --- | --- | --- | --- | --- |
|  |  |  | yes / no | yes / no | yes / no |

## 9. Validation

After replacement, verify:

- Quiz build shows the new bank
- Draw count matches intended number of questions
- Points per question match
- Sample attempt renders equations correctly
- Existing historical bank remains available until sign-off
- Faculty / CC sign-off received

## 10. Retirement of Old Bank

- Old bank retained temporarily:
  - Yes / No
- Old bank removed from active quiz pulls:
  - Yes / No
- Old bank archived / renamed:
  - Yes / No
- Old bank deleted only after validation:
  - Yes / No

Final notes:
