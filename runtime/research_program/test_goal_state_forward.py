from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research_program import goal_state_forward as forward
from research_program.goal_state_study import write_new


class GoalStateForwardTests(unittest.TestCase):
    def test_missing_prices_and_pending_fixtures_keep_the_full_first_issue_population(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root/'copies'
            f = dict(match_id='test:1', cohort='eng.1', stage='regular', home='h', away='a',
                     kickoff_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())
            plan = forward.register([f], dict(pairs={'market': ['market', 'selected']}), 'test-goal-state', folder, freeze_root=root/'issued')
            f['cutoff_at'] = datetime.now(timezone.utc).isoformat()
            rec = forward.issue(plan, f, {'line': None, 'forecasts': {}}, 'test-goal-state', folder, freeze_root=root/'issued')
            again = forward.register([f], dict(pairs={'market': ['market', 'selected']}), 'test-goal-state',
                                     folder, freeze_root=root/'issued')
            self.assertEqual(again, plan)
            write_new(folder/'receipts.json', dict(registration=plan, matches={f['match_id']: rec}))
            report = forward.review(folder, [], root)['market']
            self.assertEqual((report['population'], report['pending']), (1, 1))
            self.assertEqual(report['tracks']['selected']['metrics']['actual_ou']['issued'], 0)
            with self.assertRaises(ValueError):
                forward.review(folder, [dict(match_id='unregistered')], root)
            path = folder / rec['file']
            changed = json.loads(path.read_bytes())
            changed['payload']['line'] = 2.5
            path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                forward.review(folder, [], root)
