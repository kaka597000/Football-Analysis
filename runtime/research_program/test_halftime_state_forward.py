import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from research_program import halftime_state_forward as f


def write(path, value):
    path.write_text(json.dumps(value, allow_nan=False, indent=2) + '\n', encoding='utf-8')


def utc_receipt(ref):
    return dict(ref, recorded_at_utc=f.aware(ref['recorded_at']).astimezone(timezone.utc).isoformat())


class ForwardTests(unittest.TestCase):
    """All writes and issue calls here use fictional fixtures and private temporary ledgers."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.src, self.parent = self.root / 'study', self.root / 'parent'
        self.out, self.store = self.root / 'export', self.root / 'ledger'
        self.src.mkdir()
        self.start = f._now()
        self.earliest = self.start + timedelta(days=10)
        self.patch_constant('THROUGH', '2003-11-21')
        self.patch_constant('TRACK', 'test-halftime-standalone')
        self.patch_constant('PARENT_TRACK', 'test-prior-standalone')
        grid = np.zeros((f.base.SIZE, f.base.SIZE))
        # The original signed first is within a local tie but has a distinct cell probability.
        grid[1, 0], grid[0, 1], grid[1, 1], grid[0, 0] = .35 - 1e-14, .35 + 1e-14, .2, .1
        weights = dict(version=f.model.VERSION, training_observed=8, shares=[.42, .46],
                       leading=.8, trailing=1.25, exposure={})
        self.common = dict(version=f.model.VERSION, competition=f.CODE, protocol_sha256=f.history.PROTOCOL_SHA256,
                           panel_sha256='1' * 64, halves_sha256='2' * 64)
        self.patch_constant('HALVES_SHA256', self.common['halves_sha256'])
        doses = dict(score=0., wdl=1., total=.5)
        coverage = {k: dict(fixtures=n, verified_halves=o) for k, n, o in (
            ('training',4,3), ('validation',2,1), ('evaluation',2,2), ('recent',2,2))}
        fit = dict(self.common, bounds=list(f.history.study.BOUNDS), doses=doses, coverage=coverage,
                   calibration=dict(weights, training_observed=3))
        write(self.src / 'inputs.json', self.common)
        write(self.src / 'fit.json', fit)
        self.recipe = dict(self.common, calibration=weights, training_games=10, training_through=f.THROUGH,
                           doses=doses, fit_sha256=f.sha(self.src / 'fit.json'))
        write(self.src / 'forward.json', self.recipe)
        self.patch_constant('STUDY_SHA256', {n: f.sha(self.src / n) for n in f.STUDY_SHA256})
        self.fixtures, self.originals = {}, {}
        for i in range(10):
            mid = f'espn:{800001 + i}'
            # Later lexicographic IDs include earlier kickoffs, and kickoff ties occur within each group.
            kickoff = self.earliest + timedelta(hours=(9-i) % 3)
            fx = dict(match_id=mid, stage='Japanese J1 League', cohort=f.CODE, home=f'espn:{100+i}',
                      away=f'espn:{200+i}', kickoff_at=kickoff.isoformat())
            self.fixtures[mid] = fx
            line = (None, 2.25, 0.0)[i % 3]
            tips = dict(f.base.pick(grid, line), primary='1-0', top3=['1-0','0-1','1-1'])
            self.originals[mid] = dict(version=f.PARENT_VERSION, protocol_sha256='5' * 64, training_through=f.THROUGH,
                inputs_cutoff_at=self.start.isoformat(), r7_grid=grid.tolist(), r7_grid_sha256=f.digest(grid.tolist()),
                forecasts=dict(r7=tips, r10=tips, r15=tips, candidate=tips, r5=tips, market={} if line is None else
                               dict(total_line=line, total_side='under')),
                home_name='Home ' + str(i), away_name='Away ' + str(i), home_id=str(100+i), away_id=str(200+i),
                line=line, prices=dict(h2h=None, total=None if line is None else dict(line=line, over=1.9, under=1.9)),
                market_mode='test', market_evidence=[], source_market_status='TEST', fixture_sources=[],
                r7_first_issue='a' * 64, odds_source_first_issue='b' * 64, r10_first_issue='c' * 64,
                source_slots={}, r15_registration_sha256='d' * 64, r15_first_sha256='e' * 64,
                r15_rank=10-i, r15_seat=10-i if 10-i <= 3 else None, r15_prior_display={'status': 'saved'},
                rank=i+1, seat=i+1 if i<3 else None, panel_sha256=self.common['panel_sha256'],
                r7_forward_sha256='7' * 64, r10_recipe_sha256='8' * 64)
        spec = dict(version=f.PARENT_VERSION, competitions=[f.CODE], window='nov28_29', training_through=f.THROUGH,
                    inputs_cutoff_at=self.start.isoformat(), r15_history_coverage={'scheduled': 10},
                    r15_inputs=dict(panel_sha256=self.common['panel_sha256'], r7_forward_sha256='7' * 64,
                                    r10_recipe_sha256='8' * 64),
                    payload_sha256={m: f.digest(p) for m, p in self.originals.items()})
        self.parent_store = self.root / 'parent-scopes'
        plan = f.ledger.register(list(self.fixtures.values()), spec, f.PARENT_TRACK, self.parent, freeze_root=self.parent_store)
        matches = {m: utc_receipt(f.ledger.issue(plan, dict(fx, cutoff_at=plan['recorded_at']), self.originals[m],
                                               f.PARENT_TRACK, self.parent, freeze_root=self.parent_store))
                   for m, fx in self.fixtures.items()}
        self.index = dict(registration=utc_receipt(plan), matches=matches)
        write(self.parent / 'receipts.json', self.index)
        self.patch_constant('PARENT_REGISTRATION', plan['sha256'])
        self.patch_constant('PARENT_FIRSTS', f.digest({m: b['sha256'] for m, b in matches.items()}))

    def patch_constant(self, name, value):
        manager = patch.object(f, name, value)
        manager.start()
        self.addCleanup(manager.stop)

    def run_forward(self, *, issue=False, out=None):
        return f.run(self.src, self.parent, out or self.out, issue=issue, freeze_root=self.store)

    def test_preview_signed_scores_and_head_specific_cells_and_lines(self):
        result = self.run_forward()
        self.assertEqual(result['mode'], 'preview')
        self.assertFalse(self.out.exists())
        self.assertFalse(self.store.exists())
        expected_order = sorted(self.fixtures, key=lambda m: (f.aware(self.fixtures[m]['kickoff_at']), m))
        self.assertNotEqual(expected_order, sorted(self.fixtures))
        self.assertEqual(result['identity']['ranking'], expected_order)
        self.assertEqual([s['match_id'] for s in result['identity']['selected'][f.CODE]], expected_order[:3])
        for rank, mid in enumerate(expected_order, 1):
            old = self.originals[mid]
            p = result['payloads'][mid]
            prior = np.array(old['r7_grid'])
            full = f.model.adjusted(prior, self.recipe['calibration'])
            pick = p['forecasts']['candidate']
            self.assertEqual(pick['top3'], old['forecasts']['r7']['top3'])
            self.assertEqual(pick['primary'], '1-0')
            self.assertEqual(p['quality']['p_first'], prior[1,0])
            self.assertLess(p['quality']['p_first'], prior.max())
            self.assertEqual(pick['wdl'], f.base.pick(full, old['line'])['wdl'])
            self.assertEqual(pick['total_side'], f.base.pick((prior+full)/2, old['line'])['total_side'])
            self.assertEqual(pick['total_line'], old['line'])
            self.assertEqual(p['forecasts']['market'], old['forecasts']['market'])
            self.assertEqual((p['rank'], p['seat']), (rank, rank if rank <= 3 else None))
            self.assertEqual((p['r18_rank'],p['r18_seat'],p['r15_rank'],p['r15_seat']),
                             (old['rank'],old['seat'],old['r15_rank'],old['r15_seat']))
            self.assertEqual(result['identity']['payload_sha256'][mid], f.digest(p))

    def test_no_state_effect_inherits_all_signed_heads_and_ignores_target_halves(self):
        mid, old = next(iter(self.originals.items()))
        rec = f.ledger._read(self.parent, self.index['matches'][mid])
        recipe = dict(self.recipe, calibration=dict(self.recipe['calibration'], leading=1., trailing=1.),
                      doses=dict.fromkeys(f.HEADS, 1.))
        payload = f._forecast(rec, recipe)
        self.assertEqual(payload['forecasts']['candidate'], old['forecasts']['r7'])
        self.assertEqual(payload['forecasts']['fixed_full'], old['forecasts']['r7'])
        self.assertEqual(set(payload['head_sources'].values()), {'signed_R7'})
        poisoned = copy.deepcopy(rec)
        poisoned['payload'].update(half1=[9,0], half2=[2,2], actual=[11,2])
        self.assertEqual(f._forecast(poisoned, recipe), payload)

    def test_parent_population_firsts_utc_and_scope_are_checked_before_writing(self):
        mid = next(iter(self.fixtures))
        for change in ('missing','sha','clock','path'):
            with self.subTest(change=change):
                idx = copy.deepcopy(self.index)
                if change == 'missing':
                    idx['matches'].pop(mid)
                elif change == 'sha':
                    idx['matches'][mid]['sha256'] = '0' * 64
                elif change == 'clock':
                    idx['matches'][mid]['recorded_at_utc'] = '2099-01-01T00:00:00+00:00'
                else:
                    idx['matches'][mid]['file'] = '../receipts.json'
                write(self.parent / 'receipts.json', idx)
                with self.assertRaises(ValueError):
                    self.run_forward(issue=True)
                self.assertFalse(self.store.exists())
                self.assertFalse(self.out.exists())
        write(self.parent / 'receipts.json', self.index)
        with self.assertRaises(ValueError):
            f._parent(self.parent, self.start)
        first = self.parent / self.index['matches'][mid]['file']
        rec = f.read(first)
        rec['payload']['r7_grid'][0][0] += .01
        write(first, rec)
        with self.assertRaises(ValueError):
            self.run_forward()

    def test_substituted_fit_forward_calibration_and_input_files_are_rejected(self):
        for name in ('forward.json', 'fit.json', 'inputs.json'):
            with self.subTest(file=name):
                path = self.src / name
                original = path.read_bytes()
                doc = json.loads(original)
                if name == 'forward.json':
                    doc['calibration']['leading'] = .85
                elif name == 'fit.json':
                    doc['doses']['score'] = .5
                else:
                    doc['halves_sha256'] = 'f' * 64
                write(path, doc)
                with self.assertRaisesRegex(ValueError, 'historical panel'):
                    self.run_forward(issue=True)
                path.write_bytes(original)
        self.assertFalse(self.store.exists())

    def test_semantic_fit_binding_and_full_population(self):
        path = self.src / 'forward.json'
        for k, value in [('fit_sha256', 'a' * 64), ('panel_sha256','e' * 64), ('halves_sha256','f' * 64),
                         ('training_through','2040-12-01'), ('training_games',9),
                         ('doses',dict.fromkeys(f.HEADS, True)),
                         ('calibration',dict(self.recipe['calibration'], training_observed=9))]:
            with self.subTest(field=k):
                write(path, dict(self.recipe, **{k:value}))
                with patch.dict(f.STUDY_SHA256, {'forward.json': f.sha(path)}), self.assertRaises(ValueError):
                    self.run_forward()

    def test_idempotent_ledger_recovery_after_export_failure_and_after_kickoff(self):
        self.store.mkdir()
        unrelated = self.store / 'other-research-track'
        unrelated.write_bytes(b'original first')
        with patch.object(f, '_export', side_effect=OSError('interrupted export')), self.assertRaises(OSError):
            self.run_forward(issue=True)
        self.assertFalse(self.out.exists())
        saved = self.run_forward(issue=True)
        again = self.run_forward(issue=True)
        self.assertEqual(saved, again)
        self.assertEqual(f.read(self.out/'receipts.json')['matches'], saved['matches'])
        self.assertEqual(unrelated.read_bytes(), b'original first')
        scopes = self.store / f.TRACK / 'scopes'
        self.assertEqual(len(list(scopes.glob('*/000001.json'))), 11)
        self.assertFalse(list(scopes.glob('*/000002.json')))
        for mid, ref in saved['matches'].items():
            r = f._first_ref(self.out, ref, f.TRACK, scopes=scopes)
            self.assertEqual(r['fixture'], dict(self.fixtures[mid], cutoff_at=saved['registration']['recorded_at']))
            self.assertEqual(r['payload']['r18_first_sha256'], self.index['matches'][mid]['sha256'])
            self.assertEqual(r['payload']['registration_sha256'], saved['registration']['sha256'])
        second = self.root / 'readback'
        with patch.object(f, '_now', return_value=self.earliest + timedelta(days=1)):
            self.assertEqual(saved, self.run_forward(issue=True, out=second))
            with self.assertRaises(ValueError):
                self.run_forward()
        self.assertEqual({p.name:p.read_bytes() for p in self.out.iterdir()},
                         {p.name:p.read_bytes() for p in second.iterdir()})
        first = self.out / next(iter(saved['matches'].values()))['file']
        first.write_bytes(b'conflicting export')
        with self.assertRaisesRegex(ValueError, 'export differs'):
            self.run_forward(issue=True)

    def test_earliest_kickoff_during_batch_discards_every_temporary_first(self):
        # Real ledger timestamps are used. Only the final wall-clock deadline check advances.
        actual = f._now
        with patch.object(f, '_now', side_effect=[actual(), actual(), self.earliest, self.earliest]):
            with self.assertRaisesRegex(ValueError, 'crossed earliest kickoff'):
                self.run_forward(issue=True)
        self.assertFalse((self.store / f.TRACK).exists())
        self.assertFalse(self.out.exists())
        with patch.object(f, '_now', return_value=self.earliest):
            with self.assertRaises(ValueError):
                self.run_forward(issue=True)

    def test_mid_issue_failure_and_concurrent_retry_leave_exactly_one_batch(self):
        native = f.ledger.issue
        def partial(plan, fixture, *args, **kwargs):
            if fixture['match_id'] == list(self.fixtures)[4]:
                raise OSError('interrupted fifth first')
            return native(plan, fixture, *args, **kwargs)
        with patch.object(f.ledger, 'issue', side_effect=partial), self.assertRaises(OSError):
            self.run_forward(issue=True)
        self.assertFalse((self.store / f.TRACK).exists())
        self.assertFalse(self.out.exists())
        with ThreadPoolExecutor(max_workers=2) as pool:
            calls = [pool.submit(self.run_forward, issue=True) for _ in range(2)]
            self.assertEqual(calls[0].result(), calls[1].result())
        self.assertEqual(len(f.read(self.out/'receipts.json')['matches']), 10)

    def test_input_directories_are_protected(self):
        with self.assertRaises(ValueError):
            self.run_forward(issue=True, out=self.parent)
        with self.assertRaises(ValueError):
            f.run(self.src, self.parent, self.out, issue=True, freeze_root=self.src/'store')
        self.assertFalse(self.out.exists())

    def test_parent_changed_during_issue_cannot_publish_a_batch(self):
        path = self.parent / next(iter(self.index['matches'].values()))['file']
        native = f._readback

        def changed(*args):
            result = native(*args)
            path.write_bytes(path.read_bytes() + b'\n')
            return result

        with patch.object(f, '_readback', side_effect=changed), self.assertRaisesRegex(ValueError, 'changed during issue'):
            self.run_forward(issue=True)
        self.assertFalse((self.store / f.TRACK).exists())
        self.assertFalse(self.out.exists())


class RegisteredInputTests(unittest.TestCase):
    def test_local_real_r18_and_full_fit_read_only_when_available(self):
        root = Path('/workspace/scratch')
        study, parent = root / 'r20-historical', root / 'r18-forward'
        if not (study/'forward.json').is_file() or not (parent/'receipts.json').is_file():
            self.skipTest('private original R18 and R20 inputs unavailable')
        reg = f.ledger._read(parent, f.read(parent/'receipts.json')['registration'])
        cutoff = f._now()
        if not f.aware(reg['recorded_at']) <= cutoff < f.aware(reg['fixture']['kickoff_at']):
            self.skipTest('outside the genuine R20 prematch window')
        with TemporaryDirectory() as temporary:
            target = Path(temporary)
            result = f.run(study, parent, target/'unused', freeze_root=target/'unused-ledger')
            self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(result['identity']['r18_registration_sha256'], f.PARENT_REGISTRATION)
        self.assertEqual(result['identity']['recipe_sha256'][f.CODE], f.sha(study/'forward.json'))
        self.assertEqual(result['identity']['training_games'], 2822)
        self.assertEqual(len(result['payloads']), 10)
        self.assertEqual(set(result['identity']['without_total_line']), set(result['fixtures']))
        rank = sorted(result['fixtures'], key=lambda m: (-result['payloads'][m]['quality']['p_first'],
                      f.aware(result['fixtures'][m]['kickoff_at']), m))
        self.assertEqual(result['identity']['ranking'], rank)
        self.assertEqual([s['match_id'] for s in result['identity']['selected'][f.CODE]], rank[:3])
        for p in result['payloads'].values():
            pick = p['forecasts']['candidate']
            self.assertEqual(len(set(pick['top3'])), 3)
            self.assertEqual(pick['primary'], pick['top3'][0])
            self.assertIn(pick['wdl'], f.base.SIDES)
            self.assertIsNone(pick['total_side'])


if __name__ == '__main__':
    unittest.main()
