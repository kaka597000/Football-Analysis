import json
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from research_program import kpi_validation as kpi
from research.analysis_freeze import digest


class KPIValidationTests(TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.freeze = self.root / 'freezes'
        self.now = datetime.now(timezone.utc)
        self.kickoff = self.now + timedelta(hours=1)
        self.specs = [
            {'version': 'candidate', 'artifact_sha256': 'a' * 64},
            {'version': 'market-only', 'artifact_sha256': 'b' * 64},
        ]

    def fixture(self, n):
        return {
            'match_id': f'espn:{n}', 'cohort': 'eng.1', 'stage': 'regular',
            'kickoff_at': self.kickoff.isoformat(), 'home': f'espn:{100+n}',
            'away': f'espn:{200+n}', 'identity_verified': True,
        }

    def plan(self, n):
        return kpi.read(kpi.register([self.fixture(i) for i in range(n)], *self.specs,
                                     freeze_root=self.freeze)['path'])

    def inputs(self, n, line=2.25):
        f = self.fixture(n)
        now = datetime.now(timezone.utc).isoformat()
        f['cutoff_at'] = now
        f['total_quote'] = {
            'verified': True, 'match_id': f['match_id'], 'bookmaker': 'A',
            'market': 'asian_total', 'line': line, 'over_odds': 2., 'under_odds': 2.,
            'quoted_at': now, 'query_time': now, 'source_url': 'https://odds.example/quote',
        }
        a, b = [dict(spec, generated_at=datetime.now(timezone.utc).isoformat()) for spec in self.specs]
        a.update(primary='2-0', top3=['2-0', '1-0', '3-0'], wdl='home', total_side='under', total_line=line)
        b.update(primary='0-2', top3=['0-2', '0-1', '1-1'], wdl='away', total_side='over', total_line=line)
        return f, a, b

    def capture(self, plan, n, line=2.25):
        return kpi.read(kpi.capture(plan, *self.inputs(n, line), freeze_root=self.freeze)['path'])

    def result(self, n, home, away):
        fixture = self.fixture(n)
        teams = []
        for side, goals in (('home', home), ('away', away)):
            teams.append({'homeAway': side, 'team': {'id': fixture[side].split(':')[1]}, 'score': str(goals)})
        raw = {'header': {'id': n, 'league': {'slug': 'eng.1'}, 'competitions': [{
            'date': fixture['kickoff_at'], 'competitors': teams,
            'status': {'period': 2, 'type': {'completed': True, 'name': 'STATUS_FULL_TIME'}},
        }]}}
        response = self.root / f'{n}.json'
        response.write_text(json.dumps(raw), encoding='utf-8')
        stamp = self.kickoff + timedelta(minutes=100)
        return {**fixture, 'event_status': 'FT', 'score_basis': 'regulation_90',
                'observed_at': stamp.isoformat(), 'home_goals': home, 'away_goals': away,
                'evidence': {'file': response.name, 'sha256': kpi.sha(response),
                             'query_time': stamp.isoformat(), 'identity_verified': True,
                             'url': f'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/summary?event={n}'}}

    def review(self, plan, captures, results):
        with patch.object(kpi, '_utcnow', return_value=self.kickoff + timedelta(hours=2)):
            return kpi.review(plan, captures, results, evidence_root=self.root)

    def test_fixed_population_keeps_missing_picks_and_pending_matches(self):
        plan = self.plan(3)
        result = self.review(plan, [self.capture(plan, 0)], [self.result(0, 2, 0), self.result(1, 0, 2)])
        m = result['tracks']['candidate']['metrics']
        self.assertEqual(result['population'], 3)
        self.assertEqual(result['pending_matches'], ['espn:2'])
        self.assertEqual(result['missing_first_captures'], ['espn:1', 'espn:2'])
        self.assertEqual(m['score_top1']['eligible_rate'], 1.)
        self.assertEqual(m['score_top1']['resolved_gate_rate'], .5)
        self.assertEqual(m['score_top1']['population_coverage'], 1./3)
        self.assertEqual(result['paired_vs_market_baseline']['score_top1']['net_hits'], 1)
        self.assertEqual(result['tracks']['candidate']['total_settlements']['HALF_WIN'], 1)
        self.assertEqual(m['actual_ou']['hits'], 0)
        self.assertFalse(result['targets_met_on_complete_population'])

    def test_strict_total_excludes_pushes_and_reports_uncertainty(self):
        plan = self.plan(2)
        original = [self.capture(plan, n, 2.) for n in range(2)]
        result = self.review(plan, original, [self.result(0, 2, 0), self.result(1, 1, 0)])
        m = result['tracks']['candidate']['metrics']
        self.assertEqual(m['score_top3']['resolved_gate_rate'], 1.)
        self.assertEqual(m['actual_ou']['eligible'], 1)
        self.assertEqual(m['actual_ou']['resolved_gate_denominator'], 1)
        self.assertEqual(m['actual_ou']['resolved_gate_rate'], 1.)
        self.assertEqual(result['tracks']['candidate']['total_settlements']['PUSH'], 1)
        self.assertTrue(result['targets_met_on_complete_population'])
        self.assertFalse(result['targets_supported_by_per_metric_wilson_95'])

    def test_registered_versions_and_actual_line_are_required(self):
        plan = self.plan(1)
        f, candidate, market = self.inputs(0)
        with self.assertRaisesRegex(ValueError, 'differs from locked identity'):
            kpi.capture(plan, f, dict(candidate, version='retuned'), market, freeze_root=self.freeze)
        with self.assertRaisesRegex(ValueError, 'differs from common actual line'):
            kpi.capture(plan, f, dict(candidate, total_line=2.), market, freeze_root=self.freeze)
        with self.assertRaisesRegex(ValueError, 'three unique'):
            kpi.capture(plan, f, dict(candidate, top3=['2-0', '2-0', '3-0']), market, freeze_root=self.freeze)
        original = kpi.capture(plan, f, candidate, market, freeze_root=self.freeze)
        same = kpi.capture(plan, f, candidate, market, freeze_root=self.freeze)
        self.assertEqual(original, same)
        with self.assertRaisesRegex(ValueError, 'idempotency'):
            kpi.capture(plan, f, dict(candidate, wdl='draw'), market, freeze_root=self.freeze)

    def test_changed_hash_and_raw_result_do_not_score(self):
        plan = self.plan(1)
        original = self.capture(plan, 0)
        result = self.result(0, 2, 0)
        edited = dict(original, payload=dict(original['payload'], plan_sha256='d' * 64))
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.review(plan, [edited], [result])
        body = {key: val for key, val in edited.items() if key != 'record_sha256'}
        edited['record_sha256'] = digest(body)
        with self.assertRaisesRegex(ValueError, 'unbound'):
            self.review(plan, [edited], [result])
        with self.assertRaisesRegex(ValueError, 'score conflict'):
            self.review(plan, [original], [dict(result, home_goals=4)])

    def test_repeating_same_registration_returns_original_receipt(self):
        fixtures = [self.fixture(0)]
        first = kpi.register(fixtures, *self.specs, freeze_root=self.freeze)
        second = kpi.register(fixtures, *self.specs, freeze_root=self.freeze)
        self.assertEqual(first, second)

    def test_new_targets_match_the_project_goals_and_settle_at_sixty_percent(self):
        goals = kpi.read(Path(__file__).resolve().parents[2] / 'project-goals.json')['first_release_acceptance_20261123']
        plan = self.plan(5)
        self.assertEqual(plan['payload']['schema'], goals['registration_schema'])
        self.assertEqual(plan['payload']['targets'], dict(
            score_top1=goals['first_exact_score'], score_top3=goals['three_exact_scores'],
            wdl=goals['wdl'], actual_ou=goals['actual_line_full_win_nonpush']))
        captures = [self.capture(plan, i, 2.5) for i in range(5)]
        goals = [(1, 0), (1, 0), (2, 0), (0, 1), (0, 1)]
        results = [self.result(i, home, away) for i, (home, away) in enumerate(goals)]
        review = self.review(plan, captures, results)
        metrics = review['tracks']['candidate']['metrics']
        self.assertEqual([metrics[k]['hits'] for k in ('score_top1','score_top3','wdl','actual_ou')], [1,3,3,5])
        self.assertEqual(metrics['wdl']['target'], .60)
        self.assertTrue(review['targets_met_on_complete_population'])

    def test_original_registration_resumes_and_scores_under_its_locked_targets(self):
        from research_program.paired_kpi_inference import compare_audited

        fixtures = [self.fixture(0)]
        sample = kpi.read(kpi.register(fixtures, *self.specs, freeze_root=self.root / 'new')['path'])
        payload = dict(sample['payload'], schema='WCPP-KPI-REGISTRATION-1',
                       targets=dict(sample['payload']['targets'], wdl=.65))
        mid = 'kpi-plan:' + digest(payload)
        receipt = kpi.save(payload, dict(sample['fixture'], match_id=mid), stage='prematch',
                           track=kpi.REGISTRATION, root=self.freeze, idempotency_key=mid)
        path = Path(receipt['path'])
        original_bytes = path.read_bytes()
        self.assertEqual(kpi.register(fixtures, *self.specs, freeze_root=self.freeze), receipt)
        plan = kpi.read(path)
        capture = self.capture(plan, 0)
        certified = compare_audited(plan, [capture], [], evidence_root=self.root)
        self.assertEqual(certified['tracks']['candidate']['metrics']['wdl']['target'], .65)
        self.assertEqual(self.review(plan, [capture], [])['tracks']['candidate']['metrics']['wdl']['target'], .65)
        with patch.object(kpi, '_utcnow', return_value=self.kickoff):
            self.assertEqual(kpi.register(fixtures, *self.specs, freeze_root=self.freeze), receipt)
            with self.assertRaisesRegex(ValueError, 'only future'):
                kpi.register([self.fixture(1)], *self.specs, freeze_root=self.freeze)
        self.assertEqual(path.read_bytes(), original_bytes)

        # Even a consistently rehashed plan cannot exchange the thresholds of two schemas.
        payload = dict(payload, targets=kpi.TARGETS)
        mid = 'kpi-plan:' + digest(payload)
        forged = kpi.save(payload, dict(sample['fixture'], match_id=mid), stage='prematch',
                          track=kpi.REGISTRATION, root=self.root / 'invalid', idempotency_key=mid)
        with self.assertRaisesRegex(ValueError, 'registered KPI protocol'):
            kpi.review(kpi.read(forged['path']), [], [])
