import copy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import tempfile
import unittest

try:
    from nations_league import model as m
except ModuleNotFoundError:
    from runtime.nations_league import model as m


class NationsModelTests(unittest.TestCase):
    def history(self):
        rows=[]
        for i in range(80):
            day=datetime(2022,1,1)+timedelta(days=i*3)
            rows.append({'match_id':str(i),'cohort':'uefa.nations:A:league-phase','date':day.date().isoformat(),
                         'home':'espn:'+str(i%4),'away':'espn:'+str((i+1)%4),'neutral':False,
                         'home_goals':1+i%3,'away_goals':i%2,'score_basis':'regulation_90',
                         'source_url':'https://site.api.espn.com/x','observed_at':'2026-01-01T00:00:00Z'})
        return rows

    def fixture(self,root):
        kickoff=(datetime.now(timezone.utc)+timedelta(days=3)).isoformat()
        query=datetime.now(timezone.utc).isoformat()
        c={'id':'101','date':kickoff,'neutralSite':False,
           'status':{'type':{'state':'pre','completed':False}},
           'competitors':[{'homeAway':s,'team':{'id':str(i)}} for s,i in [('home',1),('away',2)]]}
        p=Path(root)/'raw.json';m.write(p,{'header':{'id':'101','league':{'slug':'uefa.nations'},'season':{'name':'UEFA Nations League, League A'},'competitions':[c]}})
        return {'match_id':'espn:101','source_event_id':'101','competition':'uefa.nations','cohort':'uefa.nations:A:league-phase',
                'league_level':'A','stage':'league-phase','gender':'men','age':'senior','neutral':False,
                'home':'espn:1','away':'espn:2','kickoff_at':kickoff,'date':m.aware(kickoff).date().isoformat(),
                'cutoff_at':datetime.now(timezone.utc).isoformat(),'phase':'PreMatch',
                'phase_evidence':{'source_path':str(p),'source_sha256':m.sha(p),
                                  'source_url':'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.nations/summary?event=101','query_time':query}}

    def test_fixed_split_keeps_all_2026_labels_out_of_fit(self):
        rows=self.history();future=copy.deepcopy(rows[:3])
        for r in future:r['date']='2026-09-20';r['match_id']='held'+r['match_id']
        fit,held=m.split(rows+future)
        self.assertEqual((len(fit),len(held)),(80,3));self.assertLess(max(r['date'] for r in fit),min(r['date'] for r in held))

    def test_real_fits_are_finite_independent_and_do_not_mutate_history(self):
        rows=self.history();before=copy.deepcopy(rows);pair=m.fit_pair(rows)
        self.assertEqual(rows,before)
        self.assertIn('attack',pair['test']);self.assertNotIn('attack',pair['current'])
        for role in pair:
            matrix,_,_=m.distribution(pair[role],role,rows[-1])
            self.assertAlmostEqual(float(matrix.sum()),1.)

    def test_diagnostic_cannot_grant_formal_acceptance(self):
        rows=self.history();pair=m.fit_pair(rows[:65]);report=m.diagnostic(pair,rows[65:])
        self.assertFalse(report['formal_acceptance_passed']);self.assertFalse(report['activation_allowed'])
        self.assertIn('INSUFFICIENT_DIAGNOSTIC_SAMPLE',report['quality_reasons'])
        self.assertIsNone(report['actual_line_metrics'])

    def test_unknown_venue_is_rejected(self):
        with self.assertRaises(ValueError):m.distribution(m.fit_current(self.history()),'current',{'neutral':None})

    def test_future_raw_identity_and_target_label_guards(self):
        with tempfile.TemporaryDirectory() as d:
            f=self.fixture(d);m.validate_future(f)
            for change in ({'home':'espn:9'},{'league_level':'B'},{'home_goals':0},{'neutral':None},{'stage':'final'},{'gender':'women'}):
                with self.subTest(change=change),self.assertRaises(ValueError):m.validate_future({**f,**change})

    def test_changed_raw_and_future_capture_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            f=self.fixture(d);bad=copy.deepcopy(f);bad['phase_evidence']['query_time']=f['kickoff_at']
            with self.assertRaises(ValueError):m.validate_future(bad)
            Path(f['phase_evidence']['source_path']).write_text('{}')
            with self.assertRaises(ValueError):m.validate_future(f)

    def test_candidate_dependency_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            f=self.fixture(d);root=Path(d);dep=root/'dependency.json';m.write(dep,{})
            artifact={'schema':'WCPP-NATIONS-POISSON-1','version':'TEST_SYNTHETIC','role':'current','cohort':f['cohort'],
                      'created_at':'2020-01-01T00:00:00Z','train_end':'2022-01-01','core':m.fit_current(self.history())}
            for key in ('dataset','protocol','engine'):artifact[key+'_path']=str(dep);artifact[key+'_sha256']=m.sha(dep)
            path=root/'model.json';m.write(path,artifact)
            out=m.predict(path,f);self.assertFalse(out['formal_kpi_eligible']);self.assertFalse(out['acceptance_passed'])
            dep.write_text('{"changed":true}')
            with self.assertRaises(ValueError):m.predict(path,f)


if __name__=='__main__':unittest.main()
