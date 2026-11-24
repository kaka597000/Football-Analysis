import json
import unittest
from copy import deepcopy

from late_boards import digest, project
from research.analysis_freeze import digest as freeze_digest


class OriginalScoreboardTests(unittest.TestCase):
    def fixture(self, slug='regular-season', state='pre', query='2026-11-22T09:12:00Z'):
        event = '10'
        kickoff = '2026-11-22T14:00:00Z'
        source = 'https://site.api.espn.com/apis/site/v2/sports/soccer/ned.1/scoreboard'
        comp = dict(id=event, date=kickoff, status={'type':{'state':state}}, odds=[],
                    competitors=[{'homeAway':k,'team':{'id':t}} for k,t in [('home','1'),('away','2')]])
        raw = json.dumps({'leagues':[{'slug':'ned.1'}], 'events':[
            {'id':event,'season':{'year':2026,'slug':slug},'competitions':[comp]}]}).encode()
        sha = digest(raw)
        f = dict(match_id='espn:10', competition='ned.1', cohort='ned.1', stage='regular', home='espn:1',
                 away='espn:2', gender='men', age='senior', identity_verified=True, score_basis='regulation_90',
                 kickoff_at=kickoff, cutoff_at='2026-11-22T09:30:00Z',
                 sources=[dict(source_url=source, query_time=query, response_sha256=sha)])
        record = dict(fixture=f, match_id=f['match_id'], track='comprehensive', stage='prematch',
                      revision=1, evaluation_slot='FIRST_PREMATCH', recorded_at='2026-11-22T09:31:00Z')
        record['record_sha256'] = freeze_digest(record)
        receipt = dict(url=source, http_status=200, fetched_at_utc=query)
        return record, {sha:(raw,receipt)}

    def test_freeze_binds_response_clock_and_independent_receipt(self):
        record, sources = self.fixture()
        sha, = sources
        raw, receipt = sources[sha]
        self.assertEqual(project(record,'test',sources)['raw_sha256'], sha)
        self.assertEqual(project(record,'test',{sha:(raw,None)})['raw_sha256'], sha)
        for key,value in [('http_status',503),('fetched_at_utc','2026-11-22T09:05:00Z')]:
            self.assertEqual(project(record,'test',{sha:(raw,dict(receipt, **{key:value}))})['excluded'],
                             'original_receipt_mismatch')
        with self.assertRaisesRegex(ValueError,'changed'):
            project(record,'test',{sha:(raw+b' ',receipt)})
        self.assertIn('excluded', project(record,'test',{}))

    def test_native_phase_predecision_clock_and_identity_must_agree(self):
        for args,reason in [({'slug':'playoffs'},'outside_verified_regular_or_elite_phase'),
                            ({'state':'post'},'unverified_original_event'),
                            ({'query':'2026-11-22T09:30:00Z'},'missing_original_bytes_or_predecision_clock')]:
            record, sources = self.fixture(**args)
            self.assertEqual(project(record,'test',sources)['excluded'], reason)
        record, sources = self.fixture()
        altered = deepcopy(record)
        del altered['record_sha256']
        altered['fixture']['home']='espn:9'
        altered['record_sha256']=freeze_digest(altered)
        self.assertEqual(project(altered,'test',sources)['excluded'], 'unverified_original_event')
