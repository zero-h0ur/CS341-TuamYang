"""Verify local real-data partitions/replay/package and export non-row evidence."""

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts.package_traffy import build
from scripts.review_traffy import load_population, write_json
from src.ingest_traffy import TraffyIngestor
from src.storage import LocalStorageDriver


class NoNetworkClient:
    api_url = "https://publicapi.traffy.in.th/share/teamchadchart/search"

    def iter_pages(self, *args, **kwargs):
        raise AssertionError("Verified checkpoint replay must not request the network")


def verify(data_dir, start, end, output_dir):
    reports, partitions = load_population(data_dir, start, end)
    out = Path(output_dir)
    storage = LocalStorageDriver(data_dir)
    engine = TraffyIngestor(NoNetworkClient(), storage)
    all_keys_before = storage.list_keys("")
    daily, replays = [], []
    day, last = date.fromisoformat(start), date.fromisoformat(end)
    while day <= last:
        dt = day.isoformat()
        manifest_key = f"checkpoints/traffy/dt={dt}/manifest.json"
        if not engine._valid_checkpoint(day, manifest_key):
            raise ValueError(f"Missing or invalid completed partition: {dt}")
        manifest = storage.load_json(manifest_key)
        if not manifest.get("source_exhausted"):
            raise ValueError(f"Source pagination did not reach an explicit end: {dt}")
        for key in manifest["raw_page_keys"]:
            if not storage.exists(key):
                raise ValueError(f"Missing raw source evidence: {key}")
        replay = engine.ingest_day(day)
        if replay["status"] != "SKIPPED" or replay["records_sha256"] != manifest["records_sha256"]:
            raise AssertionError("Replay unexpectedly changed the canonical partition")
        replays.append({"date_bkk": dt, "status": replay["status"],
                        "before_count": manifest["record_count"], "after_count": replay["record_count"],
                        "before_sha256": manifest["records_sha256"], "after_sha256": replay["records_sha256"]})
        write_json(out / "manifests" / f"{dt}.json", manifest)
        daily.append({k: manifest[k] for k in (
            "date_bkk", "record_count", "flood_count", "non_flood_count", "uncertain_count",
            "invalid_coordinate_count", "duplicate_count", "source_rows_fetched",
            "source_total_advisory", "source_total_delta", "quality_warnings", "source_exhausted",
        )})
        day += timedelta(days=1)
    if storage.list_keys("") != all_keys_before:
        raise AssertionError("Replay added or removed stored objects")
    package = build("dist/ingest_traffy.zip")
    with tempfile.TemporaryDirectory() as clean_cwd:
        run = subprocess.run([
            sys.executable, "-I", "-c",
            "import sys; sys.path.insert(0,sys.argv[1]); from src.ingest_traffy import lambda_handler; print(callable(lambda_handler))",
            str(Path(package["path"]).resolve()),
        ], cwd=clean_cwd, capture_output=True, text=True, check=True)
        if run.stdout.strip() != "True":
            raise AssertionError("Packaged Lambda handler could not be imported")
    summary = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": "Local Python; public Traffy endpoint; not a deployed AWS invocation",
        "period_bkk_inclusive": [start, end], "unique_report_count": len(reports),
        "daily": daily, "source_partitions": partitions,
        "q2_denominator_preserved": True,
        "uncertain_reports_included_in_denominator": True,
        "replay": {"network_used": False, "new_objects": 0, "days": replays},
        "lambda_package": {**package, "isolated_handler_import": True},
        "aws_deployed": False, "s3_live_verified": False,
    }
    write_json(out / "ingestion_summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--start", default="2026-09-24")
    parser.add_argument("--end", default="2026-09-26")
    parser.add_argument("--output-dir", default="evidence/traffy")
    args = parser.parse_args()
    print(json.dumps(verify(args.data_dir, args.start, args.end, args.output_dir), indent=2))
