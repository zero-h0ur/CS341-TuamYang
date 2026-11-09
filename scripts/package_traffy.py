"""Build a minimal Lambda ZIP, without data, credentials or ThaiWater assets."""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

FILES = (
    "src/__init__.py", "src/storage.py", "src/traffy_client.py",
    "src/traffy_filter.py", "src/ingest_traffy.py",
    "config/__init__.py", "config/config.py", "config/traffy.py",
)


def build(output):
    root = Path(__file__).resolve().parents[1]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name in FILES:
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (root / name).read_bytes())
    return {"path": str(output), "bytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "handler": "src.ingest_traffy.lambda_handler", "files": list(FILES)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist/ingest_traffy.zip")
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2))
