#!/usr/bin/env python3
"""Run Armbruster LLM column extraction one page at a time with a watchdog.

The TypeScript extractor is resumable because every page writes its own JSON.
This runner keeps long OpenRouter/Gemini calls from blocking the whole batch:
if one page times out, it records the page and moves on.

Usage:
    python pipeline-api/run_armbruster_llm_pages.py --page-range 105-222
    python pipeline-api/run_armbruster_llm_pages.py --page-range 110-110 --timeout 420
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
PIPELINE_LLM_DIR = ROOT_DIR / "pipeline-llm"
OUTPUT_DIR = ROOT_DIR / "output" / "armbruster" / "llm-columns"
REPORT_PATH = ROOT_DIR / "output" / "armbruster" / "llm-columns-run-report.json"


def parse_page_range(value: str) -> tuple[int, int]:
    parts = value.split("-")
    start = int(parts[0])
    end = int(parts[1]) if len(parts) > 1 else start
    return start, end


def page_output_path(page: int) -> Path:
    return OUTPUT_DIR / f"page_{page:04d}.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def text_tail(value: object, length: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[-length:]
    return str(value)[-length:]


def run_page(page: int, timeout: int, force: bool) -> dict:
    if page_output_path(page).exists() and not force:
        return {"page": page, "status": "cached"}

    command = ["npx", "tsx", "src/armbruster-columns.ts", "--pages", f"{page}-{page}"]
    if force:
        command.append("--force")

    try:
        result = subprocess.run(
            command,
            cwd=PIPELINE_LLM_DIR,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "page": page,
            "status": "timeout",
            "timeout_seconds": timeout,
            "stdout_tail": text_tail(error.stdout, 2000),
            "stderr_tail": text_tail(error.stderr, 2000),
        }

    status = "ok" if result.returncode == 0 and page_output_path(page).exists() else "failed"
    return {
        "page": page,
        "status": status,
        "returncode": result.returncode,
        "stdout_tail": text_tail(result.stdout, 3000),
        "stderr_tail": text_tail(result.stderr, 3000),
    }


def write_report(results: list[dict]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": utc_now(),
        "summary": {
            "total_pages": len(results),
            "ok": sum(1 for item in results if item["status"] == "ok"),
            "cached": sum(1 for item in results if item["status"] == "cached"),
            "failed": sum(1 for item in results if item["status"] in {"failed", "timeout"}),
        },
        "pages": results,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Armbruster LLM extraction with page-level timeout.")
    parser.add_argument("--page-range", required=True, help="Page range, e.g. 105-222")
    parser.add_argument("--timeout", type=int, default=360, help="Seconds per page before skipping it")
    parser.add_argument("--force", action="store_true", help="Reprocess pages even when page JSON already exists")
    args = parser.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("Error: OPENROUTER_API_KEY is required.", file=sys.stderr)
        sys.exit(1)

    start, end = parse_page_range(args.page_range)
    results = []
    for page in range(start, end + 1):
        result = run_page(page, args.timeout, args.force)
        results.append(result)
        write_report(results)
        print(f"page {page}: {result['status']}", flush=True)

    failed = [item for item in results if item["status"] in {"failed", "timeout"}]
    if failed:
        print(f"Failed pages: {[item['page'] for item in failed]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
