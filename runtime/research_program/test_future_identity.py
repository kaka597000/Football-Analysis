"""Synthetic identity checks; no inference or historical fixture rewriting."""
import copy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from research.analysis_freeze import digest
from research_program.future_identity import validate_plan_packages


def seal(value):
    value.pop('record_sha256', None)
    value['record_sha256'] = digest(value)
    return value


class IdentityTests(unittest.TestCase):
    def package(self, root):
        fixture = {'match_id': 'synthetic', 'cohort': 'eng.1', 'stage': 'regular',
                   'kickoff_at': '2026-11-01T12:00:00+00:00', 'home': 'A', 'away': 'B'}
        plan = seal({'payload': {'eligible_match_ids': ['synthetic'],
                                'fixtures_identity': [copy.deepcopy(fixture)]}})
        package = {'match_id': 'synthetic', 'plan_sha256': plan['record_sha256']}
        for role in ('current', 'candidate'):
            record = seal({'match_id': 'synthetic', 'fixture': copy.deepcopy(fixture)})
            path = root / (role + '.json')
            path.write_text(json.dumps(record), encoding='utf-8')
            package[role + '_receipt'] = {'path': str(path), 'sha256': record['record_sha256']}
        return plan, package

    def rewrite(self, package, role, modify):
        receipt = package[role + '_receipt']; path = Path(receipt['path'])
        record = json.loads(path.read_text(encoding='utf-8')); modify(record); seal(record)
        path.write_text(json.dumps(record), encoding='utf-8'); receipt['sha256'] = record['record_sha256']

    def test_pending_pair_and_equivalent_kickoff_offsets_pass_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, package = self.package(Path(tmp))
            def offset(record):
                record['fixture']['kickoff_at'] = datetime.fromisoformat(
                    record['fixture']['kickoff_at']).astimezone(timezone(timedelta(hours=8))).isoformat()
            self.rewrite(package, 'candidate', offset)
            before = copy.deepcopy((plan, package))
            validate_plan_packages(plan, [package])
            self.assertEqual((plan, package), before)

    def test_each_role_must_match_every_registered_identity_field(self):
        changes = {'match_id': 'other', 'cohort': 'esp.1', 'stage': 'playoff',
                   'kickoff_at': '2026-11-02T12:00:00+00:00', 'home': 'OTHER', 'away': 'OTHER'}
        for role in ('current', 'candidate'):
            for field, value in changes.items():
                with self.subTest(role=role, field=field), tempfile.TemporaryDirectory() as tmp:
                    plan, package = self.package(Path(tmp))
                    self.rewrite(package, role, lambda record: record['fixture'].__setitem__(field, value))
                    with self.assertRaisesRegex(ValueError, 'preregistered fixture identity'):
                        validate_plan_packages(plan, [package])

    def test_plan_package_and_receipt_bindings_are_required(self):
        for change in ('plan', 'package', 'receipt', 'record', 'top_level_id', 'duplicate'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                plan, package = self.package(Path(tmp)); packages = [package]
                if change == 'plan':
                    plan['payload']['fixtures_identity'][0]['home'] = 'OTHER'
                elif change == 'package':
                    package['plan_sha256'] = 'wrong'
                elif change == 'receipt':
                    package['candidate_receipt']['sha256'] = 'wrong'
                elif change == 'record':
                    path = Path(package['candidate_receipt']['path'])
                    record = json.loads(path.read_text(encoding='utf-8')); record['fixture']['home'] = 'OTHER'
                    path.write_text(json.dumps(record), encoding='utf-8')
                elif change == 'top_level_id':
                    self.rewrite(package, 'candidate', lambda record: record.__setitem__('match_id', 'other'))
                else:
                    packages.append(copy.deepcopy(package))
                with self.assertRaises(ValueError):
                    validate_plan_packages(plan, packages)

    def test_identity_population_is_complete_and_unique(self):
        for values in ([], ['synthetic', 'synthetic'], ['other']):
            with self.subTest(values=values), tempfile.TemporaryDirectory() as tmp:
                plan, package = self.package(Path(tmp)); plan['payload']['eligible_match_ids'] = values; seal(plan)
                package['plan_sha256'] = plan['record_sha256']
                with self.assertRaisesRegex(ValueError, 'identity population'):
                    validate_plan_packages(plan, [package])


if __name__ == '__main__':
    unittest.main()
