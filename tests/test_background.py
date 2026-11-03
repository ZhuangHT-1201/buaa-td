import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from buaatd.background import _daemon_executable, is_running, terminate


class BackgroundProcessTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows process API test")
    def test_running_check_does_not_spawn_tasklist(self):
        with patch(
            "buaatd.background.subprocess.run",
            side_effect=AssertionError("subprocess should not be used"),
        ):
            self.assertTrue(is_running(os.getpid()))

    @unittest.skipUnless(os.name == "nt", "Windows executable selection test")
    def test_daemon_prefers_pythonw(self):
        executable = Path(_daemon_executable())
        self.assertEqual(executable.name.lower(), "pythonw.exe")

    @unittest.skipUnless(os.name == "nt", "Windows process termination test")
    def test_native_terminate_stops_process(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            terminate(process.pid)
            self.assertIsNotNone(process.wait(timeout=5))
        finally:
            if process.poll() is None:
                process.kill()


if __name__ == "__main__":
    unittest.main()
