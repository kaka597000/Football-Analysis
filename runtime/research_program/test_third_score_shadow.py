"""Exercise the command with the normal pair and author writers and a local result archive."""

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from publication import publish
from research.analysis_freeze import digest, encoded, save
from research.display_delivery import save_authored_row
from research_program import third_score_shadow as shadow


TEST = [[.02, .26, .02], [.31, .23, .02], [.04, .08, .02]]
NO_LOW_IN_TOP3 = [[.03, .11, .02], [.10, .17, .03], [.18, .30, .06]]
ORIGINAL = ['2-1', '1-1', '2-0']


class ThirdScoreShadowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.study = self.root / 'study'
        self.normal = self.root / 'original-freezes'
        self.authors = self.root / 'authored'
        self.authors.mkdir()
        self.roster = self.root / 'roster.json'
        self.starts = self.root / 'starts.json'
        self.at = datetime(2026, 11, 25, 3, tzinfo=timezone.utc)
        self.start = self.at + timedelta(hours=6)
        self.clocks = []
        self.enterContext(patch.object(shadow, '_now', side_effect=lambda: self.at))
        for module in ('research.analysis_freeze', 'research.display_delivery'):
            self.clocks.append(self.enterContext(patch(module + '.datetime', wraps=datetime)))
        self.move(self.at)
        self.r7 = self.root / 'r7.tar.gz'
        r7_issue = dict(match_id='espn:9', fixture=self.fixture(9))
        r7_issue['record_sha256'] = digest(r7_issue)
        with tarfile.open(self.r7, 'w:gz') as archive:
            ref = dict(file='fixture.json', sha256=r7_issue['record_sha256'])
            for name, value in [('receipts.json', dict(matches={'espn:9': ref})), ('fixture.json', r7_issue)]:
                data = encoded(value)
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, BytesIO(data))
        protocol = json.loads(shadow.PROTOCOL.read_bytes())
        protocol['r7'].update(archive_sha256=sha256(self.r7.read_bytes()).hexdigest(), fixture_count=1)
        spec = self.root / 'protocol.json'
        spec.write_bytes(encoded(protocol))
        self.enterContext(patch.object(shadow, 'PROTOCOL', spec))
        self.cmd('register', '--r7-archive', self.r7)
        self.move(self.at + timedelta(minutes=15))

    def move(self, when):
        self.at = when
        for clock in self.clocks:
            clock.now.return_value = when

    def cmd(self, *args):
        output = StringIO()
        with redirect_stdout(output):
            shadow.main(['--state', str(self.study), *map(str, args)])
        return json.loads(output.getvalue())

    def fixture(self, i, league='eng.1', stage='regular', start=None):
        return dict(match_id=f'espn:{i}', competition=league, cohort=league, stage=stage,
                    home=f'espn:{10*i}', away=f'espn:{10*i+1}', score_basis='regulation_90',
                    identity_verified=True, stage_verified=True, neutral=False,
                    kickoff_at=(start or self.start).isoformat(), cutoff_at=self.at.isoformat())

    def authored(self, f, scores=ORIGINAL, matrix=TEST, *, has_pair=True, missing_matrix=False):
        event = f['match_id'].split(':')[1]
        ref = None
        if has_pair:
            current = dict(matrix=TEST, primary='1-0', matrix_hash=digest(TEST), version='current-test-fixture')
            test = dict(matrix=None if missing_matrix else matrix, primary='1-0',
                        matrix_hash=digest(matrix), version='test-test-fixture')
            pair = publish(dict(match_id=f['match_id'], current_model=current, test_model=test),
                           f, out=self.root / ('pair-' + event + '.json'), freeze_root=self.normal)
            ref = pair['freeze_receipt']
        self.move(self.at + timedelta(seconds=1))
        f = dict(f, cutoff_at=self.at.isoformat())
        first, alternatives = (scores[0], scores[1:]) if scores else (None, [])
        row = dict(match_id=f['match_id'], primary=first, alternates=alternatives, ordered_scores=scores)
        payload = dict(selection_scope='active_reference', primary=first, alternates=alternatives,
                       displayed_scores=scores, user_facing_row=row, current_test_reference=ref)
        if not scores:
            payload['display_abstain'] = True
        scope = dict(competition=f['competition'], cohort=f['cohort'], stages=[f['stage']])
        output = save_authored_row(payload, f, scope=scope, idempotency_key='original', root=self.normal)
        (self.authors / (event + '.json')).write_bytes(encoded(output))
        return output

    def lock(self, *fixtures, batch='normal-batch'):
        self.roster.write_bytes(encoded(dict(batch_id=batch, fixtures=list(fixtures))))
        return self.cmd('lock', '--roster', self.roster, '--authored-dir', self.authors)

    def lock_rows(self):
        receipt, = (self.study / 'batches').glob('*/000001.json')
        return json.loads(receipt.read_bytes())['payload']['rows']

    def labels(self, fixtures, goals, *, statuses=None, first_actual_start=None):
        starts = {}
        for i, (f, (h, a)) in enumerate(zip(fixtures, goals, strict=True)):
            kickoff = shadow.aware(f['kickoff_at'])
            actual_start = first_actual_start if i == 0 and first_actual_start else kickoff + timedelta(seconds=10)
            stamp = (kickoff + timedelta(hours=2)).isoformat()
            event = f['match_id'].split(':')[1]
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{f['competition']}/summary?event={event}"
            competitors = [dict(homeAway=side, team=dict(id=f[side].split(':')[1]), score=str(g))
                           for side, g in (('home', h), ('away', a))]
            status = 'STATUS_FULL_TIME' if not statuses else statuses[i]
            comp = dict(date=f['kickoff_at'], competitors=competitors,
                        status=dict(type=dict(completed=True, name=status), period=2))
            body = encoded(dict(header=dict(id=event, league=dict(slug=f['competition']), competitions=[comp])))
            dest = self.root / ('result-' + event + '.json')
            dest.write_bytes(body)
            ref = dict(path=dest.name, sha256=sha256(body).hexdigest(), source_url=url, captured_at=stamp)
            record = dict(f, home_goals=h, away_goals=a, known_at=stamp, source_url=url, raw_reference=ref)
            with (self.root / 'normalized-results.jsonl').open('ab') as sink:
                sink.write(encoded(record) + b'\n')
            starts[f['match_id']] = dict({key: f[key] for key in ('match_id', 'competition', 'home', 'away')},
                                         actual_kickoff_at=actual_start.isoformat())
        self.starts.write_bytes(encoded(starts))
        self.move(self.start + timedelta(hours=5))
        return self.cmd('review', '--results-state', self.root, '--kickoffs', self.starts)

    def test_cli_locks_normal_handoffs_and_counts_gains_losses_and_unchanged_cases(self):
        fixtures = [self.fixture(1), self.fixture(2, 'uefa.europa', 'league-phase'),
                    self.fixture(3, 'ger.1'), self.fixture(4)]
        for f, scores, matrix in zip(fixtures, [ORIGINAL, ORIGINAL, ORIGINAL, ['1-0', '1-1', '2-0']],
                                     [TEST, TEST, NO_LOW_IN_TOP3, TEST], strict=True):
            self.authored(f, scores, matrix)
        source = {p: p.read_bytes() for p in self.normal.rglob('*.json')}
        captured = self.lock(*fixtures)
        rows = self.lock_rows()
        self.assertEqual(captured['locked_n'], 4)
        self.assertEqual(captured['applicable_of_locked'], 3/4)
        self.assertEqual([r['candidate'][2] for r in rows], ['1-0', '1-0', '2-0', '0-1'])
        self.assertTrue(all(r['baseline'][:2] == r['candidate'][:2] for r in rows))
        self.assertEqual(self.cmd('review')['paired_settled_n'], 0)
        graded = self.labels(fixtures, [(1, 0), (2, 0), (2, 1), (1, 0)])
        self.assertEqual((graded['paired_settled_n'], graded['gained'], graded['lost'], graded['net']), (4, 1, 1, 0))
        self.assertEqual(graded['baseline_top3'], {'hits': 3, 'rate': .75})
        self.assertEqual(graded['candidate_top3'], {'hits': 3, 'rate': .75})
        self.assertEqual(self.lock(*fixtures)['batch_record_sha256'], captured['batch_record_sha256'])
        self.assertEqual(source, {p: p.read_bytes() for p in self.normal.rglob('*.json')})

    def test_final_batch_keeps_missing_abstained_and_unavailable_rows(self):
        fixtures = [self.fixture(i) for i in range(1, 6)]
        self.authored(fixtures[1], [])
        self.authored(fixtures[2], has_pair=False)
        self.authored(fixtures[3], missing_matrix=True)
        self.authored(fixtures[4], [])
        wrapper = self.authors / '5.json'
        signed = json.loads(wrapper.read_bytes())
        signed['receipt']['sha256'] = '0' * 64
        wrapper.write_bytes(encoded(signed))
        output = self.lock(*fixtures)
        self.assertEqual(output['locked_n'], 0)
        self.assertEqual(self.cmd('review')['registered_n'], 5)
        self.assertEqual(output['statuses'], dict(AUTHOR_MISSING=1, AUTHOR_ABSTAIN=1, PAIR_MISSING=1,
                                                  PAIR_UNAVAILABLE=1, EVIDENCE_CONFLICT=1))
        self.assertIsNone(output['candidate_top3']['rate'])

    def test_r7_revealed_and_late_events_are_excluded_before_reading_author_paths(self):
        self.move(self.start)
        alias = dict(self.fixture(9), match_id='espn:10')
        output = self.lock(self.fixture(1), self.fixture(9), alias, self.fixture(3, start=self.at - timedelta(days=1)))
        self.assertEqual(output['statuses'], dict(ALREADY_STARTED=1, R7=2, OUTSIDE_WINDOW=1))
        self.assertIsNone(output['batch_record_sha256'])
        self.assertEqual(list((self.study / 'batches').glob('*/000001.json')), [])

    def test_writes_once_per_fixture_and_rejects_changed_roster(self):
        f = self.fixture(1)
        self.authored(f)
        first = self.lock(f)
        other = self.lock(f, batch='another-batch')
        self.assertEqual(other['statuses'], {'ALREADY_REGISTERED': 1})
        self.assertIsNone(other['batch_record_sha256'])
        with self.assertRaisesRegex(ValueError, 'differs from the original roster'):
            self.lock(dict(f, home='espn:999'))
        self.assertEqual(self.lock(f)['batch_record_sha256'], first['batch_record_sha256'])

    def test_candidate_cannot_settle_after_true_start_or_from_penalty_result(self):
        first, second = self.fixture(1), self.fixture(2)
        for f in (first, second):
            self.authored(f)
        self.lock(first, second)
        score = self.labels([first, second], [(1, 0), (1, 0)],
                            statuses=['STATUS_FULL_TIME', 'STATUS_FINAL_PEN'],
                            first_actual_start=self.at)
        self.assertEqual(score['paired_settled_n'], 0)
        self.assertEqual(score['settlement_statuses'],
                         dict(LOCK_AT_OR_AFTER_ACTUAL_START=1, REGULATION_RESULT_PENDING=1))
        self.assertEqual(score['result_evidence_issues_n'], 1)

    def test_wrong_pair_identity_and_unbound_revision_are_rejected(self):
        f, g = self.fixture(1), self.fixture(2)
        ref = self.authored(f)
        path = Path(ref['receipt']['path'])
        original = json.loads(path.read_bytes())
        pair_ref = original['payload']['current_test_reference']
        other = save(json.loads(Path(pair_ref['path']).read_bytes())['payload'], g, stage='prematch',
                     track='current-test-pair', root=self.normal)
        # Test a checked-in author envelope referencing a different fixture.
        forged = dict(original['payload'], current_test_reference=other)
        with self.assertRaisesRegex(ValueError, 'identity'):
            save(forged, original['fixture'], stage='prematch', track='comprehensive', root=self.normal)
        rev = save_authored_row(dict(original['payload'], extra_note='revision'), original['fixture'],
                                scope=original['payload']['authored_stage_scope'], idempotency_key='later',
                                root=self.normal)
        (self.authors / '1.json').write_bytes(encoded(rev))
        out = self.lock(f)
        self.assertEqual(out['statuses'], dict(EVIDENCE_CONFLICT=1))

    def test_finalized_batch_can_bind_a_later_authored_freeze_after_a_hidden_draft(self):
        fixture = self.fixture(1)
        author = self.authored(fixture)
        final = save_authored_row(dict(author['payload'], extra_note='final signed author row'), author['fixture'],
                                  scope=author['payload']['authored_stage_scope'], idempotency_key='final',
                                  root=self.normal)
        (self.authors / '1.json').write_bytes(encoded(final))
        self.lock(dict(fixture, author_freeze_sha256=final['receipt']['sha256']))
        self.assertEqual(self.lock_rows()[0]['author_freeze_sha256'], final['receipt']['sha256'])

    def test_pinned_r7_archive_and_registration_boundary(self):
        tampered = self.root / 'different.tar.gz'
        tampered.write_bytes(b'different')
        with self.assertRaisesRegex(ValueError, 'R7 archive differs'):
            self.cmd('register', '--r7-archive', tampered)
        self.study = self.root / 'late-study'
        self.move(self.start)
        with self.assertRaisesRegex(ValueError, 'late prematch freeze'):
            self.cmd('register', '--r7-archive', self.r7)


if __name__ == '__main__':
    unittest.main()
