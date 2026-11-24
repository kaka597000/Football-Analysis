"""Run the isolated research contract tests through the normal repository CI entry point."""
from pathlib import Path
import os
import subprocess
import sys
import unittest


class PriorShotDroughtReplayTest(unittest.TestCase):
    def test_isolated_replay_contracts(self):
        repo = Path(__file__).resolve().parents[2]
        study = repo / "research/20261124-prior-shot-drought"
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(study), "-p", "test_*.py"],
            cwd=repo, env=dict(os.environ, PYTHONPATH=str(repo / "runtime")),
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
