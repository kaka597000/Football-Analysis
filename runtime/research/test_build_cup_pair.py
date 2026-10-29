"""Synthetic data tests only; never football-performance evidence."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from build_cup_pair import build


class CupBuilderTests(unittest.TestCase):
    def test_time_boundary_round_scope_dedup_and_independent_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);db=root/'source.sqlite';c=sqlite3.connect(db)
            c.execute('CREATE TABLE matches(match_id,competition,training_eligible,result_status,date,stage,home_id,away_id,h90,a90,season)')
            c.execute('CREATE TABLE team_redirects(old_id,new_id)')
            for i in range(160):
                day=f'2020-{1+i//28:02}-{1+i%28:02}'
                c.execute('INSERT INTO matches VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (str(i),'eng.league_cup',1,'accepted',day,'third-round','A','B',i%3,i%2,'2020'))
            for mid,day,stage in [('future','2099-01-01','third-round'),('two-leg','2020-01-01','semifinals')]:
                c.execute('INSERT INTO matches VALUES(?,?,?,?,?,?,?,?,?,?,?)',(mid,'eng.league_cup',1,'accepted',day,stage,'A','B',50,50,'2020'))
            c.commit();c.close()
            result=build(db,root/'out','2021-01-01')
            self.assertEqual(result['n'],160)
            rows=json.loads((root/'out/history.json').read_text())
            self.assertTrue(all(x['date']<'2021-01-01' and x['stage']=='third-round' for x in rows))
            self.assertTrue(any(x['home_goals']==x['away_goals'] for x in rows))
            a=json.loads((root/'out/current-artifact.json').read_text())
            b=json.loads((root/'out/test-artifact.json').read_text())
            self.assertEqual(a['core']['fit_end'],max(x['date'] for x in rows))
            self.assertEqual((a['model_kind'],b['model_kind']),('constant','team_poisson'))
            self.assertNotIn('semifinals',a['forecast_scope']['stages'])
            validation=json.loads((root/'out/validation.json').read_text())
            self.assertFalse(validation['promotion_proven'])
            with self.assertRaises(FileExistsError):build(db,root/'out','2021-01-01')


if __name__=='__main__':unittest.main()
