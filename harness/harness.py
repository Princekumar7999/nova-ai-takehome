"""Your harness. The grader calls exactly these three commands, in a fresh process each time:

    python harness.py ingest <batch_dir> --state-dir <dir>
    python harness.py report --as-of YYYY-MM-DD [--mode supervised|autonomous] [--state-dir <dir>] > report.json
    python harness.py feedback <feedback.json> --state-dir <dir>

- ingest: read every .eml in <batch_dir> (attachments inside) and store what you learn under --state-dir. The process
  is restarted between batches, so everything you need later must be on disk.
- report: print one report.json (grader/schema/report.schema.json) to stdout. Only documents received on or before
  --as-of count. --mode defaults to supervised.
- feedback: take in a person's decisions on what you escalated (feedback.json) and let them change later reports.

The environment gives ANTHROPIC_API_KEY and ANTHROPIC_BASE_URL. Keep the argument names as they are: grader/validate.py
checks them. Replace the NotImplementedError bodies with your code.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path and harness dir is removed from sys.path to avoid shadowing package
ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
while str(HERE) in sys.path:
    sys.path.remove(str(HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.ingestion import IngestionPipeline
from harness.report_builder import build_report
from harness.storage import Storage

DEFAULT_STATE = ".state"


def ingest(batch_dir: str, state_dir: str) -> None:
    pipeline = IngestionPipeline(state_dir)
    pipeline.ingest_batch(batch_dir)


def report(as_of: str, mode: str, state_dir: str) -> dict:
    storage = Storage(state_dir)
    return build_report(storage, as_of=as_of, mode=mode)


def feedback(feedback_file: str, state_dir: str) -> None:
    storage = Storage(state_dir)
    fb_path = Path(feedback_file)
    if fb_path.is_file():
        fb_data = json.loads(fb_path.read_text())
        after_batch = fb_data.get("after_batch", "")
        for item in fb_data.get("items", []):
            item["after_batch"] = after_batch
            storage.data["feedback"].append(item)
        storage.save()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="harness.py", description="Freight invoice audit harness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest", help="read one batch folder into the state dir")
    p.add_argument("batch_dir")
    p.add_argument("--state-dir", default=DEFAULT_STATE)
    p = sub.add_parser("report", help="print report.json for a date")
    p.add_argument("--as-of", required=True, help="YYYY-MM-DD; only documents received by then count")
    p.add_argument("--mode", choices=["supervised", "autonomous"], default="supervised")
    p.add_argument("--state-dir", default=DEFAULT_STATE)
    p = sub.add_parser("feedback", help="take in a person's decisions (feedback.json)")
    p.add_argument("feedback_file")
    p.add_argument("--state-dir", default=DEFAULT_STATE)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "ingest":
            ingest(a.batch_dir, a.state_dir)
        elif a.cmd == "report":
            import json
            json.dump(report(a.as_of, a.mode, a.state_dir), sys.stdout, indent=1)
            sys.stdout.write("\n")
        else:
            feedback(a.feedback_file, a.state_dir)
    except NotImplementedError as e:
        print(f"harness.py {a.cmd}: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
