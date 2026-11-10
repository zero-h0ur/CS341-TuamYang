"""
Run the Issue #1 pipeline locally (same steps as statemachine/pipeline.asl.json).

Works on a COPY of data/incoming/ so the committed data/ tree is never modified.

    python -X utf8 scripts/run_pipeline_local.py                       # all committed incoming batches
    python -X utf8 scripts/run_pipeline_local.py --inject-pr7-defect   # + one batch with the PR #7 bank bug
    python -X utf8 scripts/run_pipeline_local.py --evidence evidence/orchestration

--evidence copies the resulting reports/markers/quarantine files into that folder.
"""

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.validation.pipeline import run_local  # noqa: E402

DEFECT_KEY = "incoming/thaiwater/dt=2026-11-10/hour=00/waterlevel_tw-wa-20261110-002600-dead01.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(ROOT / "data"), help="source data root (read only)")
    ap.add_argument("--work-dir", default=None, help="where to run (default: temp dir)")
    ap.add_argument("--inject-pr7-defect", action="store_true",
                    help="add a copy of a real waterlevel batch with min_bank set to ground_level (the PR #7 bug)")
    ap.add_argument("--evidence", default=None, help="copy outputs here")
    args = ap.parse_args()

    work = Path(args.work_dir or tempfile.mkdtemp(prefix="tuamyang-pipeline-"))
    src_incoming = Path(args.data_dir) / "incoming"
    shutil.copytree(src_incoming, work / "incoming", dirs_exist_ok=True)
    traffy_ck = Path(args.data_dir) / "checkpoints" / "traffy"
    if traffy_ck.exists():
        shutil.copytree(traffy_ck, work / "checkpoints" / "traffy", dirs_exist_ok=True)

    keys = sorted(p.relative_to(work).as_posix() for p in (work / "incoming").rglob("*.json"))
    if args.inject_pr7_defect:
        good = next(k for k in keys if "/waterlevel_" in k)
        env = json.loads((work / good).read_text(encoding="utf-8"))
        env["batch_id"] = "tw-wa-20261110-002600-dead01"
        env["_injected_defect"] = "min_bank_msl replaced by ground_level_msl (PR #7 bug) for the quarantine demo"
        for r in env["data"]:
            r["min_bank_msl"] = r["ground_level_msl"]
        (work / DEFECT_KEY).write_text(json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")
        keys.append(DEFECT_KEY)

    results = []
    for key in keys:
        res = run_local(key, base_dir=str(work))
        results.append(res)
        print(f"{res['final_state']:<17} {res.get('validation_status', '-'):<5} "
              f"rows={res.get('row_count', '-'):<5} {key}")
        for rule in res.get("failures", []):
            print(f"{'':>24}FAIL {rule}")
        for rule in res.get("warnings", []):
            print(f"{'':>24}WARN {rule}")

    print("\nReplay (idempotency):")
    for key in keys:
        print(f"  {run_local(key, base_dir=str(work))['final_state']:<9} {key}")
    print(f"\nWork dir: {work}")

    if args.evidence:
        out = Path(args.evidence)
        out.mkdir(parents=True, exist_ok=True)
        for zone in ("processed", "quarantine"):
            if (work / zone).exists():
                shutil.copytree(work / zone, out / zone, dirs_exist_ok=True)
        # trusted/: first 3 rows of each file are enough to show the format
        for p in (work / "trusted").rglob("*.jsonl"):
            dest = out / p.relative_to(work)
            dest.parent.mkdir(parents=True, exist_ok=True)
            lines = p.read_text(encoding="utf-8").splitlines()
            dest.with_suffix(".sample.jsonl").write_text("\n".join(lines[:3]) + "\n", encoding="utf-8")
        summary = [{k: r.get(k) for k in ("key", "dataset", "batch_id", "final_state", "validation_status",
                                         "row_count", "failures", "warnings", "trusted_key",
                                         "quarantine_key", "marker_key")} for r in results]
        (out / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Evidence written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
