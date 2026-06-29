# SpeakrBridge

SpeakrBridge receives local `Speakr` webhook events, pulls recording content from the Speakr API, requests a structured summary from `Ollama`, and writes a formatted page into `OneNote` through the Windows COM API.

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

## Setup

1. Create and activate a Python 3.11+ virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Edit `config.yaml` with your Speakr webhook secret, Speakr API token, Ollama host, and OneNote notebook settings.
4. Start the service:

```bash
python main.py
```

5. Verify the service is up:

```bash
curl http://127.0.0.1:8080/health
```

If you are testing from another machine on your LAN, use the host machine's actual IP instead of `127.0.0.1`. If you are testing from an external webhook provider, you still need to expose this local port through a tunnel or reverse proxy because `0.0.0.0` only makes the service listen on local interfaces; it does not create a public URL.

## Current Notes

- The webhook route returns `202 Accepted` immediately and does downstream work in a FastAPI background task.
- Speakr pull calls are concurrent.
- Ollama failures fall back to a reduced page and still try to preserve output.
- If OneNote COM fails, the service writes the generated OneNote XML body into the system temp directory and logs the error.

## Next Recommended Work

1. Add unit tests for signature validation, Speakr response parsing, and page generation.
2. Validate `win32com` calls on a Windows machine with desktop OneNote installed.
3. Replace the placeholder OneNote XML generation with tested OneNote page content updates against real hierarchy XML.
4. Add delivery deduplication by `Speakr-Delivery-Id`.
5. Add retry persistence for failed deliveries.
