# AI-Assisted Scoping Documents

## Architecture Decision

Speakr remains the system of record for recordings, transcripts, summaries, notes, and meeting
metadata. SpeakrBridge owns template selection, structured field extraction, Word generation, and
generated-document lifecycle. This keeps product-specific document logic out of the upstream Speakr
application and places Windows Word automation beside the bridge's existing Windows integrations.

## User Workflow

1. Speakr emits `recording.summary.completed`.
2. SpeakrBridge records that the meeting is ready for a scoping document.
3. The user selects a product workflow, initially OpenText Fax Install or Upgrade / Migration.
4. SpeakrBridge fetches the latest recording metadata, notes, summary, and transcript.
5. AI extracts template-specific answers with confidence and source evidence.
6. Deterministic validation converts answers into Word control values.
7. The bridge fills a new DOCX and presents it for download or opening in Word.
8. The user reviews and finishes the document in Word.

## Milestones

### 1. Versioned Word document layer

Status: implemented; Windows validation pending.

- Treat the original `.doc` as immutable.
- Map all 67 legacy controls by stable semantic IDs and verified Word indexes.
- Verify the immutable source file against its versioned SHA-256 hash.
- Validate total field count and text/checkbox/dropdown counts before writing.
- Reset existing defaults, including the telephony dropdown.
- Set install/upgrade project type deterministically from the selected workflow.
- Save only to a new `.docx` and refuse overwrite.

### 2. Structured AI extraction

Status: implemented; real-recording model evaluation pending.

- Build the model prompt from field definitions rather than hard-coded prose.
- Include metadata, notes, Speakr summary, and transcript.
- Return `value`, `status`, `confidence`, and evidence for each business answer.
- Use `found`, `inferred`, and `unknown` statuses; unknown answers remain blank.
- Validate enums, dates, booleans, mutually exclusive choices, and mode applicability.
- Translate business answers into individual Word controls only after validation.
- Apply versioned deterministic derivation rules only from verified evidence, without overriding explicit answers.

Implementation notes:

- The versioned manifest defines 36 business answers independently from the 67 Word controls.
- Install prompts include 32 applicable answers; upgrade prompts include 35.
- Every `found` answer must contain an exact quote that can be verified against its named source.
- Unsupported `found` values and omitted answers are deterministically downgraded to `unknown`.
- Only `found` values are translated to Word controls by default.

### 3. Scoping jobs and API

Status: implemented; live Windows workflow validation pending.

- Add durable job states: ready, extracting, review, generating, completed, and failed.
- Fetch current Speakr content at generation time so edited notes are included.
- Store template ID/version, project mode, extraction result, evidence, and output path.
- Make retries idempotent and keep each generated revision separate.

Implementation notes:

- SQLite assigns revisions atomically per recording, template, and project mode.
- State transitions use conditional updates so duplicate extraction or generation requests conflict.
- Interrupted `extracting` and `generating` jobs are recovered as failed and retryable on startup.
- List responses omit extraction payloads; job detail retains answers, confidence, evidence, and warnings.
- Generated file paths are server-owned and constrained to the configured output directory.
- The API exposes template discovery, job creation/list/detail, extraction retry, generation, and download.

### 4. Bridge web workflow

Status: pending.

- List meetings ready for document generation.
- Present product/workflow choices.
- Show extraction progress, unknown fields, and validation warnings.
- Generate and download the DOCX without requiring users to edit a duplicate web form.
- Add a OneNote link or notification as the initial launch point.

### 5. Hardening and expansion

Status: pending.

- Add authentication before exposing the bridge beyond localhost.
- Define retention for transcripts, extraction evidence, and generated customer documents.
- Add visual regression checks for each versioned Word template.
- Add new products/forms by supplying a source document and manifest without changing orchestration.

## Acceptance Gate for Milestone 1

Milestone 1 is complete only after testing on Windows with desktop Word confirms:

- inspection reports 67 fields: 25 text, 41 checkbox, and 1 dropdown;
- an install draft checks only the new-installation project field;
- an upgrade draft checks only the upgrade project field and fills version/SUID;
- unchecked and unknown answers remain blank or false;
- the output opens as an editable DOCX with the original layout preserved;
- the source `.doc` remains byte-for-byte unchanged.

## Acceptance Gate for Milestone 2

- run extraction against representative install and upgrade recordings;
- confirm the configured Ollama model returns all requested answers within its context window;
- compare extracted values and unknowns against a human-reviewed answer set;
- confirm unsupported claims are downgraded when their evidence quote is absent or altered;
- confirm generated values select valid dropdown and checkbox options only;
- tune field guidance and model choice based on measured results rather than isolated examples.
