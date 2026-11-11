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


class TestSamConfigContract(unittest.TestCase):
    """Seam B (5.3): samconfig.toml contract for dev (Learner Lab) and prod."""

    def test_samconfig_dev_and_prod_environments(self):
        import tomllib

        config_path = PROJECT_ROOT / "samconfig.toml"
        self.assertTrue(config_path.is_file(), "samconfig.toml must exist")
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))

        dev_params = data["dev"]["deploy"]["parameters"]
        self.assertEqual(dev_params["stack_name"], "tuamyang-dev")
        self.assertEqual(dev_params["region"], "us-east-1")
        self.assertEqual(dev_params["capabilities"], "CAPABILITY_IAM")
        self.assertIn('Stage="dev"', dev_params["parameter_overrides"])
        self.assertIn('ExistingRoleName="LabRole"', dev_params["parameter_overrides"])
        self.assertIn('CreateDataBucket="true"', dev_params["parameter_overrides"])
        self.assertIn('EnableStateMachine="true"', dev_params["parameter_overrides"])

        prod_params = data["prod"]["deploy"]["parameters"]
        self.assertEqual(prod_params["stack_name"], "tuamyang-prod")
        self.assertEqual(prod_params["region"], "ap-southeast-1")
        self.assertEqual(prod_params["capabilities"], "CAPABILITY_IAM")
        self.assertIn('Stage="prod"', prod_params["parameter_overrides"])
        self.assertIn('ExistingRoleName=""', prod_params["parameter_overrides"])
        self.assertIn('CreateDataBucket="true"', prod_params["parameter_overrides"])
        self.assertIn('EnableStateMachine="true"', prod_params["parameter_overrides"])


class TestBootstrapOidcContract(unittest.TestCase):
    """Seam B (5.4): infrastructure/bootstrap/github-oidc.yaml contract."""

    def test_bootstrap_template_roles_trust_and_permissions(self):
        import re

        oidc_path = PROJECT_ROOT / "infrastructure" / "bootstrap" / "github-oidc.yaml"
        self.assertTrue(oidc_path.is_file(), "infrastructure/bootstrap/github-oidc.yaml must exist")
        text = oidc_path.read_text(encoding="utf-8")

        for param in ("CreateOidcProvider:", "GitHubOrg:", "GitHubRepo:"):
            self.assertIn(param, text)

        for resource in ("TuamYangGitHubDeployerRole:", "TuamYangCfnExecutionRole:"):
            self.assertIn(resource, text)

        # Verify OIDC trust policy subjects and audience
        self.assertIn("token.actions.githubusercontent.com:aud", text)
        self.assertIn("sts.amazonaws.com", text)
        self.assertIn("token.actions.githubusercontent.com:sub", text)
        self.assertIn("environment:production", text)

        # Verify required permissions on TuamYangCfnExecutionRole for Issue #1 (PR #12)
        for action in (
            "states:*",
            "events:PutRule",
            "events:PutTargets",
            "events:DeleteRule",
            "events:RemoveTargets",
            "events:DescribeRule",
            "iam:CreateRole",
            "iam:DeleteRole",
            "iam:PutRolePolicy",
            "iam:DeleteRolePolicy",
            "iam:AttachRolePolicy",
            "iam:DetachRolePolicy",
            "iam:PassRole",
        ):
            self.assertIn(action, text, f"Missing required IAM permission {action}")

        # Verify required Outputs
        self.assertIn("DeployerRoleArn:", text)
        self.assertIn("CfnExecutionRoleArn:", text)

        # Verify no hardcoded 12-digit AWS account IDs
        self.assertIsNone(re.search(r"\b\d{12}\b", text))


class TestCiWorkflowContract(unittest.TestCase):
    """Seam B (5.5): .github/workflows/ci.yml contract and test_traffy.yml deduplication."""

    def test_ci_workflow_has_ci_job_and_required_steps_without_aws_deploy(self):
        ci_path = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
        self.assertTrue(ci_path.is_file(), ".github/workflows/ci.yml must exist")
        text = ci_path.read_text(encoding="utf-8")

        self.assertIn("pull_request:", text)
        self.assertIn("push:", text)
        self.assertIn("\n  ci:\n", text)
        self.assertIn("python -X utf8 -m unittest discover -s tests -v", text)
        self.assertIn("./tests/test_deploy_traffy.ps1", text)
        self.assertIn("python -X utf8 -m scripts.package_traffy", text)
        self.assertIn("python -X utf8 scripts/stage_lambda.py", text)
        self.assertIn("sam validate", text)
        self.assertIn("sam build", text)

        # Hard rules: never deploy in CI
        self.assertNotIn("sam deploy", text)
        self.assertNotIn("--config-env", text)

    def test_traffy_workflow_does_not_duplicate_pull_request_trigger(self):
        traffy_wf = (PROJECT_ROOT / ".github" / "workflows" / "test_traffy.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("pull_request:", traffy_wf)


class TestCdProdWorkflowContract(unittest.TestCase):
    """Seam B (5.6): .github/workflows/cd-prod.yml contract and hard safety rules."""

    def test_cd_prod_workflow_triggers_oidc_and_smoke_checks(self):
        import re

        cd_path = PROJECT_ROOT / ".github" / "workflows" / "cd-prod.yml"
        self.assertTrue(cd_path.is_file(), ".github/workflows/cd-prod.yml must exist")
        text = cd_path.read_text(encoding="utf-8")

        # Triggers & paths
        self.assertIn("push:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("pull_request:", text)
        for path_filter in (
            "template.yaml",
            "samconfig.toml",
            "src/**",
            "config/**",
            "reference/**",
            "scripts/stage_lambda.py",
            "statemachine/**",
            ".github/workflows/cd-prod.yml",
        ):
            self.assertIn(path_filter, text, f"Missing path filter {path_filter}")

        # Permissions, Concurrency, Environment
        self.assertIn("id-token: write", text)
        self.assertIn("contents: read", text)
        self.assertIn("group: deploy-prod", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("deploy-prod:", text)
        self.assertIn("environment: production", text)

        # OIDC + SAM Deploy + Smoke verification
        self.assertIn("vars.AWS_DEPLOYER_ROLE_ARN", text)
        self.assertIn("vars.AWS_CFN_EXECUTION_ROLE_ARN", text)
        self.assertIn("ap-southeast-1", text)
        self.assertIn("python -X utf8 scripts/stage_lambda.py", text)
        self.assertIn("sam deploy", text)
        self.assertIn("--config-env prod", text)
        self.assertIn("--no-confirm-changeset", text)
        self.assertIn("--no-fail-on-empty-changeset", text)
        self.assertIn("--invocation-type DryRun", text)
        self.assertIn("ENABLED", text)

        # Hard safety rules
        self.assertNotIn("--config-env dev", text)
        self.assertNotIn("delete-stack", text)
        self.assertNotIn("s3 rm", text)
        self.assertNotIn("s3 rb", text)
        self.assertNotIn("backfill", text.lower())
        self.assertIsNone(re.search(r"\b\d{12}\b", text))

    def test_no_workflow_deploys_to_dev(self):
        wf_dir = PROJECT_ROOT / ".github" / "workflows"
        for wf in wf_dir.glob("*.yml"):
            content = wf.read_text(encoding="utf-8")
            self.assertNotIn("--config-env dev", content, f"{wf.name} must not deploy to dev")
            if wf.name != "cd-prod.yml":
                self.assertNotIn(
                    "--config-env prod", content, f"{wf.name} must not deploy to prod"
                )


class TestCicdDocsContract(unittest.TestCase):
    """Seam B (5.7): docs/cicd_setup.md, docs/cicd_team_guide.md, and README.md links."""

    def test_docs_exist_have_no_draft_badge_and_are_linked_from_readme(self):
        import re

        setup_doc = PROJECT_ROOT / "docs" / "cicd_setup.md"
        team_doc = PROJECT_ROOT / "docs" / "cicd_team_guide.md"
        readme = PROJECT_ROOT / "README.md"

        self.assertTrue(setup_doc.is_file(), "docs/cicd_setup.md must exist")
        self.assertTrue(team_doc.is_file(), "docs/cicd_team_guide.md must exist")

        setup_text = setup_doc.read_text(encoding="utf-8")
        team_text = team_doc.read_text(encoding="utf-8")
        readme_text = readme.read_text(encoding="utf-8")

        # Must not contain Draft badge or hardcoded 12-digit AWS account IDs
        for name, content in (("cicd_setup.md", setup_text), ("cicd_team_guide.md", team_text)):
            self.assertNotIn("DRAFT", content.upper(), f"{name} must not have a Draft marker")
            self.assertIsNone(
                re.search(r"\b\d{12}\b", content), f"{name} must not hardcode AWS account ID"
            )

        # Verify key topics in cicd_setup.md
        for term in (
            "TuamYangGitHubDeployerRole",
            "TuamYangCfnExecutionRole",
            "AWS_DEPLOYER_ROLE_ARN",
            "AWS_CFN_EXECUTION_ROLE_ARN",
            "CreateOidcProvider=false",
            "cicd_team_guide.md",
        ):
            self.assertIn(term, setup_text, f"cicd_setup.md missing {term}")

        # Verify key topics in cicd_team_guide.md
        for term in (
            "sam deploy --config-env dev",
            "sam deploy --config-env prod",
            "CreateDataBucket=false",
            "PhurinKaewpuangsek",
            "zero-h0ur",
            "cicd_setup.md",
        ):
            self.assertIn(term, team_text, f"cicd_team_guide.md missing {term}")

        # Verify README.md links both documents
        self.assertIn("docs/cicd_setup.md", readme_text)
        self.assertIn("docs/cicd_team_guide.md", readme_text)


if __name__ == "__main__":
    unittest.main()

