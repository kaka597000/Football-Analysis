import copy
from datetime import datetime, timezone, timedelta
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from research_program import future_status
from research_program import test_promotion_review as promotion_tests

seal = promotion_tests.seal


class Tests(unittest.TestCase):
    def plan(self, mid, home=None):
        plan = promotion_tests.GateTests().plan()
        identity = {'match_id': mid, 'cohort': 'eng.1', 'stage': 'regular',
                    'kickoff_at': '2026-11-01T12:00:00+00:00',
                    'home': home or 'home-' + mid, 'away': 'away'}
        plan['stage'] = 'review'
        plan['payload'].update(eligible_match_ids=[mid], fixtures_identity=[identity],
                               protocol_sha256='synthetic-protocol')
        return seal(plan)

    def inputs(self, root, plans):
        receipts = []
        for i, plan in enumerate(plans):
            path = root / f'plan-{i}.json'
            path.write_text(json.dumps(plan), encoding='utf-8')
            receipts.append({'path': str(path), 'sha256': plan['record_sha256']})
        manifest = {'protocol_sha256': 'synthetic-protocol', 'plans': receipts}
        for name, value in [('plans.json', manifest), ('packages.json', []), ('results.json', [])]:
            (root / name).write_text(json.dumps(value), encoding='utf-8')
        return manifest

    def run_status(self, root):
        return future_status.run(root / 'plans.json', root / 'packages.json',
                                 root / 'results.json', root / 'out.json')

    def pending_package(self, root, plan):
        fixture = plan['payload']['fixtures_identity'][0]
        record = seal({'fixture': fixture})
        path = root / ('pair-' + fixture['match_id'] + '.json')
        path.write_text(json.dumps(record), encoding='utf-8')
        receipt = {'path': str(path), 'sha256': record['record_sha256']}
        return {'match_id': fixture['match_id'], 'plan_sha256': plan['record_sha256'],
                'current_receipt': receipt, 'candidate_receipt': dict(receipt)}

    def test_every_plan_is_reviewed_with_its_complete_population(self):
        def review(plan, packages, labels, evidence):
            ids=plan['payload']['eligible_match_ids']
            return {'population':len(ids),'completed':0,'pending_results':[p['match_id'] for p in packages],
                    'missing_first_pairs':[i for i in ids if i not in {p['match_id'] for p in packages}]}
        one, two = self.plan('one'), self.plan('two')
        with tempfile.TemporaryDirectory() as tmp, patch.object(future_status,'review',side_effect=review) as called:
            root = Path(tmp); self.inputs(root, [one, two])
            package = self.pending_package(root, one)
            (root / 'packages.json').write_text(json.dumps([package]), encoding='utf-8')
            value = self.run_status(root)
        self.assertEqual(called.call_count,2)
        self.assertEqual(value['population'],2)
        self.assertEqual(value['missing_first_pairs'],['two'])
        self.assertEqual(value['pending_results'],one['payload']['fixtures_identity'])

    def test_distinct_batches_keep_all_uncaptured_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.inputs(root, [self.plan('one'), self.plan('two')])
            value = self.run_status(root)
        self.assertEqual(value['population'], 2)
        self.assertEqual(value['missing_first_pairs'], ['one', 'two'])
        self.assertEqual(value['completed'], 0)

    def test_plan_receipt_binds_loaded_record_and_protocol(self):
        for change in ('receipt', 'record', 'protocol'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); plan = self.plan('one'); manifest = self.inputs(root, [plan])
                if change == 'receipt':
                    manifest['plans'][0]['sha256'] = 'incorrect'
                elif change == 'record':
                    plan['payload']['cohort'] = 'changed'
                    (root / 'plan-0.json').write_text(json.dumps(plan), encoding='utf-8')
                else:
                    manifest['protocol_sha256'] = 'other-protocol'
                (root / 'plans.json').write_text(json.dumps(manifest), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'checksum|receipt|protocol'):
                    self.run_status(root)
                self.assertFalse((root / 'out.json').exists())

    def test_pending_package_identity_checked_before_locked_review(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(future_status, 'review') as reviewer:
            root = Path(tmp); plan = self.plan('one'); self.inputs(root, [plan])
            package = self.pending_package(root, plan)
            path = Path(package['candidate_receipt']['path'])
            record = json.loads(path.read_text(encoding='utf-8'))
            record['fixture']['home'] = 'OTHER'; seal(record)
            path.write_text(json.dumps(record), encoding='utf-8')
            for role in ('current', 'candidate'):
                package[role + '_receipt']['sha256'] = record['record_sha256']
            (root / 'packages.json').write_text(json.dumps([package]), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'preregistered fixture identity'):
                self.run_status(root)
            reviewer.assert_not_called()
            self.assertFalse((root / 'out.json').exists())

    def test_duplicate_plan_cannot_double_population(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); plan = self.plan('one'); self.inputs(root, [plan, copy.deepcopy(plan)])
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                self.run_status(root)
            self.assertFalse((root / 'out.json').exists())

    def test_distinct_plans_cannot_overlap_match_ids_or_alias_same_fixture(self):
        for same_id in (True, False):
            with self.subTest(same_id=same_id), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); one = self.plan('one'); two = self.plan('one' if same_id else 'two')
                two['payload']['batch'] = 'distinct-plan'
                if not same_id:
                    identity = two['payload']['fixtures_identity'][0]
                    identity['home'] = one['payload']['fixtures_identity'][0]['home']
                    identity['kickoff_at'] = datetime.fromisoformat(identity['kickoff_at']).astimezone(
                        timezone(timedelta(hours=8))).isoformat()
                seal(two); self.inputs(root, [one, two])
                with self.assertRaisesRegex(ValueError, 'duplicate|overlap'):
                    self.run_status(root)
                self.assertFalse((root / 'out.json').exists())

    def test_identity_population_must_match_eligible_ids_exactly(self):
        for change in ('duplicate', 'missing', 'outside'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); plan = self.plan('one')
                if change == 'duplicate':
                    plan['payload']['fixtures_identity'] *= 2
                elif change == 'missing':
                    plan['payload']['fixtures_identity'] = []
                else:
                    plan['payload']['fixtures_identity'][0]['match_id'] = 'other'
                seal(plan); self.inputs(root, [plan])
                with self.assertRaisesRegex(ValueError, 'identity population'):
                    self.run_status(root)
                self.assertFalse((root / 'out.json').exists())


if __name__ == '__main__':
    unittest.main()
