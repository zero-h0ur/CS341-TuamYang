"""Stage clean Lambda source directories under .aws-sam/stage/{thaiwater,traffy}.

Prevents AWS SAM from packaging data/, .git/, evidence/, or .env into Lambda bundles.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.package_traffy import FILES as TRAFFY_FILES  # noqa: E402

THAIWATER_DIRS = ("src", "config", "reference")
IGNORE_PATTERNS = (
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*$py.class",
    ".DS_Store",
    "Thumbs.db",
    ".env",
    ".env.*",
)
FORBIDDEN_NAMES = {"data", ".git", "evidence", ".env"}


def stage_thaiwater(root: Path, dest: Path) -> list[str]:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    ignore_fn = shutil.ignore_patterns(*IGNORE_PATTERNS)
    for folder in THAIWATER_DIRS:
        src_dir = root / folder
        if not src_dir.is_dir():
            raise FileNotFoundError(f"Required directory missing: {src_dir}")
        shutil.copytree(src_dir, dest / folder, ignore=ignore_fn)
    return sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())


def stage_traffy(root: Path, dest: Path) -> list[str]:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for rel_path in TRAFFY_FILES:
        src_file = root / rel_path
        if not src_file.is_file():
            raise FileNotFoundError(f"Required Traffy file missing: {src_file}")
        target_file = dest / rel_path
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, target_file)
    return sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())


def _assert_clean_stage(stage_dir: Path) -> None:
    for item in stage_dir.rglob("*"):
        if item.name in FORBIDDEN_NAMES:
            raise RuntimeError(f"Forbidden path detected in staged bundle: {item}")


def stage_all(root: Path = PROJECT_ROOT, stage_dir: Path | None = None) -> dict:
    root = Path(root).resolve()
    stage_dir = Path(stage_dir) if stage_dir is not None else root / ".aws-sam" / "stage"
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)

    thaiwater_files = stage_thaiwater(root, stage_dir / "thaiwater")
    traffy_files = stage_traffy(root, stage_dir / "traffy")
    _assert_clean_stage(stage_dir)

    return {
        "stage_dir": str(stage_dir),
        "thaiwater": {
            "path": str(stage_dir / "thaiwater"),
            "file_count": len(thaiwater_files),
        },
        "traffy": {
            "path": str(stage_dir / "traffy"),
            "file_count": len(traffy_files),
            "files": traffy_files,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage-dir",
        default=str(PROJECT_ROOT / ".aws-sam" / "stage"),
        help="Destination directory for staged Lambda bundles",
    )
    args = parser.parse_args()
    summary = stage_all(root=PROJECT_ROOT, stage_dir=Path(args.stage_dir))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
