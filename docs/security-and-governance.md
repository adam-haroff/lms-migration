# Security and Governance Notes (US Higher Ed)

This is operational guidance, not legal advice.

## Primary risk categories

- Student data exposure (FERPA-regulated records).
- Licensed textbook/publisher content redistribution.
- Copyright ownership and derivative rights for course media.
- Unauthorized retention of assessment banks and answer keys.

## Local processing vs cloud AI

## Local-first processing (recommended default)

- Keeps raw course packages on institution-controlled systems.
- Simplifies FERPA controls and incident scope.
- Reduces third-party data transfer and retention risk.

## Cloud AI processing (possible with controls)

Use only if vendor contract and controls are acceptable:

- Signed DPA and FERPA-aligned terms.
- Clear data retention policy (`no training` and strict retention windows).
- Regional data handling requirements documented.
- Role-based access control and audit logging enabled.
- Encryption in transit and at rest validated.

## Minimum governance checklist before production use

1. Data classification for each artifact (content, gradebook, assessments, media).
2. Approved processing boundary (local only or contracted cloud).
3. Rules for redaction/anonymization prior to AI-assisted review.
4. Legal review for publisher-licensed and third-party copyrighted material.
5. Standard operating procedure for deletion and incident response.
6. Human review gate for all high-risk items:
   - Quiz banks
   - Accessibility remediation
   - Embedded media/licenses
   - External tool links (LTI)

## Practical policy split for migration work

- Tier A (safe for broad automation):
  - Structural HTML cleanup
  - Link rewrites
  - Template injection
  - Accessibility heuristics
- Tier B (human-required approval):
  - Assessments and answer keys
  - Grading policy language
  - Media rights statements
  - Any content with uncertain licensing
