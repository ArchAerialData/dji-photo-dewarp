"""The alias bridge must let the CLI finish its graceful cancellation."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock


class LauncherTests(unittest.TestCase):
    def test_console_interrupt_waits_for_graceful_child_exit(self):
        path = Path(__file__).resolve().parents[1] / "scripts/dewarp.py"
        spec = importlib.util.spec_from_file_location("dewarp_alias_bridge", path)
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        child = MagicMock()
        child.wait.side_effect = [KeyboardInterrupt, 130]
        child.__enter__.return_value = child
        with patch.object(launcher.subprocess, "Popen", return_value=child):
            self.assertEqual(launcher._run_in_environment(["python", "app"]), 130)
        self.assertEqual(child.wait.call_count, 2)
        child.kill.assert_not_called()
        child.terminate.assert_not_called()
