from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research.analysis_freeze import aware, digest
from research_program import territory_forward as forward
from research_program.goal_state_study import sha, write_new


def encoded(obj):
    return json.dumps(obj, sort_keys=True).encode()


def lines(rows):
    return b''.join(encoded(row) + b'\n' for row in rows)


def record(files, track, fixture, payload, timestamp):
    scope = dict(track=track, stage='prematch', match_id=fixture['match_id'])
    fid = digest(scope)[:16] + '-v000001'
    body = dict(scope, fixture=fixture, payload=payload, freeze_id=fid, revision=1, previous_sha256=None,
                recorded_at=timestamp.isoformat(), evaluation_slot='FIRST_PREMATCH')
    body['record_sha256'] = digest(body)
    files[fid + '.json'] = encoded(body)
    return dict(file=fid + '.json', id=fid, sha256=body['record_sha256'], recorded_at=body['recorded_at'])


class TerritoryForwardTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.day = datetime.now(timezone.utc).date()
        self.study, self.out, self.store = (self.root / k for k in ('study', 'out', 'canonical'))
        self.archives = {k: self.root / k for k in ('r5', 'r7', 'r10', 'r11')}
        self.files = {k: {} for k in self.archives}
        self.events, self.protocol = self.root / 'events.jsonl', self.root / 'protocol.json'
        write_new(self.protocol, dict(version=forward.hurdle.VERSION))
        self.baselines = {}
        extension = []
        for code in forward.CODES:
            folder = self.study / 'main' / code
            folder.mkdir(parents=True)
            games = [dict(id=f'{code}-{i}', date=self.date(i), home='Panel Alias', away='Away R5',
                          competition=code, stage='regular', score_basis='regulation_90', home_goals=1, away_goals=2)
                     for i in (-12, -11, -10, -8, -1)]
            panel = lines(games[:3])
            self.pack('r5', f'models/{code}.tar.gz', {'panel.jsonl': panel})
            fit = encoded(dict(source_panel_sha256=sha256(panel).hexdigest()))
            anchor = encoded(dict(competition=code, version=forward.r7.model.VERSION, through=self.date(-10),
                                  fit_sha256=sha256(fit).hexdigest(), state=forward.r7.model.dump(forward.r7.model.blank()),
                                  policy={'mix': 0}))
            self.baselines[code] = sha256(anchor).hexdigest()
            self.pack('r7', f'models/{code}.tar.gz', {'main/forward.json': anchor, 'main/fit.json': fit})
            extension.extend(games[3:])
            write_new(folder / 'fit.json', dict(competition=code))
            full = forward.hurdle.fit([], [], [])
            xg = forward.hurdle.fit([], [], [], xg_only=True)
            full['scoring_beta'][2] = xg['scoring_beta'][0] = 1.
            write_new(folder / 'forward.json', dict(version=forward.hurdle.VERSION, competition=code,
                      through=self.date(-8), baseline_sha256=self.baselines[code], fit_sha256=sha(folder / 'fit.json'),
                      doses=dict(score=0., wdl=1., total=.5), xg_doses=dict(score=1., wdl=0., total=0.),
                      fitted=full, xg_fitted=xg))
        captured = self.date(-1) + 'T01:00:00Z'
        manifest = dict(sources=[dict(league=c, acquired_at_utc=captured) for c in forward.CODES])
        self.pack('r11', 'extension.tar.gz', {'fixture-panels.jsonl': lines(extension), 'manifest.json': encoded(manifest)})
        self.events.write_bytes(lines(dict(competition='eng.1', date=self.date(i), source_match_id=str(i),
                    home='Understat Home', away='Away R5', home_id='100', away_id='200',
                    home_xg=3., away_xg=.5, home_deep=12., away_deep=2., source_fetched_at_utc=captured)
                    for i in (-12, -11, -10, -8, -1, 0)))
        self.fixtures = [dict(match_id='test-' + c, cohort=c, home='espn:11', away='espn:22', stage='League',
                              kickoff_at=self.date(3) + 'T00:30:00+14:00') for c in (*forward.CODES, 'ned.1')]
        self.fixtures.append(dict(self.fixtures[0], match_id='test-expired', kickoff_at=self.date(-1) + 'T16:00:00Z'))
        self.sources()
        write_new(self.study / 'inputs.json', dict(events_sha256=sha(self.events),
                  extension_manifest_sha256=sha(self.archives['r11'] / 'manifest.json')))

    def date(self, days):
        return (self.day + timedelta(days=days)).isoformat()

    def pack(self, key, name, files):
        path = self.archives[key] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(path, 'w:gz') as archive:
            for filename, body in files.items():
                member = tarfile.TarInfo(filename)
                member.size = len(body)
                archive.addfile(member, BytesIO(body))
        self.files[key][name] = sha(path)

    def sources(self, *, missing_r7=None, changed_line=False, late_quote=False):
        raw_hash = {}
        r10_weights = {c: digest(['r10', c]) for c in forward.CODES}
        identities = dict(r5=dict(model=forward.base.VERSION),
                          r7=dict(version=forward.r7.model.VERSION, recipe_sha256=self.baselines),
                          r10=dict(version=forward.decision.VERSION, recipe_sha256=r10_weights))
        for i, (key, name, prefix) in enumerate((('r5', 'forward-first-issues.tar.gz', 'issued/'),
                 ('r7', 'first-issues.tar.gz', ''), ('r10', 'forward/first-issues.tar.gz', ''))):
            stamp = datetime.combine(self.day - timedelta(days=3), time(i), timezone.utc)
            fixtures = {f['match_id']: f for f in self.fixtures if key != 'r7' or f['match_id'] != missing_r7}
            files = {}
            track = 'test-' + key
            plan = record(files, track + '-registration', dict(match_id=track + '-plan'),
                          dict(fixtures=fixtures, track=track, identity=identities[key]), stamp)
            receipts = {}
            for mid, f in fixtures.items():
                code = f['cohort']
                line = None if code == 'fra.1' else 2.25
                total = dict(line=line, over=1.9, under=2.) if line else None
                market = dict(wdl='draw')
                control = dict(primary='4-4', top3=['4-4', '1-0', '0-1'], wdl='away',
                               total_side='under' if line else None, total_line=line)
                if key == 'r5':
                    observed = stamp + timedelta(days=2) if late_quote else stamp
                    p = dict(model_stage='regular', model_teams=['Panel Alias', 'Away R5'], home_name='ESPN Home',
                             away_name='ESPN Away', market_mode='paired' if line else 'hda_only', neutral=False,
                             prices=dict(h2h=dict(home=2., draw=3., away=4.), total=total),
                             market_evidence=dict(h2h=dict(source_sha256='f'*64, observed_at=observed.isoformat())),
                             forecasts=dict(market=market))
                elif key == 'r7':
                    p = dict(input_sha256=raw_hash[mid], model_sha256=self.baselines.get(code),
                             forecasts=dict(candidate=control, market=market), training_through=self.date(-10))
                else:
                    p = dict(odds_source_first_issue=raw_hash[mid], r7_forward_sha256=self.baselines.get(code),
                             forecasts=dict(candidate=dict(control, wdl='home'), r7=control, market=market),
                             recipe_sha256=r10_weights.get(code), training_through=self.date(-10))
                p.update(registration_sha256=plan['sha256'], line=4.5 if changed_line and key == 'r10' else line)
                rec = record(files, track, dict(f, cutoff_at=stamp.isoformat()), p, stamp + timedelta(minutes=5))
                receipts[mid] = rec
                if key == 'r5':
                    raw_hash[mid] = rec['sha256']
            files['receipts.json'] = encoded(dict(registration=plan, matches=receipts))
            self.pack(key, name, {prefix + k: v for k, v in files.items()})
        for key, files in self.files.items():
            (self.archives[key] / 'manifest.json').write_bytes(encoded(dict(files=files)))

    def issue(self):
        return forward.run(*self.archives.values(), self.study, self.events, self.protocol, self.out,
                           freeze_root=self.store)

    def test_first_issues_use_the_same_population_model_names_and_real_line(self):
        paths = dict(self.archives, study=self.study, events=self.events, protocol=self.protocol,
                     out=self.out, **{'freeze-root': self.store})
        args = [arg for key, path in paths.items() for arg in ('--' + key, str(path))]
        cli = subprocess.run([sys.executable, '-m', 'research_program.territory_forward', *args],
                             cwd=Path(forward.__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        plan = json.loads(cli.stdout)
        idx = forward.read(self.out / 'receipts.json')
        self.assertEqual(set(idx['matches']), {'test-' + c for c in forward.CODES})
        registration = forward.ledger._read(self.out, plan)['payload']['identity']
        self.assertEqual(registration['already_started'], ['test-expired'])
        self.assertEqual(registration['events_sha256'], sha(self.events))
        issue = forward.ledger._read(self.out, idx['matches']['test-eng.1'])
        p = issue['payload']
        tips, process = p['forecasts'], p['process']
        self.assertFalse(p['formal_kpi_eligible'])
        self.assertEqual((p['home_name'], p['away_name']), ('Panel Alias', 'Away R5'))
        self.assertEqual((process['home_method'], process['home_signature']['games']), ('calendar', 4))
        self.assertEqual(process['home_exposure']['xg_for'], 5)
        self.assertEqual(process['home_history_dates'][-1], self.date(-1))
        self.assertEqual((process['date'], process['cutoff']), (self.date(2), self.date(0)))
        self.assertEqual(p['history_cutoff'], aware(issue['fixture']['cutoff_at']).astimezone(timezone.utc).date().isoformat())
        self.assertEqual(p['prices']['total']['line'], 2.25)
        self.assertEqual(p['baseline_sha256'], self.baselines['eng.1'])
        self.assertEqual(tips['candidate']['top3'], tips['r7']['top3'])
        self.assertEqual(tips['xg_matched_dose']['top3'], tips['r7']['top3'])
        self.assertEqual(tips['xg']['top3'], tips['fixed_xg']['top3'])
        self.assertNotEqual(tips['xg']['top3'], tips['r7']['top3'])
        self.assertNotEqual(tips['r7'], tips['r10'])
        missing = forward.ledger._read(self.out, idx['matches']['test-fra.1'])['payload']
        self.assertIsNone(missing['line'])
        self.assertFalse(missing['process']['available'])
        for arm in ('candidate', 'xg', 'xg_matched_dose', 'fixed_xg', 'fixed_full'):
            self.assertEqual(missing['forecasts'][arm], missing['forecasts']['r7'])
        pending = forward.read(self.out / 'pending.json')
        for c in forward.CODES:
            self.assertEqual(pending[c]['registered'], 1)
            self.assertTrue(all(r['pending'] == 1 for r in pending[c]['comparisons'].values()))
        self.assertEqual(pending['fra.1']['comparisons']['vs_r7']['tracks']['candidate']['metrics']['actual_ou']['issued'], 0)

    def test_interrupted_issue_resumes_once_and_later_retry_keeps_original_cutoff(self):
        original = forward.ledger.issue
        saved = []

        def interrupt(*args, **kwargs):
            if saved:
                raise RuntimeError('interrupted')
            saved.append(original(*args, **kwargs))
            return saved[0]

        with patch.object(forward.ledger, '_now', return_value=self.date(0) + 'T13:59:00+14:00'):
            with patch.object(forward.ledger, 'issue', side_effect=interrupt), self.assertRaises(RuntimeError):
                self.issue()
        first = forward.ledger._read(self.out, saved[0])
        self.assertEqual(first['payload']['history_cutoff'], self.date(0))
        plan_before = forward._existing(self.out)
        after_kickoff = datetime.combine(self.day + timedelta(days=10), time(), timezone.utc)

        class LateClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return after_kickoff.astimezone(tz)

        with patch('research.analysis_freeze.datetime', LateClock), self.assertRaisesRegex(ValueError, 'late prematch'):
            self.issue()
        self.assertFalse((self.out / 'receipts.json').exists())
        self.assertEqual(forward._existing(self.out), plan_before)
        self.assertEqual(len(list(self.store.rglob('000001.json'))), 2)  # Registration and the first saved fixture.
        plan = self.issue()
        idx = forward.read(self.out / 'receipts.json')
        self.assertIn(saved[0], idx['matches'].values())
        before = {p.name: p.read_bytes() for p in self.out.glob('*.json')}
        with patch.object(forward.ledger, '_now', return_value=self.date(10) + 'T00:00:00Z'):
            self.assertEqual(self.issue(), plan)
        self.assertEqual({p.name: p.read_bytes() for p in self.out.glob('*.json')}, before)
        self.assertEqual(list(self.store.glob('*/000002.json')), [])
        path = self.study / 'main/eng.1/forward.json'
        original_bytes = path.read_bytes()
        recipe = forward.read(path)
        recipe['xg_doses']['total'] = .5
        path.write_bytes(encoded(recipe))
        with self.assertRaisesRegex(ValueError, 'changed after R14 registration'):
            self.issue()
        path.write_bytes(original_bytes)
        with (self.archives['r11'] / 'extension.tar.gz').open('ab') as stream:
            stream.write(b'changed after first issue')
        with self.assertRaisesRegex(ValueError, 'archive checksum mismatch'):
            self.issue()

    def test_rejects_missing_controls_changed_prices_and_future_observation(self):
        for args, error in ((dict(missing_r7='test-eng.1'), 'missing same-fixture'),
                            (dict(changed_line=True), 'R5 prices'), (dict(late_quote=True), 'after R5 cutoff')):
            with self.subTest(args=args):
                self.sources(**args)
                with self.assertRaisesRegex(ValueError, error):
                    self.issue()
                self.assertEqual(list(self.store.rglob('000001.json')), [])

    def test_checks_fit_and_source_hashes_and_training_before_issue(self):
        path = self.study / 'inputs.json'
        inputs = forward.read(path)
        for key in ('events_sha256', 'extension_manifest_sha256'):
            path.write_bytes(encoded(dict(inputs, **{key: '0'*64})))
            with self.assertRaisesRegex(ValueError, 'differs from the fitted study'):
                self.issue()
        path.write_bytes(encoded(inputs))
        recipe_path = self.study / 'main/eng.1/forward.json'
        recipe = forward.read(recipe_path)
        recipe_path.write_bytes(encoded(dict(recipe, fit_sha256='0'*64)))
        with self.assertRaisesRegex(ValueError, 'forward recipe or fit'):
            self.issue()
        recipe_path.write_bytes(encoded(recipe))
        with patch.object(forward.ledger, '_now', return_value=self.date(-8) + 'T23:59:00Z'):
            with self.assertRaisesRegex(ValueError, 'training reaches the UTC issue date'):
                self.issue()
        with patch.object(forward.ledger, '_now', return_value=self.date(-2) + 'T23:59:00Z'):
            with self.assertRaisesRegex(ValueError, 'source acquired after issue cutoff'):
                self.issue()
        self.assertEqual(list(self.store.rglob('000001.json')), [])


if __name__ == '__main__':
    unittest.main()
