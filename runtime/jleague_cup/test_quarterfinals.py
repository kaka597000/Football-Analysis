import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jleague_cup import database, evidence, quarterfinals as qf


class QuarterfinalTests(unittest.TestCase):
    def history(self):
        rows = []
        for year in ('2023', '2024', '2025'):
            for tie in range(4):
                for leg in (1, 2):
                    home, away = (str(tie * 2), str(tie * 2 + 1))
                    if leg == 2:
                        home, away = away, home
                    rows.append({'match_id': f'{year}-{tie}-{leg}', 'season': year,
                                 'date': f'{year}-09-0{leg}', 'home': home, 'away': away,
                                 'leg': leg, 'stage': 'quarterfinals', 'tie_format': 'two-leg',
                                 'cohort': 'original-edition-cohort', 'home_goals': 2,
                                 'away_goals': 1, 'neutral': None})
        return rows

    def cert(self, root):
        raw = root / 'rules.html'
        raw.write_text('Official QF two legs 90 minutes no away goal extra time penalties')
        s = {'raw_path': str(raw), 'raw_sha256': evidence.sha(raw),
             'source_url': 'https://www.jleague.jp/leaguecup/outline/',
             'query_time': (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
             'excerpt': 'QF two legs 90 minutes no away goal extra time penalties'}
        entry = {'rules': qf.RULES, 'support': {k: [s] for k in qf.RULES}}
        c = {'schema': 'WCPP-JCUP-QF-RULES-1', 'reviewed': True,
             'compatibility_scope': 'quarterfinal_mechanics_only', 'target_edition': '2026/27',
             'historical_editions': ['2023'], 'editions': {'2023': entry, '2026/27': entry},
             'team_aliases': {'a': {'reviewed': True, 'official_name': 'チームA', 'historical_name': 'Team A'},
                              'b': {'reviewed': True, 'official_name': 'チームB', 'historical_name': 'Team B'}}}
        en = root / 'english.html'
        obj = {'date': '$D2026-11-11T10:00:00Z', 'homeTeam': {'code': 'a', 'name': 'Team A'},
               'awayTeam': {'code': 'b', 'name': 'Team B'}}
        en.write_text('<script>self.__next_f.push([1,' + json.dumps(json.dumps(obj)) + '])</script>')
        for alias in c['team_aliases'].values():
            alias['official_english_name'] = alias['historical_name']
            alias['english_source'] = {**s, 'raw_path': str(en), 'raw_sha256': evidence.sha(en)}
        p = root / 'certificate.json'
        p.write_text(json.dumps(c))
        return c, p

    def fixture(self, root):
        now = datetime.now(timezone.utc)
        kickoff = now + timedelta(days=2)
        url = 'https://www.jleague.jp/match/leaguecup/2026/111101/'
        data = {'date': '$D' + kickoff.isoformat().replace('+00:00', 'Z'),
                'homeTeam': {'code': 'a', 'name': 'チームA'}, 'awayTeam': {'code': 'b', 'name': 'チームB'}}
        raw = '<link rel="canonical" href="' + url + '">準々決勝 第1戦'
        raw += '<script>self.__next_f.push([1,' + json.dumps(json.dumps(data, separators=(',', ':'), ensure_ascii=False)) + '])</script>'
        path = root / 'fixture.html'
        path.write_text(raw)
        return {'match_id': 'jleague:111101', 'competition': 'jpn.league_cup', 'edition': '2026/27',
                'gender': 'men', 'age': 'senior', 'stage': 'quarterfinals', 'tie_format': 'two-leg',
                'leg': 1, 'phase': 'PreMatch', 'cutoff_at': now.isoformat(), 'kickoff_at': kickoff.isoformat(),
                'date': kickoff.date().isoformat(), 'neutral': None, 'home': database.team('Team A'),
                'away': database.team('Team B'), 'home_official_code': 'a', 'away_official_code': 'b',
                'phase_evidence': {'raw_path': str(path), 'raw_sha256': evidence.sha(path),
                                   'query_time': now.isoformat(), 'source_url': url}}

    def test_reversed_second_leg_aggregate_is_prematch_only(self):
        before = self.history()
        original = copy.deepcopy(before)
        rows = qf.pair_history(before, ['2023', '2024', '2025'])
        second = next(r for r in rows if r['leg'] == 2)
        self.assertEqual((second['aggregate_before_home'], second['aggregate_before_away']), (1, 2))
        self.assertFalse(second['aggregate_context_trained'])
        self.assertEqual(before, original)
        self.assertEqual(len(rows), 24)

    def test_incomplete_duplicate_wrong_orientation_and_date_rejected(self):
        rows = self.history()[:8]
        cases = [rows[:-1], rows + [rows[0]]]
        for field, value in [('home', 'x'), ('date', '2023-09-01'), ('leg', 1), ('tie_format', 'single')]:
            changed = copy.deepcopy(rows)
            changed[1][field] = value
            cases.append(changed)
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                qf.pair_history(rows, ['2023'])

    def test_rules_reject_away_goals_missing_excerpt_and_unofficial_host(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c, p = self.cert(root)
            qf.certificate(p)
            for change in ('away-goals', 'excerpt', 'host', 'unreviewed'):
                bad = copy.deepcopy(c)
                if change == 'away-goals':
                    bad['editions']['2023']['rules']['away_goals_rule'] = True
                elif change == 'excerpt':
                    bad['editions']['2023']['support']['regulation_minutes'][0]['excerpt'] = 'absent'
                elif change == 'host':
                    bad['editions']['2023']['support']['regulation_minutes'][0]['source_url'] = 'https://example.org/'
                else:
                    bad['reviewed'] = False
                p.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    qf.certificate(p)

    def test_commented_old_rules_are_not_visible_evidence(self):
        self.assertNotIn('awaygoals', qf.text('<p>Current</p><!-- old away goals -->'))
        self.assertEqual(qf.text('<script>old rules</script><p>90 分</p>'), '90分')

    def test_target_checks_actual_event_team_leg_date_and_time(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c, _ = self.cert(root)
            f = self.fixture(root)
            qf.target_identity(f, c)
            for patch in ({'competition': 'jpn.1'}, {'edition': '2025'}, {'stage': 'semifinals'},
                          {'leg': 2}, {'gender': 'women'}, {'home_official_code': 'b'},
                          {'match_id': 'jleague:111102'}, {'home_goals': 1}, {'home_90': 1}, {'neutral': False},
                          {'date': '2000-01-01'}, {'kickoff_at': '2020-01-01T00:00:00Z'}):
                with self.subTest(patch=patch), self.assertRaises(ValueError):
                    qf.target_identity({**f, **patch}, c)

    def test_dependency_tamper_and_leg_mismatch_refuse_prediction(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c, cert = self.cert(root)
            f = self.fixture(root)
            a = {'schema': qf.SCHEMA, 'version': 'SYNTHETIC-ONLY', 'role': 'test', 'leg': 1,
                 'core': {'coefficients': [0., 0., 0.], 'teams': [], 'fit_end': '2025-01-01'},
                 'built_at': '2025-02-01T00:00:00Z',
                 'dependencies': {'certificate': {'path': str(cert), 'sha256': evidence.sha(cert)}}}
            path = root / 'artifact.json'
            path.write_text(json.dumps(a))
            result = qf.predict(path, f)
            self.assertFalse(result['formal_acceptance_passed'])
            self.assertEqual(len(result['unseen_teams_using_fitted_prior']), 2)
            a['leg'] = 2
            path.write_text(json.dumps(a))
            with self.assertRaises(ValueError):
                qf.predict(path, f)
            cert.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'dependency'):
                qf.predict(path, f)


    def test_holdout_labels_never_change_evaluation_fit(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dep = root / 'dependency.json'
            dep.write_text('{}')
            cert = {'historical_editions': ['2023', '2024', '2025'], 'target_edition': '2026/27'}
            plan = {'minimum_history_per_leg': 12, 'minimum_fit_per_leg': 8}
            rows = qf.pair_history(self.history(), cert['historical_editions'])
            changed = copy.deepcopy(rows)
            for r in changed:
                if r['season'] == '2025':
                    r['home_goals'] += 3
            reports = []
            for name, values in [('original', rows), ('changed', changed)]:
                with patch.object(qf, 'prepare', return_value=(values, cert, plan)):
                    index = qf.train(dep, dep, dep, root / name)
                report = qf.read(index['routes']['1']['report_path'])
                self.assertEqual(report['metrics']['current']['n'], 4)
                fold = report['folds'][0]
                self.assertFalse(set(fold['fit_ids']) & set(fold['target_ids']))
                self.assertEqual(fold['target_edition'], '2025')
                reports.append(report)
            self.assertEqual(reports[0]['folds'][0]['cores'], reports[1]['folds'][0]['cores'])
            self.assertNotEqual(reports[0]['metrics'], reports[1]['metrics'])

    def test_second_leg_cannot_predict_before_first_result_is_verified(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, cert = self.cert(root)
            f = self.fixture(root)
            f['leg'] = 2
            raw = Path(f['phase_evidence']['raw_path'])
            raw.write_text(raw.read_text().replace('第1戦', '第2戦'))
            f['phase_evidence']['raw_sha256'] = evidence.sha(raw)
            a = {'schema': qf.SCHEMA, 'version': 'SYNTHETIC', 'role': 'current', 'leg': 2,
                 'core': {'coefficients': [0., 0., 0.], 'teams': [], 'fit_end': '2025-01-01'},
                 'built_at': '2025-02-01T00:00:00Z',
                 'dependencies': {'certificate': {'path': str(cert), 'sha256': evidence.sha(cert)}}}
            path = root / 'artifact.json'
            path.write_text(json.dumps(a))
            with self.assertRaisesRegex(ValueError, 'SECOND_LEG_REQUIRES'):
                qf.predict(path, f)


    def test_candidate_freeze_is_separate_and_refuses_output_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, cert = self.cert(root)
            f = self.fixture(root)
            artifacts = {}
            for role in ('current', 'test'):
                a = {'schema': qf.SCHEMA, 'version': 'SYNTHETIC', 'role': role, 'leg': 1,
                     'core': {'coefficients': [0., 0., 0.], 'teams': [], 'fit_end': '2025-01-01'},
                     'built_at': '2025-02-01T00:00:00Z',
                     'dependencies': {'certificate': {'path': str(cert), 'sha256': evidence.sha(cert)}}}
                path = root / (role + '.json')
                path.write_text(json.dumps(a))
                artifacts[role] = {'path': str(path), 'sha256': evidence.sha(path)}
            index, fixtures = root / 'index.json', root / 'fixtures.json'
            index.write_text(json.dumps({'routes': {'1': {'artifacts': artifacts}}}))
            fixtures.write_text(json.dumps([f]))
            result = qf.freeze_pairs(index, fixtures, root / 'out', root / 'freezes')
            self.assertEqual(len(result['receipts']), 1)
            record = qf.read(result['receipts'][0]['path'])
            self.assertEqual(record['track'], 'jleague-cup-quarterfinal-candidates')
            self.assertFalse(record['payload']['current']['formal_acceptance_passed'])
            before = list((root / 'freezes').rglob('[0-9]*.json'))
            with self.assertRaises(FileExistsError):
                qf.freeze_pairs(index, fixtures, root / 'out', root / 'freezes')
            self.assertEqual(before, list((root / 'freezes').rglob('[0-9]*.json')))


if __name__ == '__main__':
    unittest.main()
