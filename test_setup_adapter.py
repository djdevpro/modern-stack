import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("staged_setup", Path(__file__).with_name("setup.py"))
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupAdapterTests(unittest.TestCase):
    def setUp(self):
        self.store = {name: "fixture-" + name for name in setup.SECRETS}

    def test_infra_maps_distinct_state_provider_and_application_credentials(self):
        with patch.object(setup, "load_store", return_value=self.store), patch.object(setup.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertEqual(setup.terraform("infra", ["plan", "-target=minio_s3_bucket_policy.state_application_isolation"]), 0)
            env = run.call_args.kwargs["env"]
            self.assertEqual(env["TF_VAR_state_s3_access_key"], self.store["TFSTATE_ACCESS_KEY_ID"])
            self.assertEqual(env["TF_VAR_state_s3_secret_key"], self.store["TFSTATE_SECRET_ACCESS_KEY"])
            self.assertEqual(env["TF_VAR_drive_s3_access_key"], self.store["DRIVE_S3_ACCESS_KEY_ID"])
            self.assertEqual(env["TF_VAR_drive_s3_secret_key"], self.store["DRIVE_S3_SECRET_ACCESS_KEY"])
            self.assertNotEqual(env["TF_VAR_state_s3_access_key"], env["TF_VAR_drive_s3_access_key"])

    def test_application_plan_and_apply_require_guard_before_terraform(self):
        for stack in ("drive", "tabula"):
            for command in ("plan", "apply"):
                with self.subTest(stack=stack, command=command), patch.object(setup, "load_store", return_value=self.store), patch.object(setup, "check_drive", side_effect=SystemExit(1)), patch.object(setup.subprocess, "run") as run:
                    with self.assertRaises(SystemExit): setup.terraform(stack, [command])
                    run.assert_not_called()

    def test_state_address_list_remains_available_before_isolation_repair(self):
        with patch.object(setup, "load_store", return_value=self.store), patch.object(setup, "check_drive") as check, patch.object(setup.subprocess, "run") as run:
            run.return_value.returncode = 0
            setup.terraform("drive", ["state", "list"])
            check.assert_not_called()
            run.assert_called_once()

    def test_application_provider_never_receives_state_credentials(self):
        for stack in ("drive", "tabula"):
            with self.subTest(stack=stack), patch.dict(setup.os.environ, {}, clear=True), patch.object(setup, "load_store", return_value=self.store), patch.object(setup.subprocess, "run") as run:
                setup.terraform(stack, ["state", "list"])
                env = run.call_args.kwargs["env"]
                self.assertEqual(env["AWS_ACCESS_KEY_ID"], self.store["TFSTATE_ACCESS_KEY_ID"])
                self.assertEqual(env["TF_VAR_drive_s3_access_key"], self.store["DRIVE_S3_ACCESS_KEY_ID"])
                self.assertNotIn("TF_VAR_state_s3_access_key", env)


if __name__ == "__main__":
    unittest.main()
