import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from rolling_capture_input import prepare


class CaptureAdapterTests(unittest.TestCase):
    def test_late_new_fixture_cannot_move_daily_cutoff(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root/'normalized-results.jsonl').write_text(''); requests = []
            def add(mid, kickoff, captured):
                event = {'id': mid, 'competitions': [{'id': mid, 'date': kickoff,
                    'neutralSite': False, 'competitors': [
                        {'homeAway': 'home', 'team': {'id': '1'}},
                        {'homeAway': 'away', 'team': {'id': '2'}}]}]}
                body = json.dumps({'events': [event]}).encode(); (root/(mid+'.json')).write_bytes(body)
                url = 'https://site.api.espn.com/apis/site/v2/sports/soccer/aus.1/scoreboard'
                requests.append({'source_url': url, 'status': 'OK', 'captured_at': captured,
                    'raw': {'path': mid+'.json', 'sha256': hashlib.sha256(body).hexdigest(),
                            'captured_at': captured, 'source_url': url}})
                (root/'requests.jsonl').write_text('\n'.join(map(json.dumps, requests)))
            args = dict(cohorts=['aus.1'], start='2026-11-01', end='2026-11-01',
                        evaluation_at='2026-11-10T00:00:00+00:00', batch_time_utc='06:00:00')
            add('1', '2026-11-01T10:00:00Z', '2026-10-31T10:00:00Z'); a, _ = prepare(root, **args)
            add('2', '2026-11-01T08:00:00Z', '2026-11-02T10:00:00Z'); b, audit = prepare(root, **args)
            self.assertEqual(a['day_cutoffs'], b['day_cutoffs']); self.assertEqual(a['fixtures'][0], b['fixtures'][0])
            self.assertEqual(audit['coverage_before_cutoff'], {'AVAILABLE': 1, 'LATE_CAPTURE': 1})
            self.assertEqual(audit['scope_status'], 'OBSERVED_CAPTURE_SCOPE_NOT_FULL_OFFICIAL_SCHEDULE_CERTIFICATION')

    def test_unsupported_phase_cohorts_rejected(self):
        with self.assertRaisesRegex(ValueError, 'supports only'):
            prepare('/not/read', cohorts=['uefa.europa'], start='2026-11-01',
                    end='2026-11-02', evaluation_at='2026-11-10T00:00:00Z')


if __name__ == '__main__':
    unittest.main()
