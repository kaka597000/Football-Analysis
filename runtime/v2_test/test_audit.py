import unittest
from audit import profile_history
class Tests(unittest.TestCase):
    def test_duplicate_missing_and_backfill_do_not_claim_no_leakage(self):
        row={'match_id':'x','cohort':'example','date':'2026-01-01','home':'A','away':'B','home_goals':1,'away_goals':0,'observed_at':'2026-02-01T00:00:00Z'}
        p=profile_history([row,row]);self.assertEqual(p['duplicate_match_id_excess'],1);self.assertEqual(p['missing_first_known_at'],2);self.assertEqual(p['observed_after_match_day'],2);self.assertEqual(p['invalid_goals'],0)
    def test_unknown_history_not_invented(self):
        p=profile_history([]);self.assertIsNone(p['date_max']);self.assertEqual(p['rows'],0)
if __name__=='__main__':unittest.main()
