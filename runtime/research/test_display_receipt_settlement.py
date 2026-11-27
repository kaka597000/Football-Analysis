"""Exercise the opt-in author writer, whole-message readback and main normalized evidence reader."""

from contextlib import chdir
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.analysis_freeze import digest, encoded, save, verify
from research.chat_display_readback import prepare, stage_message
from research.display_delivery import delivery_request, display_slot, save_authored_row
from research.display_review import review_batch, summarize


class DisplayReceiptSettlementTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.now = datetime(2025, 1, 1, 11, tzinfo=timezone.utc)
        self.review_at = self.now + timedelta(hours=7)
        self.sent = self.now + timedelta(minutes=2)
        self.clocks = []
        for module in ('research.analysis_freeze', 'research.display_delivery', 'research.chat_display_readback'):
            clock = self.enterContext(patch(module + '.datetime', wraps=datetime))
            clock.now.return_value = self.now
            self.clocks.append(clock)
        self.index = self.root / 'normalized-results.jsonl'
        self.fixtures, self.payloads, self.records = [], [], []
        self.scopes, self.starts = {}, {}
        for i, (league, stage, score) in enumerate([
                ('eng.1', 'regular', (2, 1)), ('uefa.europa', 'league-phase', (1, 0)),
                ('ger.1', 'regular', (0, 3))], 1):
            mid = f'espn:{i}'
            start = self.now + timedelta(hours=i)
            f = dict(match_id=mid, cohort=league, competition=league, stage=stage,
                     home=f'espn:{10*i}', away=f'espn:{10*i+1}', identity_verified=True,
                     score_basis='regulation_90', kickoff_at=start.isoformat(),
                     cutoff_at=(self.now - timedelta(hours=1)).isoformat())
            scores = ['2-1', '1-1', '1-0']
            p = dict(selection_scope='active_reference', primary=scores[0], alternates=scores[1:],
                     displayed_scores=scores, user_facing_row=dict(match_id=mid, primary=scores[0],
                     alternates=scores[1:], ordered_scores=scores, kickoff_bjt=start.isoformat()))
            self.fixtures.append(f)
            self.payloads.append(p)
            self.scopes[mid] = dict(cohort=league, competition=league, stages=[stage])
            self.starts[mid] = dict(match_id=mid, competition=league, home=f['home'], away=f['away'],
                                    actual_kickoff_at=(start + timedelta(seconds=15)).isoformat())
            self.records.append(self.record(self.issue(p, f)))
            self.result(f, score)
        self.stage(self.records)

    def issue(self, p, f, *, key='original', scope=None):
        return save_authored_row(p, f, scope=self.scopes[f['match_id']] if scope is None else scope,
                                 root=self.root / 'freezes', idempotency_key=key)

    @staticmethod
    def record(issued):
        return verify(json.loads(Path(issued['receipt']['path']).read_bytes()))

    def result(self, f, goals, *, status='STATUS_FULL_TIME'):
        event = f['match_id'].split(':')[1]
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{f['cohort']}/summary?event={event}"
        teams = [dict(homeAway=side, team={'id': f[side].split(':')[1]}, score=str(g))
                 for side, g in zip(('home', 'away'), goals)]
        comp = dict(date=f['kickoff_at'], status=dict(period=2, type=dict(name=status, completed=True)),
                    competitors=teams)
        raw = encoded(dict(header=dict(id=event, league=dict(slug=f['cohort']), competitions=[comp])))
        checksum = sha256(raw).hexdigest()
        path = self.root / (checksum + '.json')
        path.write_bytes(raw)
        captured = (datetime.fromisoformat(f['kickoff_at']) + timedelta(hours=2)).isoformat()
        meta = dict(path=path.name, source_url=url, captured_at=captured, sha256=checksum)
        row = dict(f, known_at=captured, home_goals=goals[0], away_goals=goals[1], source_url=url,
                   raw_reference=meta)
        with self.index.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(row) + '\n')
        return row

    def stage(self, records, msg_id='synthetic-message'):
        self.body = '\n\n'.join(['Synthetic test batch', *[delivery_request(r)['text'] for r in records]])
        self.plan = stage_message(records, self.body, room_id='room', author_id='owner',
                                  path=self.root / ('local-stage-' + msg_id + '-' + digest(self.body)))
        self.message = dict(room_id='room', partial=False, message=dict(
            channel='chatgpt', author='aeon', author_id='owner', message_id=msg_id,
            sent_at=self.sent.isoformat(), content={'text': self.body}))
        self.first = {}
        for record in records:
            slot = display_slot(record['match_id'])
            self.first[slot] = dict(status='DELIVERED', slot=slot, channel='chatgpt', room_id='room',
                                    author_id='owner', message_id=msg_id, delivered_at=self.sent.isoformat(),
                                    original_body_sha256=sha256(self.body.encode()).hexdigest())

    def grade(self, *, fixtures=None, records=None, **change):
        args = dict(state=self.root, at=self.review_at, staged=self.plan, room_id='room', author_id='owner',
                    message_id=self.message['message']['message_id'], read_message=lambda _: self.message,
                    lookup_original=self.first.get, lookup_actual_kickoff=lambda f: self.starts.get(f['match_id']),
                    lookup_approved_scope=lambda f: self.scopes.get(f['match_id']))
        return review_batch(self.fixtures if fixtures is None else fixtures,
                            self.records if records is None else records, **dict(args, **change))

    def test_author_to_certified_hits_and_legacy_list_contract(self):
        rows = self.grade()
        self.assertEqual([(r['top1_hit'], r['top3_hit']) for r in rows],
                         [(True, True), (False, True), (False, False)])
        self.assertEqual([r['actual_kickoff_at'] for r in rows], [s['actual_kickoff_at'] for s in self.starts.values()])
        s = summarize(rows)
        self.assertEqual(s['top1'], dict(issued_n=3, coverage=1, settled_n=3, hits=1, accuracy=1/3))
        self.assertEqual(s['top3'], dict(issued_n=3, coverage=1, settled_n=3, hits=2, accuracy=2/3))
        for r, p in zip(self.records, self.payloads):
            self.assertEqual(r['payload']['displayed_scores'], [p['primary'], *p['alternates']])
            self.assertEqual(r['payload']['user_facing_row'], p['user_facing_row'])
            self.assertNotIn('authored_display', p)

    def test_idempotent_freeze_and_restart_from_persisted_evidence(self):
        f, p = self.fixtures[0], self.payloads[0]
        one = self.issue(p, f)
        path = Path(one['receipt']['path'])
        before = path.read_bytes()
        self.assertEqual(one, self.issue(p, f))
        revised = deepcopy(p)
        revised['user_facing_row']['note'] = 'changed original'
        with self.assertRaisesRegex(ValueError, 'idempotency conflict'):
            self.issue(revised, f)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.grade(), self.grade(staged=json.loads(encoded(self.plan)),
                                                  records=json.loads(encoded(self.records))))
        for clock in self.clocks:
            clock.now.return_value = datetime.fromisoformat(f['kickoff_at'])
        with self.assertRaisesRegex(ValueError, 'deadline'):
            self.issue(p, f)
        self.assertEqual(self.grade()[0]['message_id'], 'synthetic-message')
        self.assertEqual(path.read_bytes(), before)

    def test_missing_freeze_or_unsigned_historical_list_never_infers_delivery(self):
        f, p = self.fixtures[0], self.payloads[0]
        receipt = save(p, f, stage='prematch', track='comprehensive', root=self.root / 'old')
        path = Path(receipt['path'])
        before = path.read_bytes()
        old = json.loads(before)
        for records in ([], [old]):
            rows = self.grade(records=records, staged=None, read_message=lambda _: self.fail('unexpected read'))
            s = summarize(rows)
            self.assertEqual((s['registered_n'], s['verified_completed_n'], s['missing_display_n']), (3, 3, 3))
            self.assertIsNone(s['top3']['accuracy'])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(summarize(self.grade(records=self.records[1:]))['top1']['issued_n'], 0)

    def test_partial_lost_or_unissued_delivery_never_scores(self):
        for changes in (dict(staged=None), dict(message_id=None), dict(read_message=None),
                        dict(read_message=lambda _: None), dict(read_message=lambda _: dict(self.message, partial=True)),
                        dict(lookup_original=None), dict(lookup_original=lambda _: None)):
            with self.subTest(changes=list(changes)):
                self.assertEqual(summarize(self.grade(**changes))['top1']['issued_n'], 0)
        # An existing first-delivery entry cannot regenerate a lost visible body after kickoff.
        self.assertEqual(len(self.first), 3)
        self.assertTrue(all(r['top3_hit'] is None for r in self.grade(read_message=lambda _: None)))

    def test_wrong_author_room_message_or_visible_text_refused(self):
        for changes in (dict(room_id='different'), dict(author_id='different'), dict(message_id='different')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.grade(**changes)
        for changes in (dict(channel='email'), dict(author_id='other'), dict(deleted_at=self.sent.isoformat()),
                        dict(content={'text': self.body.replace('备选一：1-1', '备选一：1-0')}),
                        dict(content={'text': self.body + ' changed after delivery'})):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.grade(read_message=lambda _: dict(self.message, message=dict(self.message['message'], **changes)))

    def test_first_service_receipt_is_independent_and_conflicts_fail_closed(self):
        for key, wrong in [('original_body_sha256', 'a'*64), ('slot', 'other-fixture'), ('message_id', 'revision'),
                           ('room_id', 'other-room'), ('track', 'current-test-pair'), ('revision', 99),
                           ('freeze_sha256', 'b'*64), ('freeze_id', 'different'), ('match_id', 'espn:999'),
                           ('ordered_scores', ['1-1', '2-1', '1-0']), ('first_visible_verified', False),
                           ('text', 'edited'), ('request_sha256', 'c'*64), ('status', 'QUEUED'),
                           ('delivered_at', self.now.isoformat())]:
            with self.subTest(field=key), self.assertRaises(ValueError):
                self.grade(lookup_original=lambda slot: dict(self.first[slot], **{key: wrong}))

    def test_actual_and_planned_kickoff_boundaries_are_per_fixture(self):
        for start in (self.sent, self.sent-timedelta(seconds=1)):
            r = self.grade(lookup_actual_kickoff=lambda f: dict(self.starts[f['match_id']], actual_kickoff_at=start.isoformat()))[0]
            self.assertEqual(r['display_issue'], 'DELIVERED_AT_OR_AFTER_KICKOFF')
        future = (self.review_at + timedelta(hours=1)).isoformat()
        for lookup in (None, lambda _: None, lambda f: dict(self.starts[f['match_id']], actual_kickoff_at=future)):
            self.assertEqual(self.grade(lookup_actual_kickoff=lookup)[0]['display_issue'], 'UNVERIFIED_ACTUAL_KICKOFF')
        scheduled = self.fixtures[0]['kickoff_at']
        self.message['message']['sent_at'] = scheduled
        for original in self.first.values():
            original['delivered_at'] = scheduled
        rows = self.grade()
        self.assertEqual(rows[0]['display_issue'], 'DELIVERED_AT_OR_AFTER_PLANNED_KICKOFF')
        self.assertTrue(rows[1]['top3_hit'])

    def test_complete_kickoff_minute_must_follow_delivery(self):
        def lookup(starts):
            def actual(fixture):
                evidence = dict(self.starts[fixture['match_id']])
                evidence.pop('actual_kickoff_at')
                evidence['actual_kickoff_interval'] = dict(start_inclusive=starts.isoformat(),
                                                           end_exclusive=(starts+timedelta(minutes=1)).isoformat())
                return evidence
            return actual

        safe = self.sent + timedelta(minutes=1)
        row = self.grade(lookup_actual_kickoff=lookup(safe))[0]
        self.assertTrue(row['top1_hit'])
        self.assertNotIn('actual_kickoff_at', row)
        self.assertEqual(row['actual_kickoff_interval']['start_inclusive'], safe.isoformat())
        inside = self.grade(lookup_actual_kickoff=lookup(self.sent))[0]
        self.assertEqual(inside['display_issue'], 'DELIVERY_INSIDE_KICKOFF_MINUTE_UNPROVEN')
        self.assertFalse(inside['top1_issued'])
        after = self.grade(lookup_actual_kickoff=lookup(self.sent - timedelta(minutes=1)))[0]
        self.assertEqual(after['display_issue'], 'DELIVERED_AT_OR_AFTER_KICKOFF')
        with self.assertRaisesRegex(ValueError, 'before actual regulation'):
            self.grade(lookup_actual_kickoff=lookup(self.now+timedelta(hours=1, minutes=30)))

    def test_wrong_stage_identity_registration_track_or_revision_refused(self):
        f = self.fixtures[0]
        for changes in (dict(cohort='ger.1'), dict(stage='playoffs'), dict(competition='ita.1'),
                        dict(home='espn:9'), dict(provider_stage='FINAL'), dict(score_basis='including_extra_time'),
                        dict(cutoff_at=self.sent.isoformat()), dict(identity_verified=False)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.grade(fixtures=[dict(f, **changes), *self.fixtures[1:]])
        for change in (dict(track='current-test-pair'), dict(stage='review'), dict(match_id='espn:99'),
                       dict(revision=8)):
            r = dict(self.records[0], **change)
            r['record_sha256'] = digest({k:v for k,v in r.items() if k != 'record_sha256'})
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.grade(records=[r, *self.records[1:]])
        for key in ('match_id', 'competition', 'home', 'away'):
            with self.subTest(reported_start=key), self.assertRaisesRegex(ValueError, 'actual kickoff report'):
                self.grade(lookup_actual_kickoff=lambda f: dict(self.starts[f['match_id']], **{key:'unrelated'}))
        with self.assertRaises(ValueError):
            self.grade(fixtures=[f, f])

    def test_literal_original_issue_scope_only(self):
        f, p = self.fixtures[0], self.payloads[0]
        for stage in ('regular-season', 'playoffs'):
            with self.assertRaisesRegex(ValueError, 'approved'):
                self.issue(p, dict(f, stage=stage))
        ucl = 'uefa.champions'
        scoped = dict(f, competition=ucl, cohort=ucl+':NEW_FORMAT:league_phase', stage='league_phase')
        with self.assertRaisesRegex(ValueError, 'approved'):
            self.issue(p, scoped, scope=dict(cohort=scoped['cohort'], competition=ucl, stages=['NEW_FORMAT:league_phase']))
        self.assertEqual(self.grade(lookup_approved_scope=lambda _: None)[0]['display_issue'], 'UNVERIFIED_ISSUE_SCOPE')
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.grade(lookup_approved_scope=lambda f: dict(self.scopes[f['match_id']], stages=[f['stage'], 'new']))

    def test_invalid_or_misaligned_three_scores_and_observation_rows_rejected(self):
        f, p = self.fixtures[0], self.payloads[0]
        for scores in (['1-0','1-0','2-1'], ['1-0','1-1'], ['01-0','1-1','2-1']):
            q = dict(p, primary=scores[0], alternates=scores[1:], displayed_scores=scores,
                     user_facing_row=dict(p['user_facing_row'], primary=scores[0], alternates=scores[1:], ordered_scores=scores))
            with self.subTest(scores=scores), self.assertRaises(ValueError):
                self.issue(q, f)
        for q in (dict(p, selection_scope='observation'), dict(p, primary='1-1'),
                  dict(p, displayed_scores=list(reversed(p['displayed_scores']))),
                  dict(p, user_facing_row=dict(p['user_facing_row'], kickoff_bjt=self.now.isoformat()))):
            with self.assertRaises(ValueError):
                self.issue(q, f)

    def test_duplicate_missing_or_reordered_body_blocks_refused(self):
        blocks = [delivery_request(r)['text'] for r in self.records]
        for body in (self.body + '\n' + blocks[0], '\n'.join(blocks[1:]), '\n'.join(reversed(blocks))):
            with self.assertRaisesRegex(ValueError, 'batch order'):
                prepare(self.records, body, room_id='room', author_id='owner', at=self.now)
        with self.assertRaisesRegex(ValueError, 'planned kickoff'):
            prepare(self.records, self.body, room_id='room', author_id='owner',
                    at=datetime.fromisoformat(self.fixtures[0]['kickoff_at']))

    def test_visible_abstention_owns_slot_hidden_draft_does_not(self):
        f, p = self.fixtures[0], self.payloads[0]
        q = dict(p, primary=None, alternates=[], displayed_scores=[], display_abstain=True,
                 user_facing_row=dict(p['user_facing_row'], primary=None, alternates=[], ordered_scores=[]))
        abstain = self.record(self.issue(q, f, key='abstention'))
        # A private draft revision does not displace the service's actual first.
        self.assertTrue(self.grade()[0]['top1_hit'])
        self.stage([abstain], msg_id='genuine-test-abstention')
        original = self.first.copy()
        rows = self.grade(fixtures=[f], records=[abstain])
        self.assertEqual((rows[0]['display_status'], summarize(rows)['top3']['settled_n']), ('ABSTAIN', 0))
        self.stage(self.records, msg_id='later-scores')
        with self.assertRaisesRegex(ValueError, 'first channel delivery'):
            self.grade(lookup_original=original.get)
        self.stage([abstain])
        with self.assertRaises(ValueError):
            self.issue(dict(q, display_abstain=False), f)

    def test_conflicting_normalized_labels_and_missing_raw_remain_pending(self):
        self.result(self.fixtures[0], (0, 0))
        first_row = json.loads(self.index.read_text().splitlines()[1])
        (self.root / first_row['raw_reference']['path']).unlink()
        rows = self.grade()
        self.assertIn('conflicting certified regulation scores', rows[0]['result_issues'])
        self.assertEqual((rows[0]['top1_hit'], rows[1]['top3_hit'], rows[1]['top3_issued']), (None, None, True))
        self.assertEqual((summarize(rows)['pending_n'], summarize(rows)['top3']['settled_n']), (2, 1))

    def test_raw_alone_bad_hash_non_regulation_and_outside_root_are_ineligible(self):
        original = [json.loads(line) for line in self.index.read_text().splitlines()]
        self.index.unlink()
        self.assertEqual(summarize(self.grade())['verified_completed_n'], 0)
        self.result(self.fixtures[0], (2, 1), status='STATUS_FINAL_PEN')
        for row, update in zip(original[1:], ({'sha256':'0'*64}, {'path':'../outside.json'})):
            row['raw_reference'].update(update)
            with self.index.open('a') as handle:
                handle.write(json.dumps(row)+'\n')
        rows = self.grade()
        self.assertEqual([r['top1_hit'] for r in rows], [None]*3)
        self.assertTrue(all(r['result_issues'] for r in rows))
        self.assertEqual(summarize(rows)['top3']['issued_n'], 3)

    def test_unpublished_signed_row_and_storage_order_do_not_block_displayed_neighbors(self):
        self.stage([self.records[2], self.records[0]])
        rows = self.grade(records=list(reversed(self.records)))
        self.assertEqual([r['display_status'] for r in rows], ['ISSUED', 'MISSING', 'ISSUED'])
        self.assertEqual(summarize(rows)['top1']['settled_n'], 2)

    def test_hidden_first_freeze_does_not_displace_later_first_visible_body(self):
        f, p = self.fixtures[0], self.payloads[0]
        q = dict(p, primary=None, alternates=[], displayed_scores=[], display_abstain=True,
                 user_facing_row=dict(p['user_facing_row'], primary=None, alternates=[], ordered_scores=[]))
        folder = self.root / 'another-batch'
        hidden = save_authored_row(q, f, scope=self.scopes[f['match_id']], root=folder, idempotency_key='draft')
        issued = save_authored_row(p, f, scope=self.scopes[f['match_id']], root=folder, idempotency_key='issued')
        original, visible = self.record(hidden), self.record(issued)
        self.assertEqual((original['revision'], visible['revision']), (1, 2))
        self.stage([visible])
        for entries in ([visible], [original, visible], [visible, original, visible]):
            self.assertTrue(self.grade(fixtures=[f], records=entries)[0]['top1_hit'])
        self.assertIsNone(self.grade(fixtures=[f], records=[original])[0]['top1_hit'])
        corrupt = dict(visible, revision=99)
        corrupt['record_sha256'] = digest({k:v for k,v in corrupt.items() if k != 'record_sha256'})
        with self.assertRaisesRegex(ValueError, 'conflicting content'):
            self.grade(fixtures=[f], records=[visible, corrupt])

    def test_relative_normalized_state_matches_absolute_evidence(self):
        expected = self.grade()
        with chdir(self.root.parent):
            self.assertEqual(self.grade(state=self.root.name), expected)

    def test_local_staging_recovers_only_existing_bytes_after_kickoff(self):
        path = self.root / 'persisted-message'
        args = dict(room_id='room', author_id='owner', path=path)
        original = stage_message(self.records, self.body, **args)
        before = path.read_bytes()
        self.clocks[-1].now.return_value = self.review_at
        self.assertEqual(stage_message(self.records, self.body, **args), original)
        with self.assertRaisesRegex(ValueError, 'existing staged'):
            stage_message(self.records, self.body + '\nchanged', **args)
        self.assertEqual(path.read_bytes(), before)
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'before its planned kickoff'):
            stage_message(self.records, self.body, **args)
        self.assertFalse(path.exists())

    def test_staging_cannot_cross_kickoff_while_committing(self):
        path = self.root / 'slow-write'
        deadline = datetime.fromisoformat(self.fixtures[0]['kickoff_at'])
        self.clocks[-1].now.side_effect = [self.now, deadline]
        with self.assertRaisesRegex(ValueError, 'deadline passed before local commit'):
            stage_message(self.records, self.body, room_id='room', author_id='owner', path=path)
        self.assertFalse(path.exists())
