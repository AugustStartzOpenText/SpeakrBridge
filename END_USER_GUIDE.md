# SpeakrBridge Daily Workflow

## What You Need

- Speakr installed and configured with Docker.
- SpeakrBridge configured in `config.yaml`.
- Desktop Microsoft OneNote and Word installed on the Windows computer running SpeakrBridge.
- Speakr configured to send its completed-recording webhook to SpeakrBridge.

## Start the Services

### 1. Start Speakr

Open PowerShell in the folder containing Speakr's `docker-compose.yml` or `compose.yml`, then run:

```powershell
docker compose up -d
docker compose ps
```

Confirm the Speakr containers show as running or healthy. Open the normal Speakr web page and confirm
it loads.

### 2. Start SpeakrBridge

Open a second PowerShell window and run:

```powershell
cd "C:\Users\astartz\Documents\Code Projects\SpeakrBridge-feature-scoping-documents"
.\.venv\Scripts\Activate.ps1
python .\main.py
```

If SpeakrBridge is stored somewhere else, use that folder in the `cd` command.

Leave this PowerShell window open. SpeakrBridge must remain running to receive completed recordings,
write OneNote pages, run scoping extraction, and generate Word documents.

To verify SpeakrBridge, open this address on the same Windows computer:

```text
http://127.0.0.1:8080/health
```

The page should display `{"status":"ok"}`.

## Process a Meeting

1. Record or upload the meeting in Speakr.
2. Wait for Speakr to finish the transcript and summary.
3. SpeakrBridge receives the webhook and writes the meeting page to OneNote.
4. After the OneNote page is created, the meeting appears in the Scoping Desk.

## Create a Scoping Document

1. Open the Scoping Desk:

   ```text
   http://127.0.0.1:8080/scoping
   ```

2. If prompted, select **API token**, enter the value of `scoping.api_token` from `config.yaml`, and
   select **Connect**.
3. Find the meeting under **Ready from OneNote**.
4. Select the form and workflow, such as **OpenText Fax** and **Upgrade / Migration**.
5. Select **Extract answers**.
6. Wait for the job to change to **review**. The page refreshes automatically while it is working.
7. Review the found, unknown, and warning counts, then select **Generate Word**.
8. When the job is complete, select **Download DOCX**.
9. Open the downloaded document in Word, review every answer, fill in missing information, and save
   the final customer document.

Select **Dismiss** for a meeting that does not need a scoping document. Dismissing it does not remove
the recording from Speakr or the page from OneNote.

## Use the Page from Another Computer

Use the Windows computer's IP address instead of `127.0.0.1`:

```text
http://<speakrbridge-windows-ip>:8080/scoping
```

Remote access requires `scoping.api_token` in `config.yaml`. Windows Firewall must also allow the
configured SpeakrBridge port, which is `8080` by default.

## Stop the Services

1. In the SpeakrBridge PowerShell window, press `Ctrl+C`.
2. In the Speakr Docker folder, run:

   ```powershell
   docker compose stop
   ```

Use `docker compose up -d` the next time you want to start Speakr. Do not use commands that delete
Docker volumes unless you intentionally want to remove Speakr data.

## Common Problems

- **The meeting is not in the Scoping Desk:** Confirm SpeakrBridge was running when Speakr completed
  the recording and confirm the OneNote page was created successfully.
- **Disconnected or invalid token:** Enter the exact `scoping.api_token` value from `config.yaml`.
- **Extraction fails:** Confirm the Ollama server in `ollama.host` is online and the configured model is
  installed.
- **Word generation fails:** Run SpeakrBridge on Windows and confirm desktop Microsoft Word is
  installed. Word generation does not run inside the Linux Speakr container.
- **The Scoping Desk does not open from another computer:** Use the Windows computer's LAN IP, confirm
  SpeakrBridge listens on `0.0.0.0`, and check Windows Firewall for port `8080`.
