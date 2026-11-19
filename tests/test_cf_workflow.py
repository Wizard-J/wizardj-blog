import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


MODULES = Path(__file__).resolve().parents[1] / "deploy" / "cf-select"
RUN = "a" * 32


class PublicationTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.selector = SimpleNamespace(
            ETC=root, LIVE=root / "live", PROFILE=root / "profile",
            ARTIFACT=root / "artifact", USER="proxy",
            replace=lambda path, data, *_: path.write_bytes(data),
            restart=Mock(),
        )
        self.selector.LIVE.write_bytes(b"old-live")
        self.selector.ARTIFACT.write_bytes(b"old-artifact")
        self.selector.PROFILE.write_bytes(b"template")
        self.publisher = Mock()
        spec = importlib.util.spec_from_file_location("cf_test_workflow", MODULES / "cf_workflow.py")
        self.workflow = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            "cf_select": self.selector, "cf_result": SimpleNamespace(record=Mock()),
            "cf_status": SimpleNamespace(validate_summary=lambda summary: summary),
            "subscription_publish": self.publisher,
        }):
            spec.loader.exec_module(self.workflow)
        self.data = {"summary": {"published": False}, "live": "new-live", "artifact": "new-artifact"}

        def activate(raw, live, artifact, gid):
            self.selector.LIVE.write_bytes(live)
            self.selector.ARTIFACT.write_bytes(artifact)
            return True

        self.selector.activate = Mock(side_effect=activate)
        self.workflow.read_draft = Mock(return_value=self.data)
        self.workflow.write_status = Mock()
        self.workflow.DRAFT.touch()

    def test_receipt_follows_verified_publication(self):
        with patch.object(self.workflow.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=42)):
            self.workflow.publish(RUN, scheduled=False)
        self.publisher.main.assert_called_once_with(record_receipt=False)
        self.assertEqual(self.workflow.write_status.call_args.args[1], "published")
        self.assertTrue(self.data["summary"]["published"])
        self.assertFalse(self.workflow.DRAFT.exists())

    def test_failed_verification_restores_local_files(self):
        self.publisher.main.side_effect = RuntimeError
        with patch.object(self.workflow.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=42)):
            with self.assertRaises(RuntimeError):
                self.workflow.publish(RUN, scheduled=False)
        self.assertEqual(self.selector.LIVE.read_bytes(), b"old-live")
        self.assertEqual(self.selector.ARTIFACT.read_bytes(), b"old-artifact")
        self.assertFalse(self.data["summary"]["published"])
        self.selector.restart.assert_called_once()


if __name__ == "__main__":
    unittest.main()
