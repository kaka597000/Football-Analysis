from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from research.analysis_freeze import aware
from . import penalty_opportunity_forward as forward
from .goal_state_study import sha, write_new
from .test_territory_forward import encoded, lines, record


class PenaltyForwardTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data, self.study, self.out, self.ranks, self.store = (
            self.root / key for key in ('data', 'study', 'out', 'ranks', 'canonical'))
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.roots = {key: self.data / key for key in ('r5', 'r7', 'r10', 'r11')}
        self.package = self.data / 'r22'
        self.manifests = {key: {'files': {}} for key in self.roots}
        self.events = self.root / 'events.jsonl'
        self.baselines, self.r22_recipes = {}, {}
        self._calendars()
        self.fixtures = {}
        for code in forward.source.CODES:
            for i in range(9):
                mid = f'{code}-{i}'
                self.fixtures[mid] = dict(match_id=mid, cohort=code, stage='League', home='espn:1', away='espn:2',
                    kickoff_at=(self.now if mid == 'eng.1-0' else self.stamp(3 - (i % 2))).isoformat())
        self._firsts()
        self.start(patch.object(forward.study, 'EVENTS_SHA', sha(self.events)))
        self.start(patch.object(forward.study, '_archives', return_value=(self.package, self.r22_manifest, {},
                         self.roots, self.manifests, None, self.lock)))
        self.start(patch.object(forward.ledger, '_now', return_value=self.now.isoformat()))
        self.start(patch.object(forward.source.r7, 'matrix', side_effect=self._prior))

    def start(self, replacement):
        self.addCleanup(replacement.stop)
        return replacement.start()

    def stamp(self, days):
        return self.now + timedelta(days=days)

    def day(self, days):
        return self.stamp(days).date().isoformat()

    def pack(self, key, name, files):
        target = self.roots[key] / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(target, 'w:gz') as bundle:
            for path, body in files.items():
                entry = tarfile.TarInfo(path)
                entry.size = len(body)
                bundle.addfile(entry, BytesIO(body))
        self.manifests[key]['files'][name] = sha(target)

    def _calendars(self):
        events, extension = [], []
        for code in forward.source.CODES:
            train = [dict(id=f'{code}-history-{day}', competition=code, stage='regular',
                          date=self.day(day), home='Home Club', away='Away Club', home_goals=1, away_goals=0,
                          score_basis='regulation_90') for day in (-11, -10, -7)]
            extension.append(train[-1])
            for offset in (-10, -7, 0):
                events.append(dict(competition=code, source_match_id=f'{code}-source-{offset}',
                    date=self.day(offset), home='Home Club', away='Away Club', home_id='100', away_id='200',
                    source_sha256='a' * 64, source_fetched_at_utc=self.stamp(-1).isoformat(),
                    source_history_sides={'home': dict(xG=1.8, npxG=1, xGA=1., npxGA=1.),
                                          'away': dict(xG=1., npxG=1., xGA=1.8, npxGA=1.)}))
            panel = lines(train[:-1])
            self.pack('r5', f'models/{code}.tar.gz', {'panel.jsonl': panel})
            r7_fit = encoded(dict(source_panel_sha256=sha256(panel).hexdigest()))
            baseline = encoded(dict(version=forward.source.r7.model.VERSION, competition=code,
                                    through=self.day(-10), fit_sha256=sha256(r7_fit).hexdigest()))
            self.baselines[code] = sha256(baseline).hexdigest()
            self.pack('r7', f'models/{code}.tar.gz', {'main/fit.json': r7_fit, 'main/forward.json': baseline})

            r22_fit = self.package / 'replay/reference/main' / code / 'fit.json'
            r22_fit.parent.mkdir(parents=True)
            write_new(r22_fit, dict(competition=code, dose=.5))
            self.r22_recipes[code] = sha(r22_fit)
            folder = self.study / 'main' / code
            folder.mkdir(parents=True)
            validation = folder / 'fit.json'
            dose = 0. if code == 'eng.1' else 1.
            bindings = dict(r7_recipe_sha256=self.baselines[code])
            write_new(validation, dict(dose=dose, inputs=bindings))
            write_new(folder / 'forward.json', dict(version=forward.model.VERSION, competition=code,
                      through=self.day(-7), inputs=bindings, dose=dose, fitted=dict(beta=[.75, 0.]),
                      fit_sha256=sha(validation)))
        self.pack('r11', 'extension.tar.gz', {'fixture-panels.jsonl': lines(extension), 'manifest.json': encoded(
                  dict(sources=[dict(league=c, acquired_at_utc=self.stamp(-3).isoformat()) for c in forward.source.CODES]))})
        self.events.write_bytes(lines(events))
        write_new(self.study / 'inputs.json', dict(protocol_sha256=forward.study.PROTOCOL_SHA,
                                                  events_sha256=sha(self.events)))

    def _firsts(self):
        for key, folder in self.roots.items():
            folder.mkdir(parents=True, exist_ok=True)
            write_new(folder / 'manifest.json', self.manifests[key])
        histories = {}
        tracks = dict(r5='synthetic-r5', r7='synthetic-r7', r10='synthetic-r10', r22=forward.r22.TRACK)
        plan_ids = {}
        for key, offset in (('r5', -6), ('r7', -5), ('r10', -4), ('r22', -2)):
            track, stamp = tracks[key], self.stamp(offset)
            weights = self.r22_recipes if key == 'r22' else self.baselines
            identity = dict(recipe_sha256=weights)
            if key == 'r5':
                identity['model'] = forward.source.base.VERSION
            if key in ('r7', 'r10'):
                identity['version'] = forward.source.r7.model.VERSION if key == 'r7' else forward.source.decision.VERSION
            if key == 'r22':
                identity.update(version=forward.r22.model.VERSION, events_sha256=sha(self.events),
                                archive_manifest_sha256={k: sha(p / 'manifest.json') for k, p in self.roots.items()},
                                r21_study_inputs_sha256='c' * 64, **plan_ids)
            files, refs = {}, {}
            plan = record(files, track + '-registration', dict(match_id=track + '-plan'),
                          dict(track=track, identity=identity, fixtures=self.fixtures), stamp)
            for mid, fixture in self.fixtures.items():
                code = fixture['cohort']
                parents = histories.setdefault(mid, {})
                line = None if mid == 'fra.1-1' else 2.25
                total = dict(line=line, over=1.91, under=1.96) if line else None
                prices = dict(h2h=dict(home=2.1, draw=3.1, away=3.4), total=total)
                quote = dict(bookmaker='synthetic', source_sha256='e' * 64, source_url='https://example.org/odds',
                             observed_at=self.stamp(-6).isoformat(), quoted_at=None)
                evidence = dict(h2h=quote, total=quote) if total else dict(h2h=quote)
                market = dict(wdl='home')
                control = dict(primary='1-0', top3=['1-0', '1-1', '0-0'], wdl='away',
                               total_side='under' if line else None, total_line=line)
                if key == 'r5':
                    teams = ['Mystery Home', 'Mystery Away'] if mid == 'fra.1-1' else ['Home Club', 'Away Club']
                    payload = dict(model_stage='regular', model_teams=teams, market_mode='paired' if line else 'hda_only',
                                   neutral=False, prices=prices, market_evidence=evidence, forecasts=dict(market=market))
                elif key == 'r7':
                    payload = dict(input_sha256=parents['r5']['sha256'], model_sha256=weights[code],
                                   training_through=self.day(-10), forecasts=dict(candidate=control, market=market))
                elif key == 'r10':
                    payload = dict(odds_source_first_issue=parents['r5']['sha256'], r7_forward_sha256=self.baselines[code],
                                   recipe_sha256=weights[code], training_through=self.day(-10),
                                   forecasts=dict(r7=control, market=market))
                else:
                    payload = dict(version=forward.r22.model.VERSION, formal_kpi_eligible=False,
                                   r7_first_issue=parents['r7']['sha256'], odds_source_first_issue=parents['r5']['sha256'],
                                   odds_cutoff_at=self.stamp(-6).isoformat(), training_through=self.day(-7), prices=prices,
                                   market_evidence=evidence, baseline_sha256=self.baselines[code], recipe_sha256=weights[code],
                                   forecasts=dict(candidate=dict(control, primary='0-0', top3=['0-0', '1-0', '1-1']),
                                                  r7=control, market=market))
                payload.update(registration_sha256=plan['sha256'], line=line)
                refs[mid] = record(files, track, dict(fixture, cutoff_at=stamp.isoformat()),
                                   payload, stamp + timedelta(seconds=1))
                parents[key] = refs[mid]
            files['receipts.json'] = encoded(dict(registration=plan, matches=refs))
            plan_ids[key + '_registration'] = plan['sha256']
            if key == 'r22':
                destination = self.package / 'forward'
                destination.mkdir()
                for name, body in files.items():
                    (destination / name).write_bytes(body)
                receipt = self.package / 'replay/reference/inputs.json'
                write_new(receipt, {})
                self.r22_manifest = dict(files={str(p.relative_to(self.package)): sha(p)
                                               for p in self.package.rglob('*.json')})
                self.r22_manifest['files']['source-lock.json'] = 'a' * 64
                self.lock = dict(official_forward_registration_sha256=plan['sha256'],
                                 r21_parent=dict(understat_archive_sha256='b' * 64))
                self.r22_index = refs
            else:
                archive, prefix = next((a, p) for k, a, p in forward.FIRST_ARCHIVES if k == key)
                self.pack(key, archive, {str(Path(prefix) / name): body for name, body in files.items()})
                (self.roots[key] / 'manifest.json').write_bytes(encoded(self.manifests[key]))

    @staticmethod
    def _prior(game, recipe):
        prior = np.array([[.30, .13, .08], [.18, .12, .04], [.08, .05, .02]])
        if game['id'].endswith('-8'):
            prior[0, 0], prior[1, 0] = .28, .20
        return prior

    def issue(self):
        return forward.run(self.data, self.events, self.study, self.out, self.ranks, freeze_root=self.store)

    def test_firsts_keep_original_45_market_gaps_directions_and_rank(self):
        missing = 'eng.1-1'
        (self.package / 'forward' / self.r22_index[missing]['file']).unlink()
        checked = forward.check(self.data, self.events, self.study)
        self.assertEqual(checked['status'], 'READY')
        self.assertEqual(checked['original_population'], 45)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.store.exists())
        result = self.issue()
        self.assertEqual(result['first_issues'], 43)
        index = forward.source.read(self.out / 'receipts.json')
        coverage = forward.source.read(self.out / 'coverage.json')
        self.assertEqual(set(coverage['fixtures']), set(self.fixtures))
        self.assertEqual(coverage['fixtures']['eng.1-0']['disposition'], 'already_started')
        self.assertEqual(coverage['fixtures'][missing]['missing_inputs'], ['r22_first_issue_missing'])
        self.assertEqual(coverage['fixtures'][missing]['disposition'], 'unavailable_inputs')
        self.assertEqual(set(index['matches']), set(self.fixtures) - {'eng.1-0', missing})
        pending = forward.source.read(self.out / 'pending.json')
        self.assertEqual(pending['fra.1']['comparisons']['r7_vs_candidate']['tracks']['candidate']
                         ['metrics']['actual_ou']['issued'], 8)
        picks = {}
        for mid, ref in index['matches'].items():
            saved = forward.ledger._read(self.out, ref)
            p, f = saved['payload'], saved['fixture']
            parent = forward.source.read(self.package / 'forward' / self.r22_index[mid]['file'])
            self.assertEqual(f['cutoff_at'], result['registration']['recorded_at'])
            self.assertLess(aware(saved['recorded_at']), aware(f['kickoff_at']))
            self.assertEqual(p['r22_first_issue'], parent['record_sha256'])
            self.assertEqual(p['r7_first_issue'], parent['payload']['r7_first_issue'])
            self.assertEqual(p['market_evidence'], parent['payload']['market_evidence'])
            self.assertEqual(p['history_cutoff'], aware(f['cutoff_at']).astimezone(timezone.utc).date().isoformat())
            self.assertTrue(all(g['date'] < p['history_cutoff'] for s in ('home', 'away')
                                for g in p['process'][s]['games']))
            arms = p['forecasts']
            self.assertEqual(arms['r22'], parent['payload']['forecasts']['candidate'])
            for arm in ('candidate', 'full_dose'):
                self.assertEqual(arms[arm]['wdl'], 'away')
                self.assertEqual(arms[arm]['total_side'], arms['r7']['total_side'])
                self.assertEqual(len(set(arms[arm]['top3'])), 3)
            if f['cohort'] == 'eng.1':
                self.assertEqual(arms['candidate'], arms['r7'])
                self.assertEqual(p['quality']['p_first'], self._prior({'id': mid}, {})[1, 0])
            picks[mid] = p
        gap = picks['fra.1-1']
        self.assertEqual(gap['market_gaps'], ['h2h_quote_clock_missing', 'total_prices_missing'])
        self.assertIsNone(gap['line'])
        self.assertIsNone(gap['process']['home']['source_team_id'])
        self.assertEqual(gap['features'], [0., 0., 0., 0.])
        self.assertEqual(gap['forecasts']['candidate'], gap['forecasts']['r7'])
        ranked = forward.ledger._read(self.ranks, result['ranking'])['payload']['identity']['selected']
        for code, rows in ranked.items():
            ids = [mid for mid in picks if self.fixtures[mid]['cohort'] == code]
            ordered = sorted(ids, key=lambda mid: (-picks[mid]['quality']['p_first'],
                                                   aware(self.fixtures[mid]['kickoff_at']), mid))
            self.assertEqual([r['match_id'] for r in rows], ordered[:3])
        self.assertEqual(self.issue(), result)
        self.assertFalse(list(self.store.rglob('000002.json')))
        with self.assertRaisesRegex(ValueError, 'already has a different first issue'):
            forward.run(self.data, self.events, self.study, self.root / 'different', self.root / 'other-ranks',
                        freeze_root=self.store)

    def test_rejects_changed_baseline_or_signed_r22_record_before_freezing(self):
        path = self.study / 'main/eng.1/forward.json'
        original = path.read_bytes()
        recipe = json.loads(original)
        recipe['inputs']['r7_recipe_sha256'] = '0' * 64
        path.write_bytes(encoded(recipe))
        with self.assertRaisesRegex(ValueError, 'R7 baseline hash'):
            self.issue()
        path.write_bytes(original)
        path = self.package / 'forward' / self.r22_index['eng.1-1']['file']
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'source checksum mismatch'):
            self.issue()
        self.assertFalse(self.store.exists())

    def test_rejects_same_day_history_and_changed_directions_before_freezing(self):
        context = forward.model.Design.context

        def future_history(design, game, cutoff):
            feature, exposure = context(design, game, cutoff)
            exposure['home']['games'].append(dict(date=cutoff))
            return feature, exposure

        with patch.object(forward.model.Design, 'context', future_history):
            with self.assertRaisesRegex(ValueError, 'as-of club history'):
                self.issue()
        choices = forward.model.choices

        def changed_direction(*args):
            pick, grid = choices(*args)
            return dict(pick, wdl='home'), grid

        with patch.object(forward.model, 'choices', changed_direction):
            with self.assertRaisesRegex(ValueError, 'inherited R7 directions'):
                self.issue()
        self.assertFalse(list(self.store.rglob('000001.json')))

    def test_all_started_preserves_45_dispositions_without_a_registration(self):
        with patch.object(forward.ledger, '_now', return_value=self.stamp(4).isoformat()):
            result = self.issue()
        self.assertEqual(result['status'], 'NO_ELIGIBLE_FIXTURES')
        self.assertEqual(set(result['fixtures']), set(self.fixtures))
        self.assertTrue(all(row['disposition'] == 'already_started' for row in result['fixtures'].values()))
        self.assertFalse(list(self.store.rglob('000001.json')))


if __name__ == '__main__':
    unittest.main()
