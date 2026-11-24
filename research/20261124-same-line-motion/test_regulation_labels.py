from copy import deepcopy
from hashlib import sha256
import json
import unittest

from raw_summary_adapter import authenticate
from rebuild_late_labels import result_stage


class RegulationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.event={'id':'espn:10','competition':'uefa.nations','stage':'league-phase','home_id':'espn:21',
                    'away_id':'espn:22','kickoff_at':'2026-11-14T16:00:00Z'}
        teams=[{'homeAway':side,'team':{'id':team},'score':'1','linescores':[{'value':0},{'value':1}]}
               for side,team in (('home','21'),('away','22'))]
        comp={'id':'10','date':self.event['kickoff_at'],'competitors':teams,
              'status':{'type':{'completed':True,'state':'post','name':'STATUS_FULL_TIME'}}}
        self.summary={'format':{'regulation':{'periods':2}},'header':{'id':'10','league':{'slug':'uefa.nations'},
                      'season':{'name':'2026-27 UEFA Nations League, League Phase'},'competitions':[comp]}}

    def read(self, summary):
        raw=json.dumps(summary).encode()
        return authenticate(raw,expected=self.event,source_url='https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.nations/summary?event=10',
                            captured_at='2026-11-14T18:00:00Z',cutoff_at='2026-11-24T11:26:32Z',
                            evidence_sha256=sha256(raw).hexdigest())

    def test_strict_ft_requires_two_periods_and_matching_total(self):
        self.assertEqual(self.read(self.summary)['score'], [1,1])
        for problem in ('extra_time','third_period','final_total'):
            summary=deepcopy(self.summary)
            comp=summary['header']['competitions'][0]
            if problem=='extra_time':
                comp['status']['type']['name']='STATUS_FINAL_AET'
            elif problem=='third_period':
                comp['competitors'][0]['linescores'].append({'value':0})
            else:
                comp['competitors'][0]['score']='2'
            with self.subTest(problem=problem), self.assertRaises(ValueError):
                self.read(summary)

    def test_later_knockout_summary_cannot_supply_a_league_phase_result(self):
        self.assertTrue(result_stage(json.dumps(self.summary).encode(),self.event))
        self.summary['header']['season']['name']='UEFA Nations League, Quarterfinals'
        self.assertFalse(result_stage(json.dumps(self.summary).encode(),self.event))
