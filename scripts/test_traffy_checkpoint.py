"""Run Traffy checks plus ThaiWater regression checks without editing team logs."""

import contextlib
import io
import json
import logging
import platform
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import config.config as shared_config


def test_ids(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from test_ids(item)
        else:
            yield item.id()


def main():
    output = io.StringIO()
    original_logs_dir = shared_config.LOGS_DIR
    with tempfile.TemporaryDirectory(prefix="traffy_checkpoint_tests_") as temp_dir:
        shared_config.LOGS_DIR = Path(temp_dir)
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                suite = unittest.defaultTestLoader.loadTestsFromNames([
                    "tests.test_traffy", "tests.test_traffy_delivery", "tests.test_traffy_issue5",
                    "tests.test_traffy_full_backfill", "tests.test_ingest_thaiwater",
                ])
                names = list(test_ids(suite))
                result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
        finally:
            # Windows cannot remove a temporary directory while FileHandler
            # still owns ingestion.log. Release it before context cleanup.
            logging.shutdown()
            shared_config.LOGS_DIR = original_logs_dir
    report = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": "python -X utf8 -m scripts.test_traffy_checkpoint",
        "python_version": platform.python_version(),
        "tests_run": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped),
        "success": result.wasSuccessful(),
        "network_used": False, "s3_test_boundary": "in-memory SDK fixture, not live AWS",
        "test_ids": names,
    }
    path = Path("evidence/traffy/unit_tests.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output.getvalue())
    print(json.dumps({k: v for k, v in report.items() if k != "test_ids"}, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
