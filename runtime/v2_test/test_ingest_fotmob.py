import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from ingest_fotmob import import_capture, ingest


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=datetime(2026,11,2,10,tzinfo=timezone.utc)
        self.capture={'file':'source.json','url':'https://example.test/SYNTHETIC_ONLY',
            'query_time':self.now.isoformat(),'expected_match_id':1,'expected_home_id':2,
            'expected_away_id':3,'expected_league_id':4,'cohort':'eng.1','identity_verified':True}
        self.raw={'general':{'matchId':'1','leagueId':4,'gender':'male'},'header':{
            'teams':[{'id':2},{'id':3}], 'status':{'finished':True,'reason':{'short':'FT'},
            'utcTime':(self.now-timedelta(days=1)).isoformat()}},'content':{'stats':{
            'Periods':{'All':{'stats':[{'stats':[{'title':'Expected goals (xG)','stats':['.77','.62']}]}]}}},
            'shotmap':{'shots':[{'teamId':2,'expectedGoals':.775},{'teamId':3,'expectedGoals':.617}]}}}

    def write(self):
        path=self.root/'source.json';path.write_text(json.dumps(self.raw))
        self.capture['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()

    def test_summary_and_shotmap_rounding_with_opposite_xga(self):
        self.write();rows=import_capture(self.capture,self.root,now=self.now)
        self.assertEqual(len(rows),4)
        self.assertEqual([r['value'] for r in rows],[.77,.62,.62,.77])
        self.assertTrue(all(r['first_known_at'] is None for r in rows))

    def test_summary_shotmap_conflict_quarantined_instead_of_selected(self):
        self.raw['content']['shotmap']['shots'][1]['expectedGoals']=1.9;self.write()
        result=ingest([self.capture],self.root,now=self.now)
        self.assertEqual(result['observations'],[]);self.assertEqual(len(result['quarantined']),1)

    def test_wrong_identity_does_not_become_another_team(self):
        self.write();self.capture['expected_home_id']=999
        with self.assertRaises(ValueError):import_capture(self.capture,self.root,now=self.now)

    def test_missing_xg_is_not_imputed_zero(self):
        self.raw['content']['stats']['Periods']['All']['stats'][0]['stats'][0]['stats']=[None,None]
        self.write()
        with self.assertRaises(ValueError):import_capture(self.capture,self.root,now=self.now)

    def test_extra_time_not_imported_as_regulation(self):
        self.raw['header']['status']['halfs']={'firstExtraHalfStarted':'nonempty'};self.write()
        with self.assertRaises(ValueError):import_capture(self.capture,self.root,now=self.now)

    def test_duplicates_do_not_increase_sample_size(self):
        self.write()
        with self.assertRaises(ValueError):ingest([self.capture,self.capture],self.root,now=self.now)

    def test_tampering_and_future_capture_not_imported(self):
        self.write();(self.root/'source.json').write_text('{}')
        self.assertEqual(len(ingest([self.capture],self.root,now=self.now)['quarantined']),1)
        self.write();self.capture['query_time']=(self.now+timedelta(hours=1)).isoformat()
        self.assertEqual(len(ingest([self.capture],self.root,now=self.now)['quarantined']),1)

    def test_boolean_or_nonfinite_vendor_values_not_interpreted_as_xg(self):
        self.raw['content']['stats']['Periods']['All']['stats'][0]['stats'][0]['stats']=[True,.62]
        self.write()
        with self.assertRaises(ValueError):import_capture(self.capture,self.root,now=self.now)


if __name__=='__main__':unittest.main()
