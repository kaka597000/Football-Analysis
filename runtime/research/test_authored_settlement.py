"""Exercise the normal writer, whole-message readback and shared scorer offline."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.analysis_freeze import digest, save, save_authored_row, verify
from research.chat_display_readback import first_delivery_lookup, match_readback, prepare
from research.display_delivery import approved_scope_for, attach_display, delivery_request, display_slot
from research.display_review import settle, summarize


class AuthoredSettlementTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.now = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)
        clock = self.enterContext(patch('research.analysis_freeze.datetime', wraps=datetime))
        clock.now.return_value = self.now
        self.sent = self.now + timedelta(minutes=3)
        self.review_at = self.now + timedelta(hours=6)
        self.records, self.starts, self.results, self.approvals = [], {}, {}, {}
        fixtures = [('eng.1', 'regular', 'PL', 'REGULAR_SEASON', (2, 1)),
                    ('uefa.europa', 'league-phase', 'EL', 'LEAGUE_STAGE', (1, 1)),
                    ('ger.1', 'regular', 'BL1', 'REGULAR_SEASON', (0, 3))]
        for i, (competition, stage, code, source_stage, goals) in enumerate(fixtures, 1):
            match_id = f'fdorg:{i}'
            scheduled = self.now + timedelta(hours=i)
            f = dict(match_id=match_id, home=f'fdorg:{i*10}', away=f'fdorg:{i*10+1}',
                     competition=competition, cohort=competition, stage=stage, identity_verified=True,
                     score_basis='regulation_90', provider_competition=code, provider_stage=source_stage,
                     cutoff_at=(self.now - timedelta(minutes=2)).isoformat(), kickoff_at=scheduled.isoformat())
            # The real evening author does not always repeat ordered_scores or stage_verified.
            row = dict(match_id=match_id, primary='2-1', alternates=['1-1', '1-0'])
            payload = dict(primary='2-1', alternates=row['alternates'], user_facing_row=row,
                           selection_scope='active_reference', displayed_scores=['2-1', '1-1', '1-0'])
            scope = dict(competition=competition, cohort=competition, stages=[stage])
            self.approvals[competition] = deepcopy(scope)
            issued = save_authored_row(payload, f, scope=scope, root=self.root / 'freezes',
                                       idempotency_key=f'synthetic-{i}')
            rec = verify(json.loads(Path(issued['receipt']['path']).read_bytes()))
            self.records.append(rec)
            self.starts[match_id] = (scheduled + timedelta(seconds=31)).isoformat()
            raw = dict(id=i, homeTeam={'id': i*10}, awayTeam={'id': i*10+1}, competition={'code': code},
                       stage=source_stage, utcDate=f['kickoff_at'], status='FINISHED',
                       score={'duration':'REGULAR', 'fullTime':{'home':goals[0], 'away':goals[1]}})
            path = self.root / f'{i}.json'
            path.write_text(json.dumps(raw), encoding='utf-8')
            self.results[match_id] = dict(path=path.name, sha256=sha256(path.read_bytes()).hexdigest(),
                                          source_url=f'https://api.football-data.org/v4/matches/{i}',
                                          captured_at=(scheduled + timedelta(hours=2)).isoformat())
        self.blocks = [delivery_request(r)['text'] for r in self.records]
        self.body = '\n\n'.join(['Synthetic normal batch', *self.blocks])
        self.staged = prepare(self.records, self.body, room_id='test-room', author_id='test-author', at=self.now)
        self.visible = {'room_id':'test-room', 'partial':False, 'message':{
            'message_id':'test-id', 'channel':'chatgpt', 'author':'aeon', 'author_id':'test-author',
            'sent_at':self.sent.isoformat(), 'content':{'text':self.body}}}
        self.originals = {}
        for r in self.records:
            slot = display_slot(r['match_id'])
            self.originals[slot] = dict(status='DELIVERED', slot=slot, channel='chatgpt', room_id='test-room',
                                        author_id='test-author', message_id='test-id', delivered_at=self.sent.isoformat(),
                                        original_body_sha256=sha256(self.body.encode()).hexdigest())

    def lookup(self, **changes):
        args = dict(message_id='test-id', read_message=lambda msg: self.visible if msg == 'test-id' else None,
                    lookup_original=self.originals.get, at=self.review_at)
        return first_delivery_lookup(self.records, self.staged, **dict(args, **changes))

    def grade(self, record, reader, *, fixture=None, **changes):
        f = record['fixture'] if fixture is None else fixture
        args = dict(lookup_first=reader, lookup_actual_kickoff=lambda frozen: self.starts.get(frozen['match_id']),
                    lookup_approved_scope=lambda frozen: approved_scope_for(list(self.approvals.values()), frozen),
                    at=self.review_at)
        return settle(record, f, 'comprehensive', 'football-data.org', self.results[f['match_id']],
                      self.root, **dict(args, **changes))

    def test_first_alternative_and_miss_from_one_actual_writer_and_shared_scorer(self):
        reader = self.lookup()
        rows = [self.grade(r, reader) for r in self.records]
        self.assertEqual([(r['top1_hit'], r['top3_hit']) for r in rows], [(True,True),(False,True),(False,False)])
        self.assertEqual([r['actual_kickoff_at'] for r in rows], list(self.starts.values()))
        self.assertEqual({r['original_body_sha256'] for r in rows}, {sha256(self.body.encode()).hexdigest()})
        self.assertEqual({r['message_id'] for r in rows}, {'test-id'})
        self.assertEqual(summarize(rows)['top3']['settled_n'], 3)
        self.assertEqual(rows, [self.grade(r, self.lookup()) for r in self.records])

    def test_unsigned_legacy_list_and_missing_freeze_keep_no_scores(self):
        rec = self.records[0]
        unsigned = {k:v for k,v in rec['payload'].items() if k not in ('authored_display','authored_stage_scope')}
        older = save(unsigned, rec['fixture'], stage='prematch', track='comprehensive', root=self.root / 'legacy')
        path = Path(older['path'])
        before = path.read_bytes()
        reader = lambda slot: self.fail('unsigned record reached channel lookup')
        self.assertEqual(self.grade(json.loads(before),reader)['display_status'], 'MISSING')
        self.assertEqual(path.read_bytes(), before)
        row = settle(None, rec['fixture'], 'comprehensive', lookup_first=reader)
        self.assertEqual((row['top1_hit'], row['top3_hit']), (None,None))

    def test_current_named_message_alone_or_lost_evidence_stays_pending(self):
        obs = match_readback(self.staged, self.visible, message_id='test-id', at=self.review_at)
        self.assertFalse(obs['first_visible_verified'])
        rec = self.records[0]
        for reader in (self.lookup(lookup_original=None), self.lookup(lookup_original=lambda _: None),
                       self.lookup(read_message=None),
                       self.lookup(read_message=lambda _: None),
                       self.lookup(read_message=lambda _: dict(self.visible, partial=True))):
            self.assertEqual(self.grade(rec,reader)['display_issue'], 'UNCONFIRMED_DELIVERY')
        with self.assertRaisesRegex(ValueError, 'first channel delivery'):
            self.grade(rec,self.lookup(lookup_original=lambda _: obs))
        row = self.grade(rec,self.lookup(),lookup_actual_kickoff=lambda _: None)
        self.assertEqual(row['display_issue'], 'UNVERIFIED_ACTUAL_KICKOFF')
        meta = self.results[rec['match_id']]
        (self.root / meta['path']).unlink()
        row = self.grade(rec,self.lookup())
        self.assertTrue(row['top1_issued'])
        self.assertEqual((row['result_issue'], row['top3_hit']), ('RETAINED_RESULT_EVIDENCE_MISSING',None))
        self.assertEqual(summarize([row])['pending_n'], 1)

    def test_conflicting_first_id_author_room_slot_hash_or_time_cannot_score(self):
        rec = self.records[0]
        first = self.originals[display_slot(rec['match_id'])]
        for field, value in (('status','QUEUED'),('message_id','first-other'),('author_id','other-author'),
                             ('room_id','other-room'),('channel','email'),('slot','other-fixture'),
                             ('original_body_sha256','0'*64),('delivered_at',self.now.isoformat())):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'first channel delivery'):
                self.grade(rec,self.lookup(lookup_original=lambda _: dict(first, **{field:value})))

    def test_conflicting_optional_service_assertions_are_preserved_and_rejected(self):
        rec = self.records[0]
        first = self.originals[display_slot(rec['match_id'])]
        for field, value in (('request_sha256','bad'), ('text','a different text'), ('freeze_sha256','bad'),
                             ('freeze_id','another freeze'), ('revision',10), ('match_id','other fixture'),
                             ('track','current-test-pair'), ('first_visible_verified',False),
                             ('original_text','old first version'), ('body_sha256','bad'),
                             ('ordered_scores',['2-1','1-0','1-1'])):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.grade(rec,self.lookup(lookup_original=lambda _: dict(first, **{field:value})))
        enriched = dict(first, request_sha256=digest(delivery_request(rec)), text=self.body,
                        original_text=self.body, freeze_id=rec['freeze_id'])
        self.assertTrue(self.grade(rec,self.lookup(lookup_original=lambda _: enriched))['top1_hit'])

    def test_single_block_receipt_cannot_qualify_an_unverified_authored_batch(self):
        rec = self.records[0]
        request = delivery_request(rec)
        thin = dict(status='DELIVERED', message_id='test-id', delivered_at=self.sent.isoformat(),
                    request_sha256=digest(request), text=request['text'])
        row = self.grade(rec,lambda _: thin)
        self.assertEqual(row['display_issue'],'UNVERIFIED_ORIGINAL_MESSAGE')
        self.assertIsNone(row['top1_hit'])

    def test_authored_stage_needs_the_original_batches_independent_approval(self):
        rec = self.records[1]
        for optional in (None, lambda _: None):
            row = self.grade(rec,self.lookup(),lookup_approved_scope=optional)
            self.assertEqual(row['display_issue'],'UNVERIFIED_AUTHORED_STAGE')
            self.assertFalse(row['top1_issued'])
        policy = self.approvals[rec['fixture']['cohort']]
        with self.assertRaisesRegex(ValueError,'approved cohort stages'):
            self.grade(rec,self.lookup(),lookup_approved_scope=lambda _: dict(policy, stages=['final']))
        row = self.grade(rec,self.lookup(),lookup_approved_scope=lambda _: dict(policy, name='extra metadata'))
        self.assertTrue(row['top3_hit'])

    def test_body_ranking_order_and_different_frozen_version_are_refused(self):
        for body in ('\n\n'.join(reversed(self.blocks)), self.body.replace('备选一：1-1','备选一：0-0',1),
                     self.body.replace(self.blocks[0], self.blocks[0].split('\n')[0]+'\n第一比分待定\n'+self.blocks[0])):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, 'batch order'):
                prepare(self.records,body,room_id='test-room',author_id='test-author',at=self.now)
        edited = deepcopy(self.visible)
        edited['message']['content']['text'] += '\nchanged'
        with self.assertRaisesRegex(ValueError,'visible body'):
            self.lookup(read_message=lambda _: edited)
        self.staged['plan']['matches'][0]['freeze_sha256'] = '0'*64
        self.staged['plan_sha256'] = digest(self.staged['plan'])
        with self.assertRaisesRegex(ValueError,'staged matches'):
            self.lookup()

    def test_first_message_cannot_settle_a_second_signed_revision_with_the_same_scores(self):
        original = self.records[0]
        payload = {k:v for k,v in original['payload'].items() if k not in ('authored_display','authored_stage_scope')}
        issued = save_authored_row(payload,original['fixture'],scope=self.approvals['eng.1'],
                                   root=self.root/'freezes',idempotency_key='later-revision')
        revision = verify(json.loads(Path(issued['receipt']['path']).read_bytes()))
        self.assertGreater(revision['revision'],original['revision'])
        with self.assertRaisesRegex(ValueError,'delivery does not match'):
            self.grade(revision,self.lookup())

    def test_wrong_external_identity_stage_cutoff_or_result_cannot_score(self):
        rec = self.records[0]
        for field, value in (('competition','ger.1'),('cohort','ger.1'),('stage','playoffs'),
                             ('provider_stage','FINAL'),('home','fdorg:99'),('identity_verified',False),
                             ('stage_verified',False),('score_basis','extra_time'),('cutoff_at',self.sent.isoformat())):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.grade(rec,self.lookup(),fixture=dict(rec['fixture'],**{field:value}))
        self.results[rec['match_id']]['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError,'checksum'):
            self.grade(rec,self.lookup())

    def test_scheduled_and_actual_kickoff_limits_apply_to_each_match_in_a_batch(self):
        early, later = self.records[:2]
        for actual in (self.sent,self.sent-timedelta(seconds=1)):
            row = self.grade(early,self.lookup(),lookup_actual_kickoff=lambda _: actual.isoformat())
            self.assertEqual(row['display_issue'], 'DELIVERED_AT_OR_AFTER_KICKOFF')
        boundary = early['fixture']['kickoff_at']
        self.visible['message']['sent_at'] = boundary
        for first in self.originals.values():
            first['delivered_at'] = boundary
        self.assertEqual(self.grade(early,self.lookup())['display_issue'],'DELIVERED_AT_OR_AFTER_PLANNED_KICKOFF')
        self.assertTrue(self.grade(later,self.lookup())['top3_hit'])

    def test_confirmed_first_abstention_keeps_its_slot(self):
        later = self.records[0]
        abstain, _ = attach_display({'primary':None},abstain=True)
        r = save(abstain,later['fixture'],stage='prematch',track='comprehensive',root=self.root/'freezes')
        first = verify(json.loads(Path(r['path']).read_bytes()))
        body = delivery_request(first)['text']
        plan = prepare([first],body,room_id='test-room',author_id='test-author',at=self.now)
        msg = deepcopy(self.visible)
        msg['message'].update(message_id='first-abstain',content={'text':body})
        slot = display_slot(first['match_id'])
        self.originals[slot].update(message_id='first-abstain',original_body_sha256=sha256(body.encode()).hexdigest())
        lookup = first_delivery_lookup([first],plan,message_id='first-abstain',read_message=lambda _: msg,
                                       lookup_original=self.originals.get,at=self.review_at)
        row = self.grade(first,lookup)
        self.assertEqual((row['display_status'],row['top1_issued'],row['top3_hit']),('ABSTAIN',False,None))
        with self.assertRaisesRegex(ValueError,'first channel delivery'):
            self.grade(later,self.lookup())

    def test_writer_checks_approved_stage_and_era_even_when_bad_scope_repeats_them(self):
        rec = self.records[0]
        payload = {k:v for k,v in rec['payload'].items() if k not in ('authored_display','authored_stage_scope')}
        cases = [('eng.1','eng.1','playoffs','playoffs',False),
                 ('uefa.champions','uefa.champions:NEW_FORMAT:league_phase','league_phase','league_phase',False),
                 ('uefa.champions','uefa.champions:NEW_FORMAT:league_phase','league_phase','NEW_FORMAT:league_phase',True),
                 ('afc.champions','afc.champions:ELITE:league_phase','league_phase','league_phase',True),
                 ('afc.champions','afc.champions:ELITE:group','group','group',False),
                 ('fifa.world','fifa.world','invented','invented',False)]
        for competition, cohort, stage, token, allowed in cases:
            f = dict(rec['fixture'],competition=competition,cohort=cohort,stage=stage)
            scope = dict(competition=competition,cohort=cohort,stages=[token])
            key = f'{cohort}-{token}'
            with self.subTest(cohort=cohort, token=token):
                if allowed:
                    self.assertIn('display_block',save_authored_row(payload,f,scope=scope,idempotency_key=key,
                                                                   root=self.root/'scope'))
                else:
                    with self.assertRaises(ValueError):
                        save_authored_row(payload,f,scope=scope,idempotency_key=key,root=self.root/'scope')
