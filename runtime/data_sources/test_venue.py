import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from venue import resolve


class VenueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = {'match_id':'espn:123', 'home':'espn:1', 'away':'espn:2',
                        'cutoff_at':'2026-01-01T10:00:00Z','kickoff_at':'2026-01-01T12:00:00Z'}
        self.event = {'id':'123','date':'2026-01-01T12:00:00Z',
                      'competitions':[{'neutralSite':False,'competitors':[
                          {'homeAway':'home','team':{'id':'1'}},
                          {'homeAway':'away','team':{'id':'2'}}]}]}

    def ref(self, event, name='one.json', query='2026-01-01T09:00:00Z'):
        raw=json.dumps({'events':[event]}).encode();(self.root/name).write_bytes(raw)
        return {'file':name,'sha256':hashlib.sha256(raw).hexdigest(),
                'url':'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard?dates=20260101',
                'query_time':query}

    def test_explicit_state_and_missing_never_assumed(self):
        self.assertEqual(resolve(self.fixture,[self.ref(self.event)],self.root)['neutral'],False)
        del self.event['competitions'][0]['neutralSite']
        r=resolve(self.fixture,[self.ref(self.event)],self.root)
        self.assertEqual(r['status'],'UNKNOWN');self.assertIsNone(r['neutral'])

    def test_conflicting_and_late_sources(self):
        a=self.ref(self.event);other=copy.deepcopy(self.event);other['competitions'][0]['neutralSite']=True
        b=self.ref(other,'two.json');r=resolve(self.fixture,[a,b],self.root)
        self.assertEqual(r['status'],'CONFLICT');self.assertIsNone(r['neutral'])
        b['query_time']='2026-01-01T11:00:00Z'
        self.assertEqual(resolve(self.fixture,[a,b],self.root)['neutral'],False)

    def test_wrong_identity_or_tampered_response_rejected(self):
        ref=self.ref(self.event);self.event['competitions'][0]['competitors'][0]['team']['id']='9'
        with self.assertRaises(ValueError):resolve(self.fixture,[self.ref(self.event)],self.root)
        self.ref(self.event);ref['sha256']='0'*64
        with self.assertRaises(ValueError):resolve(self.fixture,[ref],self.root)


if __name__ == '__main__':unittest.main()
