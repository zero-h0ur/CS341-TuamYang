"""Unit tests for Issue #11 CI/CD and IaC deliverables (Seam A & Seam B)."""

import tempfile
import unittest
from pathlib import Path

from scripts.package_traffy import FILES as TRAFFY_FILES

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestStageLambda(unittest.TestCase):
    """Seam A: scripts.stage_lambda.stage_all public interface and .gitignore."""

    def test_stage_all_creates_thaiwater_and_traffy_bundles_cleanly(self):
        from scripts.stage_lambda import stage_all

        with tempfile.TemporaryDirectory() as tmp:
            stage_root = Path(tmp) / ".aws-sam" / "stage"
            # Pre-populate a stale file to verify idempotency / clean rebuild
            stale_file = stage_root / "thaiwater" / "data" / "stale.json"
            stale_file.parent.mkdir(parents=True, exist_ok=True)
            stale_file.write_text("{}", encoding="utf-8")

            summary = stage_all(root=PROJECT_ROOT, stage_dir=stage_root)

            thaiwater_dir = stage_root / "thaiwater"
            traffy_dir = stage_root / "traffy"
            self.assertTrue(thaiwater_dir.is_dir())
            self.assertTrue(traffy_dir.is_dir())

            # ThaiWater bundle must include src/ (including src/validation/), config/, reference/
            self.assertTrue((thaiwater_dir / "src" / "ingest_thaiwater.py").is_file())
            self.assertTrue((thaiwater_dir / "src" / "validation" / "pipeline.py").is_file())
            self.assertTrue((thaiwater_dir / "src" / "validation" / "rules.py").is_file())
            self.assertTrue((thaiwater_dir / "config" / "config.py").is_file())
            self.assertTrue((thaiwater_dir / "reference" / "stations_water.json").is_file())
            self.assertTrue((thaiwater_dir / "reference" / "stations_rain.json").is_file())

            # Traffy bundle must contain the exact FILES from scripts.package_traffy
            staged_traffy_files = sorted(
                p.relative_to(traffy_dir).as_posix()
                for p in traffy_dir.rglob("*")
                if p.is_file()
            )
            self.assertEqual(staged_traffy_files, sorted(TRAFFY_FILES))

            # Forbidden paths/artifacts must never appear in either bundle
            forbidden_top_dirs = {"data", ".git", "evidence", "tests", "docs", "dist"}
            for bundle_dir in (thaiwater_dir, traffy_dir):
                top_names = {p.name for p in bundle_dir.iterdir()}
                self.assertTrue(
                    top_names.isdisjoint(forbidden_top_dirs),
                    f"Forbidden directory found in {bundle_dir}: {top_names & forbidden_top_dirs}",
                )
                for path in bundle_dir.rglob("*"):
                    self.assertNotEqual(path.name, ".env")
                    self.assertNotEqual(path.name, "__pycache__")
                    self.assertNotIn(path.suffix, {".pyc", ".pyo"})

            self.assertIn("thaiwater", summary)
            self.assertIn("traffy", summary)

    def test_gitignore_excludes_aws_sam_directory(self):
        gitignore_text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        lines = [line.strip() for line in gitignore_text.splitlines()]
        self.assertIn(".aws-sam/", lines)


class TestSamTemplateContract(unittest.TestCase):
    """Seam B (5.2): template.yaml contract with Issue #11 and docs/orchestration_validation.md."""

    def setUp(self):
        self.template_path = PROJECT_ROOT / "template.yaml"
        self.assertTrue(self.template_path.is_file(), "template.yaml must exist")
        self.text = self.template_path.read_text(encoding="utf-8")

    def test_required_parameters_and_conditions_match_orchestration_validation_doc(self):
        required_params = (
            "Stage:",
            "DataBucketName:",
            "CreateDataBucket:",
            "ExistingRoleName:",
            "EnableTraffyHourly:",
            "EnableStateMachine:",
        )
        for param in required_params:
            self.assertIn(param, self.text, f"Missing parameter {param}")

        required_conditions = (
            "ShouldCreateDataBucket:",
            "HasCustomDataBucketName:",
            "UseExistingRole:",
            "CreateCustomRoles:",
            "ShouldEnableTraffyHourly:",
            "HasStateMachineDefinition:",
            "CreatePipelineRoles:",
        )
        for cond in required_conditions:
            self.assertIn(cond, self.text, f"Missing condition {cond}")

    def test_required_resources_and_normalized_names_present(self):
        required_resources = (
            "DataBucket:",
            "IngestThaiWaterFunction:",
            "IngestThaiWaterFunctionLogGroup:",
            "IngestThaiWaterFunctionRole:",
            "IngestTraffyFunction:",
            "IngestTraffyFunctionLogGroup:",
            "IngestTraffyFunctionRole:",
            "PipelineStepFunction:",
            "PipelineStepFunctionLogGroup:",
            "PipelineStateMachine:",
            "PipelineIncomingRule:",
            "PipelineStepFunctionRole:",
            "PipelineStateMachineRole:",
            "StateMachineEventBridgeRole:",
        )
        for res in required_resources:
            self.assertIn(res, self.text, f"Missing resource {res}")

        normalized_names = (
            "tuamyang-${Stage}-data-${AWS::AccountId}",
            "tuamyang-${Stage}-ingest-thaiwater",
            "tuamyang-${Stage}-ingest-traffy",
            "tuamyang-${Stage}-pipeline-step",
            "tuamyang-${Stage}-pipeline",
            "tuamyang-${Stage}-thaiwater-waterlevel-15min",
            "tuamyang-${Stage}-thaiwater-rain-1hr",
            "tuamyang-${Stage}-traffy-daily",
            "tuamyang-${Stage}-traffy-hourly",
            "tuamyang-${Stage}-pipeline-incoming",
        )
        for name in normalized_names:
            self.assertIn(name, self.text, f"Missing normalized resource name {name}")

        # Verify EventBridge notification on S3 DataBucket and no ScheduleV2
        self.assertIn("EventBridgeEnabled: true", self.text)
        self.assertNotIn("ScheduleV2", self.text)
        # Verify no hardcoded 12-digit AWS account IDs
        import re
        self.assertIsNone(re.search(r"\b\d{12}\b", self.text))


if __name__ == "__main__":
    unittest.main()

