"""Expose the isolated study's tests to the existing repository runner."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


class ResearchDiscovery(unittest.TestCase):
    def test_corner_study(self):
        root = Path(__file__).resolve().parents[2]
        suite = root / "research/20261125-corner-opportunities"
        process = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-v"],
            cwd=suite,
            env=dict(os.environ, PYTHONPATH=str(root / "runtime")),
            capture_output=True,
            text=True,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
