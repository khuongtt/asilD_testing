import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ORCH = os.path.join(ROOT, "asil-d-orchestrator", "src", "orchestrator.py")
WORKSPACE = os.path.join(ROOT, "asil-d-orchestrator")
PROJECT = os.path.join(WORKSPACE, "tests")


class OrchestratorSmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.graph = os.path.join(self.tmp, ".graph")
        self.evidence = os.path.join(self.tmp, "evidence")
        os.makedirs(self.graph, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, extra_args=None):
        if extra_args and extra_args[0] == "init":
            cmd = [sys.executable, ORCH, "init", PROJECT, "--workspace", self.tmp] + extra_args[1:]
        else:
            cmd = [sys.executable, ORCH, PROJECT, "--workspace", self.tmp] + (extra_args or [])
        return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)

    def test_bootstrap_creates_graph(self):
        r = self._run(["--force"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.graph, "manifest.json")))
        self.assertTrue(os.path.exists(os.path.join(self.graph, "bootstrap_state.json")))

    def test_init_subcommand(self):
        r = self._run(["init", "--force"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Mode: BOOTSTRAP", r.stdout)

    def test_incremental_cache_hit(self):
        self._run(["--force"])
        r2 = self._run([])
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("Cache hit rate", r2.stdout)


if __name__ == "__main__":
    unittest.main()
