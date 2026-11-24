"""Run the independent fixed-margin contract tests in the normal CI suite."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class FixedMarginRankReplayTest(unittest.TestCase):
    def test_research_contract(self):
        repo=Path(__file__).resolve().parents[2]
        result=subprocess.run([sys.executable,'-m','unittest','discover','-s',
                               str(repo/'research/20261124-fixed-goal-margin'),'-p','test_*.py'],
                              cwd=repo,env=dict(os.environ,PYTHONPATH=str(repo/'runtime')),
                              capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':
    unittest.main()
