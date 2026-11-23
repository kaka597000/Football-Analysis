from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from research_program import kpi_validation as kpi
from research_program.paired_kpi_inference import compare, compare_audited


def row(mid, day='2026-11-01', home=2, away=0, line=2.25, arms=None):
    return {'id': mid, 'date': day, 'cohort': 'eng.1', 'actual': [home, away],
            'line': line, 'forecast': arms or {}}


def picks(top3=('2-0', '1-0', '0-0'), wdl='home', total='under'):
    return {'top3': top3, 'wdl': wdl, 'total_side': total}


class PairedKPIInferenceTests(TestCase):
    def replay(self, rows):
        return compare(rows, baseline='before', candidate='after', draws=100, seed=123)

    def test_report_uses_unique_games_and_keeps_missing_coverage(self):
        predictable = row('one', arms={'before': picks(), 'after': picks()})
        no_candidate = row('two', day='2026-11-02', arms={'before': picks(), 'after': {'total_line': None}})
        pending = dict(row('third', arms={'before': picks(), 'after': picks()}), actual=None)
        r = self.replay([predictable, no_candidate, pending])
        self.assertEqual(r['population'], 3)
        self.assertEqual(r['pending'], 1)
        c = r['tracks']['after']['metrics']['score_top1']
        self.assertEqual(c['hits'], 1)
        self.assertEqual(c['eligible_rate'], 1.)
        self.assertEqual(c['registered_coverage'], 2 / 3)
        self.assertEqual(c['resolved_gate_rate'], 0.5)
        p = r['paired']['score_top1']
        self.assertEqual(p['eligible'], 1)
        self.assertEqual(p['coverage'], 0.5)
        self.assertEqual(p['net_hits'], 0)
        self.assertEqual(p['fixed_population_net_hits'], -1)
        self.assertIsNone(p['date_cluster_ci'])

    def test_strict_totals_drop_push_and_keep_half_win_and_half_loss(self):
        both = {'before': picks(total='under'), 'after': picks(total='over')}
        v = [
            row('integer push', line=2, arms=both),
            row('quarter half', day='2026-11-02', arms={
                'before': {'settlement': 1}, 'after': {'settlement': 'HALF_LOSS'}}),
            row('full win', day='2026-11-02', home=3, arms=both),
            row('missing', day='2026-11-02', home=1, away=1, arms={'before': picks(total='under')}),
        ]
        r = self.replay(v)
        self.assertEqual(r['common_total_pushes'], 1)
        a = r['tracks']['after']
        self.assertEqual(a['total_settlements'], {'FULL_WIN': 1, 'HALF_WIN': 0, 'PUSH': 1, 'HALF_LOSS': 1, 'FULL_LOSS': 0})
        self.assertEqual(a['metrics']['actual_ou']['eligible_rate'], 0.5)
        self.assertEqual(a['metrics']['actual_ou']['resolved_gate_rate'], 1/3)
        self.assertEqual(r['paired']['actual_ou']['net_hits'], 1)

    def test_interval_resamples_whole_days_with_unequal_cluster_sizes(self):
        lose = {'before': picks(), 'after': picks(('0-0','0-1','1-0'), 'draw')}
        win = {'after': lose['before'], 'before': lose['after']}
        v = [row(str(i), arms=win) for i in range(5)] + [row('lost', day='2026-11-02', arms=lose)]
        p = self.replay(v)['paired']['score_top1']
        self.assertEqual(p['matchday_clusters'], 2)
        self.assertAlmostEqual(p['net_rate'], 4/6)
        self.assertEqual(p['date_cluster_ci'], [-1., 1.])
        self.assertEqual(self.replay(list(reversed(v)))['paired']['score_top1'], p)

    def test_rejects_duplicate_fixtures_and_false_settlement(self):
        x = row('x')
        with self.assertRaisesRegex(ValueError, 'distinct fixture'):
            self.replay([x, x])
        with self.assertRaisesRegex(ValueError, 'impossible'):
            self.replay([row('x', line=2, arms={'before': {'settlement': -1}})])
        with self.assertRaisesRegex(ValueError, 'differs from the common'):
            self.replay([row('x', arms={'before': {'total_side': 'under', 'total_line': 3.0}})])
        with self.assertRaisesRegex(ValueError, 'three unique'):
            self.replay([row('x', arms={'before': picks(('0-0', '0-0', '1-0'))})])

    def test_prospective_bridge_checks_registration_and_keeps_pending(self):
        with TemporaryDirectory() as work:
            now = datetime.now(timezone.utc)
            fixture = {'match_id': 'espn:205', 'cohort': 'eng.1', 'stage': 'regular',
                       'kickoff_at': (now + timedelta(hours=2)).isoformat(), 'home': 'espn:10',
                       'away': 'espn:11', 'identity_verified': True}
            specs = [{'version': s, 'artifact_sha256': t * 64}
                     for s, t in [('candidate', 'a'), ('market', 'b')]]
            receipt = kpi.register([fixture], *specs, freeze_root=work)
            plan = kpi.read(receipt['path'])
            fixture['cutoff_at'] = datetime.now(timezone.utc).isoformat()
            gen = datetime.now(timezone.utc).isoformat()
            candidate, market = [dict(s, generated_at=gen, wdl='home') for s in specs]
            receipt = kpi.capture(plan, fixture, candidate, market, freeze_root=work)
            cap = kpi.read(receipt['path'])
            r = compare_audited(plan, [cap], [], evidence_root=Path(work))
            self.assertEqual(r['pending'], 1)
            self.assertTrue(r['evidence']['prospective_certified'])
            self.assertEqual(r['tracks']['candidate']['metrics']['wdl']['issued'], 1)
            self.assertEqual(r['tracks']['candidate']['metrics']['wdl']['eligible'], 0)
            edited = dict(cap, record_sha256='d' * 64)
            with self.assertRaisesRegex(ValueError, 'checksum'):
                compare_audited(plan, [edited], [], evidence_root=Path(work))
