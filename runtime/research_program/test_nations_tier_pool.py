"""Run the isolated research regressions as part of repository discovery."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class NationsTierPool(unittest.TestCase):
    def test_research_regressions(self):
        root = Path(__file__).resolve().parents[2]
        suite = root / 'research' / '20261124-nations-tier-fusion'
        process = subprocess.run(
            [sys.executable, '-m', 'unittest', '-v', 'test_study'],
            cwd=suite, env=dict(os.environ, PYTHONPATH=str(root / 'runtime')),
            text=True, capture_output=True,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
