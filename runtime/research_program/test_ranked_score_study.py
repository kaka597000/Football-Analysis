import unittest

from research_program.ranked_score_study import rank, sweep


class RankedScoresTest(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(match_id=str(n), first_probability=p, kickoff_at=t, primary='1-0',
                          top3=['1-0','1-1','2-1']) for n,p,t in [
            (2,.2,'2026-12-01T18:00Z'),(1,.2,'2026-12-01T17:00Z'),
            (3,.2,'2026-12-01T18:00Z'),(4,.1,'2026-12-01T17:00Z')]]

    def test_lock_uses_probability_then_time_then_id(self):
        self.assertEqual([x['match_id'] for x in rank(self.rows)], ['1','2','3'])
        with self.assertRaises(ValueError):
            rank([dict(self.rows[0],first_probability=float('nan'))])

    def test_backup_hit_does_not_complete_a_first_score_sweep(self):
        scores={'1':'1-0','2':'1-1','3':'1-0'}
        result=sweep(rank(self.rows),scores)
        self.assertEqual((result['primary_hits'],result['primary_sweeps'],result['displayed_three_sweeps']), (2,0,1))
        self.assertEqual(sweep(rank(self.rows[:2]),scores)['complete_batches'],0)
        self.assertEqual(sweep(rank(self.rows),{'1':'1-0'})['pending'],2)
        self.assertEqual(sweep(rank(self.rows),{k:'1-0' for k in scores})['primary_sweeps'],1)
