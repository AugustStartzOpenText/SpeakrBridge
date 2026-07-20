# SpeakrBridge

SpeakrBridge receives local `Speakr` webhook events, pulls recording content from the Speakr API, requests a structured summary from `Ollama`, and writes a formatted page into `OneNote` through the Windows COM API.

For the normal start-to-finish operating procedure, see [SpeakrBridge Daily Workflow](END_USER_GUIDE.md).

## Status

This repository contains the initial service scaffold from the PRD. The core pipeline is implemented, but OneNote COM hierarchy handling and production hardening still need real Windows validation against a live Speakr and OneNote environment.

## Project Layout

- `main.py`: FastAPI app and webhook pipeline orchestration.
- `webhook.py`: raw-body HMAC validation and payload parsing.
- `speakr_client.py`: Speakr REST API pull layer.
- `ollama_client.py`: Ollama request and structured response parsing.
- `page_builder.py`: OneNote outline XML assembly for the target page body.
- `onenote_writer.py`: PowerShell bridge wrapper for OneNote COM integration.
- `notifier.py`: Windows toast notifications with `win10toast` and `plyer`.
- `config.py`: YAML config loading and validation.
- `models.py`: shared Pydantic models used across the pipeline.
- `scoping/`: versioned scoping-form manifests and Word document generation.
- `scoping_cli.py`: template inspection and draft-generation commands.

## Setup

1. Create and activate a Python 3.11+ virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `config.yaml.example` to `config.yaml`, then edit `config.yaml` with your Speakr webhook secret, Speakr API token, Ollama host, and initial OneNote notebook settings.
4. On Windows, optionally choose and persist the default OneNote destination:

```bash
python main.py --set-destination
```

You can inspect the currently available notebook/section targets with:

```bash
python main.py --list-onenote-sections
```

5. Start the service:

```bash
python main.py
```

6. Verify the service is up:

```bash
curl http://127.0.0.1:8080/health
```

If you are testing from another machine on your LAN, use the host machine's actual IP instead of `127.0.0.1`. If you are testing from an external webhook provider, you still need to expose this local port through a tunnel or reverse proxy because `0.0.0.0` only makes the service listen on local interfaces; it does not create a public URL.

If you want per-recording manual routing instead of immediate OneNote writes, set `onenote.manual_selection: true` in `config.yaml`. Queued jobs can then be managed with:

```bash
python main.py --list-pending
python main.py --route-pending
python main.py --route-job <job-id>
```

## Scoping Document Development

The first configured scoping form is the combined OpenText Fax installation / upgrade quoting
worksheet. It is exposed as two project modes that share the same source document:

```bash
python scoping_cli.py list
```

On Windows with desktop Microsoft Word installed, validate that the source still matches its
67-field manifest before generating documents:

```powershell
python .\scoping_cli.py inspect open_text_fax_install_upgrade_2025_08_20
```

Create an isolated DOCX draft using field values from JSON:

```powershell
python .\scoping_cli.py generate open_text_fax_install_upgrade_2025_08_20 upgrade `
  --values .\scoping\examples\open_text_fax_values.example.json
```

The generator opens the legacy source read-only, verifies the expected field count and types,
resets template defaults, and saves a new `.docx`. It refuses to overwrite an existing output.
The source worksheet is never modified.

Fetch the latest Speakr content for a recording and run grounded AI extraction:

```powershell
python .\scoping_cli.py recordings
python .\scoping_cli.py recordings --query "RightFax"
python .\scoping_cli.py extract open_text_fax_install_upgrade_2025_08_20 upgrade 123
```

The `recordings` command lists recent completed recordings with the numeric ID required by `extract`.

The extraction JSON retains per-answer status, confidence, exact evidence, and validation warnings.
Scoping extraction uses the configured `ollama.host` directly and processes form questions in small,
schema-constrained batches. Configure `ollama.scoping_batch_size` to tune the default batch size of 8;
`ollama.scoping_context_tokens` defaults these requests to a 32K context window.

Template manifests can define readable `derivation_rules`. A rule identifies a grounded source answer,
terms in `match_any` or `when_source_found`, optional `exclude_any` terms, a target answer/value, and
either `set_if_missing` or `append`. An optional `review_warning` flags details that the user must confirm
rather than inventing them. Rules only run from verified source evidence.

Generate a Word draft using only source-supported `found` answers:

```powershell
python .\scoping_cli.py generate open_text_fax_install_upgrade_2025_08_20 upgrade `
  --extraction .\generated\scoping\extractions\recording_123_open_text_fax_install_upgrade_2025_08_20_upgrade.json
```

`inferred` values are excluded by default. The optional `--include-inferred` flag is intended for
explicitly reviewed extraction files, not unattended generation.

## Scoping Jobs API

When scoping is enabled, SpeakrBridge persists jobs in SQLite and exposes the workflow under
`/api/scoping`. Relative database and output paths are resolved from the application directory.
Configure `scoping.api_token` before accessing these endpoints over the network. Without a token,
the API permits loopback requests only.

Create a job and start extraction immediately:

```bash
curl -X POST http://127.0.0.1:8080/api/scoping/jobs \
  -H "Authorization: Bearer $SCOPING_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"recording_id":123,"template_id":"open_text_fax_install_upgrade_2025_08_20","mode":"upgrade"}'
```

Inspect persisted extraction results and validation warnings:

```bash
curl -H "Authorization: Bearer $SCOPING_API_TOKEN" \
  http://127.0.0.1:8080/api/scoping/jobs/<job-id>
```

Generate a document using verified `found` answers, then download it:

```bash
curl -X POST http://127.0.0.1:8080/api/scoping/jobs/<job-id>/generate \
  -H "Authorization: Bearer $SCOPING_API_TOKEN" \
  -H "Content-Type: application/json" -d '{"include_inferred":false}'
curl -OJ -H "Authorization: Bearer $SCOPING_API_TOKEN" \
  http://127.0.0.1:8080/api/scoping/jobs/<job-id>/document
```

List endpoints return compact job summaries; the detail endpoint returns evidence and all extracted
answers. Failed extraction and generation operations can be retried through the corresponding action
endpoint. Jobs left in progress by a service restart are recovered as failed and retryable.

## Scoping Web Workflow

Open the workflow page from the machine running SpeakrBridge or another computer that can reach it:

```text
http://<speakrbridge-host>:8080/scoping
```

After SpeakrBridge successfully routes a recording to OneNote, it adds that recording to the
persistent scoping inbox. The page lets the user choose a configured form and workflow, start AI
extraction, monitor the job, generate the Word document, and download the completed DOCX. Meetings
that do not need a scoping document can be dismissed.

If `scoping.api_token` is configured, enter that token through the page's **API token** control. The
token is retained only in browser session storage. A token is required for access from another
computer; without one, the scoping API remains restricted to loopback requests.

The web page is served by the existing FastAPI process and has no separate frontend build or runtime.
Word generation still requires SpeakrBridge to run on Windows with desktop Word installed; a Linux
Speakr container cannot perform the COM automation.

## Current Notes

- The webhook route returns `202 Accepted` immediately and does downstream work in a FastAPI background task.
- Successfully routed OneNote recordings are added idempotently to the scoping inbox.
- Saved OneNote destination defaults are stored locally in `user_settings.json` and take precedence over the notebook/section names in `config.yaml`.
- Manual routing jobs are stored locally in `pending_jobs/` when `onenote.manual_selection` is enabled.
- Speakr pull calls are concurrent.
- Ollama failures fall back to a reduced page and still try to preserve output.
- If OneNote COM fails, the service writes the generated OneNote XML body into the system temp directory and logs the error.

## Next Recommended Work

1. Add unit tests for signature validation, Speakr response parsing, and page generation.
2. Validate `win32com` calls on a Windows machine with desktop OneNote installed.
3. Replace the placeholder OneNote XML generation with tested OneNote page content updates against real hierarchy XML.
4. Add delivery deduplication by `Speakr-Delivery-Id`.
5. Add retry persistence for failed deliveries.
