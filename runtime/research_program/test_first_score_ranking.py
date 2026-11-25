from copy import deepcopy
from datetime import datetime, timezone
import unittest

from research_program import first_score_ranking as model
from research_program import first_score_ranking_study as study
from research_program import first_score_ranking_forward as forward


def fixture(i, day, *, window='recent', actual=(1,1), p=.14):
    pick = dict(primary='1-1',top3=['1-1','1-0','0-1'],wdl='home',total_side=None,total_line=None)
    return dict(id=str(i), date=day, cohort='jpn.1', stage='regular', window=window, actual=list(actual), line=None,
                forecast=dict(candidate=pick, r6=dict(primary='1-0'), market={}),
                quality=dict(candidate=dict(p_first=p, p_three=.36, wdl=[.40,.30,.30], p_observed=.0005)))


class ScoreSeatTests(unittest.TestCase):
    def test_estimate_has_only_pregame_inputs_and_cold_start_preserves_ranking(self):
        r = fixture(1,'2026-03-01')
        original = study.design(r)
        r['actual'] = [3,2]
        r['quality']['candidate']['p_observed'] = .9
        self.assertEqual(study.design(r), original)
        fitted = model.fit([(original, True)] * 100)
        self.assertEqual(fitted['training_hits'],100)
        self.assertAlmostEqual(model.predict(original,fitted),original['p_first'])
        self.assertEqual(model.select([dict(id='b',raw=.2),dict(id='a',raw=.2)], 'raw'),['a','b'])
        with self.assertRaises(ValueError):
            model.inputs('1-1', .9,.15,[.4,.3,.3],None,None)

    def test_future_labels_and_same_date_training_cannot_choose_seats(self):
        training = [fixture(i,'2025-05-01',window='evaluation',actual=(1,1) if i%3 else (2,0),p=.10+(i%7)*.01) for i in range(68)]
        overlap = fixture(100,'2026-08-07',window='evaluation')
        later = [fixture(i,'2026-08-07',p=.11+i*.008) for i in range(10,15)]
        w1,_,days1,_ = study.study(training+[overlap]+later,'2026-11-25')
        changed = deepcopy(later)
        for row in changed:
            row['actual'] = [2,0]
        w2,_,days2,_ = study.study(training+[overlap]+changed,'2026-11-25')
        self.assertEqual(w1['evaluation'],w2['evaluation'])
        self.assertEqual(w1['evaluation']['training_rows'],68)
        self.assertEqual(w1['evaluation']['status'],'FITTED')
        self.assertEqual(days1[0]['selected'],days2[0]['selected'])
        self.assertTrue(days1[0]['results']['calibrated']['sweep'])
        self.assertFalse(days2[0]['results']['calibrated']['sweep'])

    def test_short_dates_and_a_backup_hit_cannot_complete_a_three_first_sweep(self):
        rows = [fixture(1,'2026-08-01'),fixture(2,'2026-08-01'),fixture(3,'2026-08-01',actual=(1,0)),fixture(4,'2026-08-02')]
        ranked = study.predictions(rows, model.fit([]))
        self.assertIsNone(ranked[1]['results']['raw']['sweep'])
        totals = study.summarize(ranked)
        self.assertEqual((totals['scheduled_slots'],totals['filled_slots'],totals['full_batches'],totals['short_batches']),(6,4,1,1))
        self.assertEqual(totals['rules']['raw']['complete_sweeps'],0)
        self.assertEqual(totals['rules']['raw']['ordered_three_hits'],4)

    def test_started_competition_day_excludes_its_later_fixture(self):
        times = ['2026-11-25T10:00Z','2026-11-25T10:30Z','2026-11-26T10:00Z']
        fixtures = {str(i): dict(match_id=str(i),cohort='jpn.1',kickoff_at=t,home='H',away='A') for i,t in enumerate(times)}

        class Original:
            registration = dict(record_sha256='previous',payload=dict(fixtures=fixtures))
            matches = fixtures

            def match(self, f, cutoff):
                r = fixture(f['match_id'],'2026-11-26')
                return dict(recorded_at='2026-11-23T00:00Z',record_sha256='signed-first',
                            payload=dict(forecasts=r['forecast'],quality=r['quality']['candidate']))

        inputs = dict(forward_training_before='2026-11-25',scope_sha256='scope',protocol_sha256='protocol',r7_manifest_sha256='r7')
        data = forward.payload(Original(),inputs,{'jpn.1':dict(forward=model.fit([]))},
                               datetime(2026,11,25,10,2,tzinfo=timezone.utc), 'weights')
        self.assertEqual(data['excluded'][0]['ids'],['0','1'])
        self.assertEqual(data['included'][0]['selected'],{'raw':['2'],'calibrated':['2']})


if __name__=='__main__':
    unittest.main()
