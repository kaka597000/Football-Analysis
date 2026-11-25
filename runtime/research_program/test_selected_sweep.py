import unittest
from research_program import selected_sweep as s


class SelectedSweepTest(unittest.TestCase):
    def test_label_only_used_for_fit_and_review(self):
        p=dict(primary='1-1',top3=['1-1','1-0','0-1'],wdl='home')
        row=s.point('a','eng.1','2022-01-01',{'candidate':p,'market':{'primary':None}},
                    {'p_first':.1,'p_three':.27},False)
        self.assertAlmostEqual(s.probability(row,s.fit([])),.1)
        hit=dict(row,actual='1-1');miss=dict(row,actual='2-0')
        self.assertGreater(s.probability(row,s.fit([hit]*100)),s.probability(row,s.fit([miss]*100)))
        self.assertEqual(hit['primary'],miss['primary'])

    def test_swapped_seats_and_whole_batch_measurement(self):
        rows=[dict(id=str(i),raw=.3-.02*i,adjusted=.1+.02*i,actual='1-0' if i else '2-0',
                   primary='1-0',top3=['1-0','2-0','1-1']) for i in range(4)]
        batch=dict(date='2021-03-01',n=4,control=s.evaluate_group(rows,'raw'),candidate=s.evaluate_group(rows,'adjusted'))
        result=s.summarize([batch])
        self.assertEqual(result['sweep_change']['net'],1)
        self.assertEqual(result['control']['displayed_three_sweeps'],1)
        self.assertEqual(result['candidate']['primary_sweeps'],1)
        self.assertEqual(s.evaluate_group(rows[:2],'adjusted')['complete_batches'],0)
