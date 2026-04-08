# BIS 1400 Quiz Build Sheet

Use this sheet for the BIS-1400 New Quizzes rebuild.

## Working Rule

- Build fresh New Quizzes from the converted item banks.
- Do not trust the migrated quiz copies when they flattened whole banks into the quiz body.
- Leave the existing `Syllabus Quiz` alone unless you want to revise it for content reasons.
- Keep each rebuilt quiz in the same assignment group as the current live student-facing quiz it replaces.
- After each fresh quiz is verified, unpublish or remove the bad migrated copy so students only see one quiz.

## Standard Chapter Quiz Description

Use this on Modules 1-10 unless the live course already has a better finalized version:

```html
<h2 style="color: #ac1a2f; border-bottom: 10px solid #ac1a2f; padding-top: 10px; padding-right: 10px; padding-bottom: 10px;"><strong>Quiz Overview</strong></h2>
<p>In this quiz, you will answer 20 questions from the chapter. You will have 20 minutes to complete the quiz. Once you begin the quiz, your attempt will be counted.</p>
<p>You have 2 attempts for this quiz. The questions are pulled from a pool of questions, so you may not have the same questions on your second attempt. Your highest score will appear in the gradebook.</p>
<p>Follow the directions below to begin this assessment.</p>
<ol>
  <li>Click the Begin button.</li>
  <li>Click Submit button upon completion of the quiz so your score is recorded.</li>
</ol>
<hr />
<div style="background-color: #f8f8f8; padding: 15px;">
  <h2><strong>Technical Support</strong></h2>
  <p>Need help using Canvas Quizzes? If so, please review the following page: <a class="inline_disabled" href="https://design.instructure.com/courses/178/pages/quizzes" target="_blank" rel="noopener">Canvas Resources for Students - Quizzes.</a></p>
</div>
```

## TTCE Quiz Description Rule

Use this TTCE template description for `TTCE 1-6`:

```html
<h2 style="color: #ac1a2f; border-bottom: 10px solid #ac1a2f; padding-top: 10px; padding-right: 10px; padding-bottom: 10px;"><strong>Quiz Overview</strong></h2>
<p>In this quiz, you will check your understanding of the videos presented in this section.</p>
<p>You will have 10 minutes to complete the quiz. You have 3 attempts, and your highest score will appear in the gradebook.</p>
<p>Follow the directions below to begin this assessment.</p>
<ol>
  <li>Click the Begin button.</li>
  <li>Click Submit button upon completion of the quiz so your score is recorded.</li>
</ol>
<hr />
<div style="background-color: #f8f8f8; padding: 15px;">
  <h2><strong>Technical Support</strong></h2>
  <p>Need help using Canvas Quizzes? If so, please review the following page: <a class="inline_disabled" href="https://design.instructure.com/courses/178/pages/quizzes" target="_blank" rel="noopener">Canvas Resources for Students - Quizzes.</a></p>
</div>
```

Note:

- TTCE 5 source text says `1 attempt`, but the actual D2L quiz settings allow `3 attempts`.
- Use the actual D2L settings unless faculty says otherwise.

## Final Exam Description Rule

Use this on `Module 13: Final Exam`:

```html
<h2 style="color: #ac1a2f; border-bottom: 10px solid #ac1a2f; padding-top: 10px; padding-right: 10px; padding-bottom: 10px;"><strong>Exam Overview</strong></h2>
<p>In this exam, you will demonstrate your understanding of the material covered in this course.</p>
<p>You will have 50 minutes to complete the exam. You have 1 attempt.</p>
<p>Follow the directions below to begin this assessment.</p>
<ol>
  <li>Click the Begin button.</li>
  <li>Click Submit button upon completion of the exam so your score is recorded.</li>
</ol>
<hr />
<div style="background-color: #f8f8f8; padding: 15px;">
  <h2><strong>Technical Support</strong></h2>
  <p>Need help using Canvas Quizzes? If so, please review the following page: <a class="inline_disabled" href="https://design.instructure.com/courses/178/pages/quizzes" target="_blank" rel="noopener">Canvas Resources for Students - Quizzes.</a></p>
</div>
```

## New Quizzes Settings View

These are the settings to use on the `Build > Settings` tab in New Quizzes.

Important:

- Set `points`, `assignment group`, `due date`, and `availability dates` from the outer assignment details page, not from the New Quizzes settings tab.
- Canvas notes that item bank questions added in a group stay randomized within that group even if `Shuffle questions` is off. That is why the chapter quizzes can keep `Shuffle questions` off while still using a random set from an item bank.

### Chapter Quizzes: Settings Tab

Use this profile for `Module 1: Quiz: Chapter 1` through `Module 10: Quiz: Chapter 10`.

| Setting | Value |
| --- | --- |
| `Shuffle questions` | `Off` |
| `Shuffle answers` | `Off` |
| `One question at a time` | `Off` |
| `Require a student access code` | `Off` |
| `Time limit` | `On` |
| `Time limit value` | `20 minutes` |
| `Detect Multiple Sessions` | `Off` |
| `Filter IP addresses` | `Off` |
| `Allow Calculator` | `Off` |
| `Allow clearing selection (Multiple Choice)` | `Off` |
| `Show custom feedback with results` | `Off` |
| `Disable Document Uploads` | `Off` |
| `Allow multiple attempts` | `On` |
| `Attempts type` | `Limited` |
| `Number of attempts` | `2` |
| `Score to keep` | `Highest` |
| `Require time between attempts` | `Off` |
| `Enable build on last attempt` | `Off` |
| `Hide results from students` | `On` |

Result view under `Hide results from students`:

- `Show points possible`: `On`
- `Show points awarded`: `On`
- `Show questions`: `Off`
- `Show student responses`: `Off`
- `Indicate response as correct or incorrect`: `Off`
- `Show feedback`: `Off`

### TTCE Quizzes: Settings Tab

Use this profile for `TTCE 1-6`, with the noted exception for question shuffle on `TTCE 4` and `TTCE 6`.

| Setting | TTCE 1 | TTCE 2 | TTCE 3 | TTCE 4 | TTCE 5 | TTCE 6 |
| --- | --- | --- | --- | --- | --- | --- |
| `Shuffle questions` | `Off` | `Off` | `Off` | `On` | `Off` | `On` |
| `Shuffle answers` | `Off` | `Off` | `Off` | `Off` | `Off` | `Off` |
| `One question at a time` | `Off` | `Off` | `Off` | `Off` | `Off` | `Off` |
| `Require a student access code` | `Off` | `Off` | `Off` | `Off` | `Off` | `Off` |
| `Time limit` | `On` | `On` | `On` | `On` | `On` | `On` |
| `Time limit value` | `10 min` | `10 min` | `10 min` | `10 min` | `10 min` | `10 min` |
| `Detect Multiple Sessions` | `Off` | `Off` | `Off` | `Off` | `Off` | `Off` |
| `Filter IP addresses` | `Off` | `Off` | `Off` | `Off` | `Off` | `Off` |
| `Allow Calculator` | `Off` | `Off` | `Off` | `Off` | `Off` | `Off` |
| `Allow clearing selection (Multiple Choice)` | `Off` | `Off` | `Off` | `Off` | `Off` | `Off` |
| `Show custom feedback with results` | `Off` | `Off` | `Off` | `Off` | `Off` | `Off` |
| `Disable Document Uploads` | `Off` | `Off` | `Off` | `Off` | `Off` | `Off` |
| `Allow multiple attempts` | `On` | `On` | `On` | `On` | `On` | `On` |
| `Attempts type` | `Limited` | `Limited` | `Limited` | `Limited` | `Limited` | `Limited` |
| `Number of attempts` | `3` | `3` | `3` | `3` | `3` | `3` |
| `Score to keep` | `Highest` | `Highest` | `Highest` | `Highest` | `Highest` | `Highest` |
| `Require time between attempts` | `Off` | `Off` | `Off` | `Off` | `Off` | `Off` |
| `Enable build on last attempt` | `Off` | `Off` | `Off` | `Off` | `Off` | `Off` |
| `Hide results from students` | `On` | `On` | `On` | `On` | `On` | `On` |

Result view under `Hide results from students`:

- `Show points possible`: `On`
- `Show points awarded`: `On`
- `Show questions`: `Off`
- `Show student responses`: `Off`
- `Indicate response as correct or incorrect`: `Off`
- `Show feedback`: `Off`

### Final Exam: Settings Tab

Use this profile for `Module 13: Final Exam`.

| Setting | Value |
| --- | --- |
| `Shuffle questions` | `On` |
| `Shuffle answers` | `Off` |
| `One question at a time` | `Off` |
| `Require a student access code` | `Off` |
| `Time limit` | `On` |
| `Time limit value` | `50 minutes` |
| `Detect Multiple Sessions` | `Off` |
| `Filter IP addresses` | `Off` |
| `Allow Calculator` | `Off` |
| `Allow clearing selection (Multiple Choice)` | `Off` |
| `Show custom feedback with results` | `Off` |
| `Disable Document Uploads` | `Off` |
| `Allow multiple attempts` | `Off` |
| `Hide results from students` | `On` |

Result view under `Hide results from students`:

- `Show points possible`: `On`
- `Show points awarded`: `On`
- `Show questions`: `Off`
- `Show student responses`: `Off`
- `Indicate response as correct or incorrect`: `Off`
- `Show feedback`: `Off`

### Notes On Source Matching

- `Shuffle answers` should stay `Off` across the rebuild. The D2L source only shows mixed per-question answer shuffling in the final exam, and New Quizzes applies this toggle globally.
- `One question at a time` should stay `Off`. The D2L quizzes were not forward-only.
- `Hide results from students` should be `On` if you want the closest match to the D2L setting `show_correct_answers = no`.
- If you later decide students should immediately see full questions and responses, that would be a policy choice in Canvas, not a source-faithful migration choice.

### Why A Migrated Quiz May Show Different Results Settings

The migrated quiz copies appear to be reading more than one D2L result-display value.

- The D2L source consistently shows `show_correct_answers = no`.
- But the D2L source also includes a separate results-display profile (`response_display_type_id`), and those values are not perfectly uniform across all BIS quizzes.
- That means a migrated New Quiz may turn on `Show questions` and `Show student responses` even when the D2L quiz was still withholding correct answers.

So the migration is not necessarily wrong. It is just mapping D2L's richer result-display model into a smaller set of Canvas New Quizzes controls.

### Closest Available Canvas Match To The D2L Result Display Screenshot

If you want a quiz to behave more like the D2L result display shown in the source screenshots, this is the closest New Quizzes approximation:

| Setting | Value |
| --- | --- |
| `Hide results from students` | `On` |
| `Show questions` | `On` |
| `Show student responses` | `On` |
| `Student responses timing` | `For all attempts` |
| `Indicate response as correct or incorrect` | `On` |
| `Show correct answer` | `Off` |
| `Show feedback` | `Off` |
| `Show points possible` | `On` |
| `Show points awarded` | `On` |

Important limitation:

- Current New Quizzes settings do not provide a separate `incorrect questions only` display mode.
- So this Canvas configuration is only an approximation.
- It is closer to the D2L screenshot than the conservative profile above, but it also exposes more of the quiz to students.

Recommended rule for BIS:

- For banked chapter quizzes, TTCE quizzes, and the final exam, keep the conservative profile already listed in this sheet.
- Only use the closer-match profile if you intentionally want students to review their question set and their submitted responses after each attempt.

## Build Settings

### Keep As-Is

| Canvas Title | Notes |
| --- | --- |
| `Syllabus Quiz` | Already a New Quiz in the live Canvas course. No rebuild needed unless content changes are wanted. |

### Chapter Quizzes

| Canvas Title | Source D2L Title | Source Pool | Bank Size | Add To Quiz | Points | Time | Attempts | Score To Keep | Notes |
| --- | --- | --- | ---: | --- | ---: | ---: | ---: | --- | --- |
| `Module 1: Quiz: Chapter 1` | `Quiz Chapter 1` | `Ch1 Pool` | 42 | Random set of 20 from bank | 20 | 20 min | 2 | Highest | Use standard chapter quiz description. |
| `Module 2: Quiz: Chapter 2` | `Quiz Chapter 2` | `CH2` | 54 | Random set of 20 from bank | 20 | 20 min | 2 | Highest | Use standard chapter quiz description. |
| `Module 3: Quiz: Chapter 3` | `Quiz Chapter 3` | `CH3` | 50 | Random set of 20 from bank | 20 | 20 min | 2 | Highest | Use standard chapter quiz description. |
| `Module 4: Quiz: Chapter 4` | `Quiz Chapter 4` | `CH4` | 50 | Random set of 20 from bank | 20 | 20 min | 2 | Highest | Use standard chapter quiz description. |
| `Module 5: Quiz: Chapter 5` | `Quiz Chapter 5` | `CH5` | 52 | Random set of 20 from bank | 20 | 20 min | 2 | Highest | Use standard chapter quiz description. |
| `Module 6: Quiz: Chapter 6` | `Quiz Chapter 6` | `CH6` | 51 | Random set of 20 from bank | 20 | 20 min | 2 | Highest | Use standard chapter quiz description. |
| `Module 7: Quiz: Chapter 7` | `Quiz Chapter 7` | `CH7` | 59 | Random set of 20 from bank | 20 | 20 min | 2 | Highest | Use standard chapter quiz description. |
| `Module 8: Quiz: Chapter 8` | `Quiz Chapter 8` | `CH8` | 54 | Random set of 20 from bank | 20 | 20 min | 2 | Highest | Use standard chapter quiz description. |
| `Module 9: Quiz: Chapter 9` | `Quiz Chapter 9` | `CH9` | 50 | Random set of 20 from bank | 20 | 20 min | 2 | Highest | Use standard chapter quiz description. |
| `Module 10: Quiz: Chapter 10` | `Quiz Chapter 10` | `CH10` | 51 | Random set of 20 from bank | 20 | 20 min | 2 | Highest | Use standard chapter quiz description. |

### TTCE Quizzes

| Canvas Title | Source D2L Title | Total Questions | Add To Quiz | Points | Time | Attempts | Score To Keep | Notes |
| --- | --- | ---: | --- | ---: | ---: | ---: | --- | --- |
| `TTCE 1: Quiz: Through the Customer's Eyes` | `TTCE Module 1 Through the Customer's Eyes Quiz` | 10 | Add all questions from bank | 10 | 10 min | 3 | Highest | Leave description blank. |
| `TTCE 2: Quiz: What Customers Want` | `TTCE Module 2 Quiz - What Customers Want` | 10 | Add all questions from bank | 10 | 10 min | 3 | Highest | Leave description blank. |
| `TTCE 3: Quiz: Essential Customer Service Part 1` | `TTC Module 3 Quiz - Essential Customer Service Part 1` | 10 | Add all questions from bank | 10 | 10 min | 3 | Highest | Standardize the title to `TTCE 3`, not `TTC`. |
| `TTCE 4: Quiz: Essential Customer Service Part 2` | `TTCE Module 4 Quiz - Essential Customer Service Part 2` | 10 | Add all questions from bank | 10 | 10 min | 3 | Highest | D2L used random question order. Turn on question shuffle/order randomization if you want the closest match. |
| `TTCE 5: Quiz: Handling Complaints` | `TTCE Module 5 Quiz - Handling Complaints` | 10 | Add all questions from bank | 10 | 10 min | 3 | Highest | Source text mentions `1 attempt`, but D2L settings say `3 attempts`. Follow the actual quiz settings unless faculty says otherwise. |
| `TTCE 6: Quiz: Strategic Marketing` | `TTCE Module 6 Quiz - Strategic Marketing` | 10 | Add all questions from bank | 10 | 10 min | 3 | Highest | D2L used random question order. Turn on question shuffle/order randomization if you want the closest match. |

### Final Exam

| Canvas Title | Source D2L Title | Total Questions | Add To Quiz | Points | Time | Attempts | Score To Keep | Notes |
| --- | --- | ---: | --- | ---: | ---: | ---: | --- | --- |
| `Module 13: Final Exam` | `Final Exam` | 50 | Add all questions from bank | 50 | 50 min | 1 | Highest | D2L used random question order. Description can stay blank because the module page already gives the instructions. |

## Recommended Build Order

1. Build one chapter quiz first as a pattern.
2. Build the rest of Modules 1-10 chapter quizzes.
3. Build one TTCE quiz as a pattern.
4. Build TTCE 1-6.
5. Build the final exam last.
6. After each quiz is verified, unpublish or remove the bad migrated copy.

## Per-Quiz Verification Checklist

- title matches the intended student-facing Canvas title
- correct assignment group is selected
- points match the table above
- time limit matches the table above
- attempts match the table above
- highest score is kept
- chapter quizzes use a random set of 20 from the correct bank
- TTCE quizzes use all 10 questions from the correct bank
- final exam uses all 50 questions
- module placement is correct
- only one student-facing quiz remains published in the module
