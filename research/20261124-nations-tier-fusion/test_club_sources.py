import io
import json
import unittest
from unittest.mock import Mock

from club_first_sources import pair_choices, verify_capture
from club_score_review import partition


class ClubSourceReviewTest(unittest.TestCase):
    def test_coverage_and_exclusive_hits_only_use_graded_common_rows(self):
        rows = [dict(current='1-0',market='0-0',regulation_score='1-0'),
                dict(current='1-1',market='1-1',regulation_score='1-1'),
                dict(current='0-0',market='0-1',regulation_score='3-3'),
                dict(current='1-1',market='0-0',regulation_score=None),
                dict(current='2-1',market=None,regulation_score='2-1')]
        result = partition(rows, ('current','market'))
        self.assertEqual((4,3,2), (result['available'],result['graded'],result['retrospective_union']))
        self.assertEqual({'current':1,'current+market':1,'none':1},result['wins'])

    def test_first_signed_after_common_cutoff_is_unavailable(self):
        f = dict(match_id='espn:1',competition='eng.1',stage='regular',home='espn:2',away='espn:3',neutral=False,
                 identity_verified=True,cutoff_at='2026-11-07T12:00Z',kickoff_at='2026-11-07T15:00Z')
        record = dict(fixture=f,recorded_at='2026-11-07T12:01Z')
        result = pair_choices(record, f)
        self.assertIsNone(result['current'])
        self.assertEqual('FIRST_PAIR_UNAVAILABLE_AT_DECISION',result['pair_status'])
        result = pair_choices(record,dict(f,away='espn:4'))
        self.assertEqual('FIRST_PAIR_IDENTITY_UNCONFIRMED',result['pair_status'])

    def test_provider_clock_and_acquisition_clock_are_not_interchangeable(self):
        arc = Mock()
        arc.getnames.return_value = ['raw.json.meta.json']
        meta = dict(http_status=200,sha256='h',query_time_utc='2026-11-07T12:01Z')
        arc.extractfile.return_value = io.BytesIO(json.dumps(meta).encode())
        with self.assertRaisesRegex(ValueError, 'capture time'):
            verify_capture(arc,'raw.json',dict(raw_sha256='h',query_utc='2026-11-07T12:00Z'))
