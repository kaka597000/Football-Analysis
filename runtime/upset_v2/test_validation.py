import unittest,json,tempfile,copy
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from upset_v2 import train
class ValidationTest(unittest.TestCase):
 @unittest.skipUnless(Path('/workspace/WCPP/reports/python-upset-20261013/enriched-history-research-xg.json').is_file() and Path('/workspace/WCPP/models/upset-20261013/esp.1/report.json').is_file(), 'external upset training history and prior report required')
 def test_test_outcomes_do_not_select_models_blends_or_hyperparameters(self):
  # Perturbed real history is a software test, not a model-performance result.
  rows=json.loads(Path('/workspace/WCPP/reports/python-upset-20261013/enriched-history-research-xg.json').read_text());rows=[r for r in rows if r['cohort']=='esp.1'];prior=json.loads(Path('/workspace/WCPP/models/upset-20261013/esp.1/report.json').read_text());changed=copy.deepcopy(rows)
  for r in changed:
   if r['date']>=prior['date_split']['test_start']:r['home_goals']=(r['home_goals']+2)%5
  with tempfile.TemporaryDirectory() as d:
   a=Path(d)/'a';b=Path(d)/'b';a.mkdir();b.mkdir();r1,_=train.train(rows,prior,a);r2,_=train.train(changed,prior,b)
  self.assertEqual(r1['selected'],r2['selected']);self.assertEqual(r1['validation_candidates'],r2['validation_candidates']);self.assertEqual(r1['inner_optuna_trials'],r2['inner_optuna_trials']);self.assertFalse(r1['acceptance_gate_passed']);self.assertFalse(r2['acceptance_gate_passed'])
if __name__=='__main__':unittest.main()
