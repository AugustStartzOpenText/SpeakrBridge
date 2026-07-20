from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from config import load_config
from scoping.catalog import ScopingTemplateCatalog
from scoping.word_writer import WordScopingWriter

BASE_DIR = Path(__file__).resolve().parent


def recording_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 100:
        raise argparse.ArgumentTypeError("limit must be between 1 and 100")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and generate template-driven scoping documents")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List configured scoping templates and project modes")

    recordings_parser = subparsers.add_parser(
        "recordings",
        help="List recent completed Speakr recordings and their IDs",
    )
    recordings_parser.add_argument("--limit", type=recording_limit, default=10, metavar="N")
    recordings_parser.add_argument("--query", help="Filter recordings by title or participant")

    inspect_parser = subparsers.add_parser("inspect", help="Validate a source template using Microsoft Word")
    inspect_parser.add_argument("template_id")

    extract_parser = subparsers.add_parser(
        "extract",
        help="Extract grounded answers from the latest Speakr recording content",
    )
    extract_parser.add_argument("template_id")
    extract_parser.add_argument("mode", choices=["install", "upgrade"])
    extract_parser.add_argument("recording_id", type=int)
    extract_parser.add_argument("--output", type=Path, help="Destination extraction JSON path")

    generate_parser = subparsers.add_parser("generate", help="Create a populated DOCX from a values JSON file")
    generate_parser.add_argument("template_id")
    generate_parser.add_argument("mode", choices=["install", "upgrade"])
    value_source = generate_parser.add_mutually_exclusive_group(required=True)
    value_source.add_argument("--values", type=Path, help="JSON object keyed by manifest field id")
    value_source.add_argument("--extraction", type=Path, help="Validated extraction JSON from the extract command")
    generate_parser.add_argument(
        "--include-inferred",
        action="store_true",
        help="Also populate inferred values; default writes only source-supported found values",
    )
    generate_parser.add_argument("--output", type=Path, help="Destination DOCX path")
    return parser


def load_values(path: Path) -> dict[str, str | bool]:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Scoping values file must contain a JSON object")
    return {str(key): value for key, value in raw.items()}


def default_output_path(template_name: str, mode: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", template_name).strip("_") or "scoping"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return BASE_DIR / "generated" / "scoping" / f"{safe_name}_{mode}_{stamp}.docx"


def default_extraction_path(recording_id: int, template_id: str, mode: str) -> Path:
    return (
        BASE_DIR
        / "generated"
        / "scoping"
        / "extractions"
        / f"recording_{recording_id}_{template_id}_{mode}.json"
    )


async def extract_recording(args: argparse.Namespace, template) -> Path:
    from scoping.extraction import ScopingExtractor
    from speakr_client import SpeakrClient

    config = load_config()
    bundle = await SpeakrClient(config.speakr).fetch_recording_bundle(args.recording_id)
    result = await ScopingExtractor(
        config.ollama,
        progress=lambda batch, total: print(
            f"Extracting with {config.ollama.model}: batch {batch}/{total}...",
        ),
    ).extract(
        bundle=bundle,
        template=template,
        mode=args.mode,
    )
    output_path = (args.output or default_extraction_path(args.recording_id, template.id, args.mode)).resolve()
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite extraction result: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return output_path


async def list_recordings(args: argparse.Namespace) -> None:
    from speakr_client import SpeakrClient

    config = load_config()
    recordings = await SpeakrClient(config.speakr).list_recordings(
        limit=args.limit,
        query=args.query,
    )
    if not recordings:
        print("No completed Speakr recordings found.")
        return

    print("ID | MEETING DATE | STATUS | TITLE")
    for recording in recordings:
        meeting_date = recording.meeting_date.isoformat() if recording.meeting_date else "unknown"
        print(f"{recording.id} | {meeting_date} | {recording.status or 'unknown'} | {recording.title or ''}")


def main() -> int:
    args = build_parser().parse_args()
    catalog = ScopingTemplateCatalog(base_dir=BASE_DIR)

    if args.command == "list":
        for template in catalog.list_templates():
            source_status = "available" if template.source_file().is_file() else "missing"
            print(f"{template.id} | {template.name} | version {template.version} | source {source_status}")
            for mode in template.project_modes:
                print(f"  {mode.id}: {mode.label}")
        return 0

    if args.command == "recordings":
        asyncio.run(list_recordings(args))
        return 0

    template = catalog.get(args.template_id)
    if args.command == "inspect":
        payload = WordScopingWriter().inspect(template)
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "extract":
        print(asyncio.run(extract_recording(args, template)))
        return 0

    if args.extraction:
        from scoping.extraction import ScopingExtractionResult, extraction_to_word_values

        extraction = ScopingExtractionResult.model_validate_json(
            args.extraction.read_text(encoding="utf-8")
        )
        if extraction.mode != args.mode:
            raise ValueError(
                f"Extraction mode {extraction.mode!r} does not match requested mode {args.mode!r}"
            )
        values = extraction_to_word_values(
            result=extraction,
            template=template,
            include_inferred=args.include_inferred,
        )
    else:
        values = load_values(args.values)
    output_path = args.output or default_output_path(template.name, args.mode)
    generated_path = WordScopingWriter().generate(
        template=template,
        mode=args.mode,
        values=values,
        output_path=output_path,
    )
    print(generated_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
