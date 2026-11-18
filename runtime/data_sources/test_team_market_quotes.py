import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data_sources import team_market_quotes as collect


class TeamMarketQuotesTest(unittest.TestCase):
    def setUp(self):
        self.f = dict(match_id='espn:01', provider_match_id='a'*32, competition='eng.1',
                      provider_home='Home FC', provider_away='Away FC', sport_key='soccer_epl',
                      kickoff_at='2026-12-21T17:00:00Z')
        self.meta = dict(source_url='https://example.com/odds', source_file='raw/a.json', source_sha256='a'*64,
                         requested_at='2026-12-21T15:59:55Z', captured_at='2026-12-21T16:00:00Z')
        self.stamp = '2026-12-21T15:59:50Z'
        self.event = dict(id='a'*32, sport_key='soccer_epl', home_team='Home FC', away_team='Away FC',
                          commence_time=self.f['kickoff_at'], bookmakers=[{'key':'book','markets':[]}])

    def market(self, key, outcomes):
        self.event['bookmakers'][0]['markets'].append(dict(key=key, last_update=self.stamp, outcomes=outcomes))

    def total(self, team, prices):
        return [dict(description=team, name=s, point=.5, price=p) for s,p in zip(('Over','Under'), prices)]

    def test_team_sides_market_time_and_complete_pairs(self):
        self.market('btts', [dict(name='Yes', price=1.8), dict(name='No', price=2.1)])
        self.market('team_totals', self.total('Home FC',(1.75,2.2)) + self.total('Away FC',(1.5,2.3)))
        self.market('alternate_team_totals', self.total('Home FC',(1.75,2.2)))
        self.market('team_totals', self.total('Unknown FC',(1.8,2.1)))
        rows, rejects = collect.normalize_event(self.f,self.event,self.meta)
        self.assertEqual([(r['market'],r['team'],r['line']) for r in rows],
                         [('btts',None,None),('team_total','home',.5),('team_total','away',.5)])
        self.assertTrue(all(r['quoted_at']==self.stamp and r['captured_at']==self.meta['captured_at'] for r in rows))
        self.assertEqual(rejects,{'UNKNOWN_TEAM_OR_LINE':2})

    def test_conflicting_overlapping_prices_and_future_quotes(self):
        self.market('team_totals',self.total('Home FC',(1.8,2.1)))
        self.market('alternate_team_totals',self.total('Home FC',(1.85,2.1)))
        self.market('btts',[dict(name='Yes',price=1.8)])
        self.market('team_totals',self.total('Away FC',(1.5,2.2)))
        self.event['bookmakers'][0]['markets'][-1]['last_update']='2026-12-21T16:02:00Z'
        rows, rejects = collect.normalize_event(self.f,self.event,self.meta)
        self.assertEqual(rows,[])
        self.assertEqual(rejects,{'CONFLICTING_MARKET_KEYS':1,'INCOMPLETE_OR_CONFLICTING_PAIR':1,
                                  'SOURCE_QUOTE_AFTER_CAPTURE':1})
        self.event['home_team']='Away FC'
        self.assertEqual(collect.normalize_event(self.f,self.event,self.meta)[1],{'EVENT_IDENTITY_MISMATCH':1})

    def test_replay_checks_hash_and_retry_does_not_fetch(self):
        self.market('btts',[dict(name='Yes',price=1.8),dict(name='No',price=2.1)])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            out=root/'result'
            from research.analysis_freeze import digest
            plan={'record_sha256':digest({})}
            (root/'plan.json').write_text(json.dumps(plan))
            def fetch(f,regions,key,stage,markets):
                raw=json.dumps(self.event).encode()
                (stage/'raw/a.json').write_bytes(raw)
                m=dict(self.meta, match_id=f['match_id'], provider_match_id=f['provider_match_id'],
                       source_sha256=hashlib.sha256(raw).hexdigest(),quota={'x-requests-last':1},status='OK')
                return m,self.event
            with (patch.object(collect,'load_targets',return_value=[self.f]),
                  patch.object(collect,'now',return_value='2026-12-21T16:00:02Z'),
                  patch.dict(os.environ,{'THE_ODDS':'test-key'})):
                first=collect.collect(root,out,plan='plan.json',fetcher=fetch)
                second=collect.collect(root,out,plan='plan.json',fetcher=lambda *args:self.fail('unexpected retry'))
                self.assertEqual(first,second)
                with self.assertRaisesRegex(ValueError,'different collection settings'):
                    collect.collect(root,out,plan='plan.json',regions='us')
                (out/'raw/a.json').write_bytes(b'{}')
                with self.assertRaisesRegex(ValueError,'raw bytes differ'):
                    collect.verify_run(root,out)


if __name__ == '__main__':
    unittest.main()
