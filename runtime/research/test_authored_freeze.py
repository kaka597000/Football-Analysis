"""The opt-in normal author's freeze and its existing package contract."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from research.analysis_freeze import main, save, save_authored_row, verify
from research.display_delivery import attach_display, delivery_request, validate_display


class AuthoredFreezeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        now = datetime.now(timezone.utc)
        self.fixture = {'match_id': 'test:alpha-beta', 'home': 'test:alpha', 'away': 'test:beta',
                        'cohort': 'eng.1', 'competition': 'eng.1', 'stage': 'regular',
                        'identity_verified': True, 'score_basis': 'regulation_90',
                        'cutoff_at': (now - timedelta(minutes=5)).isoformat(),
                        'kickoff_at': (now + timedelta(hours=2)).isoformat()}
        self.scope = {'cohort':'eng.1', 'competition':'eng.1', 'stages':['regular']}
        self.payload = {'selection_scope': 'active_reference', 'primary': '2-1', 'alternates': ['1-1','1-0'],
                        'displayed_scores': ['2-1','1-1','1-0'],
                        'user_facing_row': {'match_id': self.fixture['match_id'], 'primary': '2-1',
                                            'alternates': ['1-1','1-0'], 'ordered_scores': ['2-1','1-1','1-0']}}

    def issue(self, payload=None, fixture=None, scope=None, out=None):
        return save_authored_row(self.payload if payload is None else payload,
                                 self.fixture if fixture is None else fixture,
                                 scope=self.scope if scope is None else scope, root=self.root,
                                 idempotency_key='fixture-first', out=out)

    def test_real_writer_returns_full_legacy_envelope_and_bound_block(self):
        before = deepcopy(self.payload)
        issued = self.issue()
        record = verify(json.loads(Path(issued['receipt']['path']).read_bytes()))
        self.assertEqual(record['record_sha256'], issued['receipt']['sha256'])
        self.assertEqual(issued['fixture'], record['fixture'])
        self.assertEqual(issued['payload'], record['payload'])
        self.assertEqual(issued['display_block'], delivery_request(record)['text'])
        self.assertIn(record['freeze_id'], issued['display_block'])
        self.assertNotIn('delivered_at', issued)
        self.assertEqual(self.payload, before)
        p = issued['payload']
        row = p['user_facing_row']
        # The three-way assertion and order used by the retained evening packager.
        self.assertEqual([p['primary'], *p['alternates']], p['displayed_scores'])
        self.assertEqual(p['displayed_scores'], [row['primary'], *row['alternates']])
        self.assertEqual(len(set(p['displayed_scores'])), 3)
        self.assertEqual(validate_display(p)['text'], p['authored_display']['text'])

    def test_league_and_cup_keep_their_own_fixture_ids_and_selected_order(self):
        for cohort, stage in [('ger.1','regular'), ('uefa.europa','league-phase')]:
            with self.subTest(cohort=cohort):
                match_id = 'test:'+cohort
                fixture = dict(self.fixture, cohort=cohort, competition=cohort, stage=stage, match_id=match_id)
                p = deepcopy(self.payload)
                p['user_facing_row']['match_id'] = match_id
                issue = self.issue(p, fixture, {'competition':cohort,'cohort':cohort,'stages':[stage]})
                self.assertIn(match_id,issue['display_block'])
                self.assertIn('第一比分：2-1',issue['display_block'])

    def test_legacy_freeze_remains_readable_and_has_no_bound_display(self):
        receipt = save(self.payload,self.fixture,stage='prematch',track='comprehensive',
                       root=self.root,idempotency_key='fixture-first')
        old_bytes = Path(receipt['path']).read_bytes()
        with self.assertRaisesRegex(ValueError,'explicit comprehensive display'):
            delivery_request(verify(json.loads(old_bytes)))
        with self.assertRaisesRegex(ValueError,'idempotency conflict'):
            self.issue()
        self.assertEqual(Path(receipt['path']).read_bytes(),old_bytes)
        self.assertEqual(len(list(self.root.rglob('[0-9]*.json'))),1)

    def test_conflicting_author_order_or_missing_signoff_fails_before_freezing(self):
        for override in ({'selection_scope':None}, {'selection_scope':'observation'},
                         {'displayed_scores':['2-1','1-0','1-1']},
                         {'displayed_scores':None},
                         {'primary':None,'alternates':[],'displayed_scores':[]},
                         {'authored_display':{}},
                         {'user_facing_row':dict(self.payload['user_facing_row'],match_id='test:wrong')},
                         {'user_facing_row':dict(self.payload['user_facing_row'],kickoff_bjt='2000-01-01T00:00Z')}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.issue(dict(self.payload,**override))
        p = deepcopy(self.payload)
        p['alternates'] = p['user_facing_row']['alternates'] = ['1-1','1-1']
        p['displayed_scores'] = p['user_facing_row']['ordered_scores'] = ['2-1','1-1','1-1']
        with self.assertRaisesRegex(ValueError,'distinct'):
            self.issue(p)
        self.assertFalse(list(self.root.rglob('[0-9]*.json')))

    def test_request_uses_saved_identity_order_and_track(self):
        issue = self.issue()
        record = verify(json.loads(Path(issue['receipt']['path']).read_bytes()))
        for k,v in [('track','current-test-pair'), ('stage','review'), ('match_id','test:other')]:
            with self.subTest(k=k), self.assertRaises(ValueError):
                delivery_request(dict(record,**{k:v}))
        for key in ('displayed_scores','authored_display','user_facing_row'):
            p = deepcopy(record['payload'])
            if key == 'displayed_scores':
                p[key].reverse()
            elif key == 'authored_display':
                p[key]['first']='0-0'
            else:
                p[key]['match_id']='test:other'
            with self.subTest(key=key), self.assertRaises(ValueError):
                delivery_request(dict(record,payload=p))

    def test_repeat_and_conflicting_key_keep_one_original_record(self):
        first = self.issue()
        receipt = first['receipt']
        path = Path(receipt['path'])
        original = path.read_bytes()
        self.assertEqual(self.issue(), first)
        p = dict(self.payload, explanation='different draft')
        with self.assertRaisesRegex(ValueError,'idempotency conflict'):
            self.issue(p)
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(len(list(self.root.rglob('[0-9]*.json'))),1)

    def test_late_retry_preserves_original_without_recreating_a_handoff(self):
        target = self.root/'private'/'issued.json'
        receipt = self.issue(out=target)['receipt']
        path = Path(receipt['path'])
        original = path.read_bytes()
        target.unlink()
        at_kickoff = datetime.fromisoformat(self.fixture['kickoff_at'])
        with patch('research.analysis_freeze.datetime') as clock:
            clock.now.return_value = at_kickoff
            clock.fromisoformat = datetime.fromisoformat
            with self.assertRaisesRegex(ValueError,'handoff deadline passed'):
                self.issue(out=target)
        self.assertFalse(target.exists())
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(len(list(self.root.rglob('[0-9]*.json'))),1)

    def test_missing_identity_outside_scope_or_late_first_refused(self):
        for override in ({'home':self.fixture['away']}, {'stage':''}, {'cohort':'fin.1'},
                         {'identity_verified':False}, {'identity_verified':None},
                         {'stage_verified':False}, {'phase':'Live'}, {'home':False},
                         {'score_basis':'including_extra_time'}, {'kickoff_at':self.fixture['cutoff_at']}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.issue(fixture=dict(self.fixture,**override))
        self.assertFalse(list(self.root.rglob('[0-9]*.json')))

    def test_cli_requires_explicit_comprehensive_prematch_and_key(self):
        source = self.root/'input.json'
        source.write_text(json.dumps({'fixture':self.fixture,'payload':self.payload}))
        scope_file = self.root/'scopes.json'
        scope_file.write_text(json.dumps([self.scope]))
        args = ['analysis_freeze',str(source),'--stage','prematch','--track','comprehensive',
                '--root',str(self.root/'freezes'),'--authored-table-key','author-1','--authored-scopes',str(scope_file)]
        output = io.StringIO()
        with patch.object(sys,'argv',args), redirect_stdout(output):
            main()
        envelope = json.loads(output.getvalue())
        self.assertIn('display_block',envelope)
        target = self.root/'batch'/'issued.json'
        output = io.StringIO()
        with patch.object(sys,'argv',args+['--authored-out',str(target)]), redirect_stdout(output):
            main()
        self.assertEqual(json.loads(target.read_bytes()),envelope)
        self.assertEqual(json.loads(output.getvalue()),envelope['receipt'])
        for which,val in ((3,'review'),(5,'current-test-pair')):
            invalid = list(args)
            invalid[which] = val
            with patch.object(sys,'argv',invalid), patch('sys.stderr',io.StringIO()), self.assertRaises(SystemExit):
                main()
        for bad_scopes in ([], [self.scope, self.scope], {}, ['invalid']):
            scope_file.write_text(json.dumps(bad_scopes))
            with patch.object(sys,'argv',args), patch('sys.stderr',io.StringIO()), self.assertRaises(SystemExit):
                main()
        with self.assertRaisesRegex(ValueError,'stable'):
            save_authored_row(self.payload,self.fixture,root=self.root,scope=self.scope,idempotency_key='')
        self.assertEqual(len(list(self.root.rglob('[0-9]*.json'))),1)

    def test_approved_stage_and_era_are_bound_to_the_exact_cohort(self):
        club = dict(self.fixture,competition='kor.1',cohort='kor.1',stage='final-a')
        approved = {'competition':'kor.1','cohort':'kor.1','stages':['regular']}
        with self.assertRaisesRegex(ValueError,'outside the approved'):
            self.issue(fixture=club,scope=approved)
        with self.assertRaisesRegex(ValueError,'same competition and cohort'):
            self.issue(fixture=self.fixture,scope=approved)
        ucl = dict(self.fixture,competition='uefa.champions',cohort='uefa.champions:NEW_FORMAT:league_phase',
                   stage='league_phase')
        scope = {'competition':'uefa.champions','cohort':ucl['cohort'],'stages':['NEW_FORMAT:league_phase']}
        self.assertEqual(self.issue(fixture=ucl,scope=scope)['payload']['authored_stage_scope'],scope)
        for wrong in ('group_stage','round-of-16','OTHER:league_phase'):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError,'outside the approved'):
                self.issue(fixture=dict(ucl,stage=wrong),scope=scope)
        with self.assertRaisesRegex(ValueError,'outside the approved'):
            self.issue(fixture=ucl,scope=dict(scope,stages=['OLD_FORMAT:league_phase']))

    def test_explicit_native_abstention_and_later_authored_scores_share_first_slot(self):
        declared, _ = attach_display({'primary':None,'selection_scope':'active_reference'},abstain=True)
        receipt = save(declared,self.fixture,stage='prematch',track='comprehensive',
                       root=self.root,idempotency_key='first-explicit-abstention')
        before = verify(json.loads(Path(receipt['path']).read_bytes()))
        later = self.issue()
        after = verify(json.loads(Path(later['receipt']['path']).read_bytes()))
        self.assertEqual(validate_display(before['payload'])['status'],'ABSTAIN')
        self.assertEqual(delivery_request(before)['slot'],delivery_request(after)['slot'])
        self.assertEqual((before['revision'],after['revision']),(1,2))

    def test_existing_pair_receipt_and_packager_chronology_survive_binding(self):
        from research.analysis_freeze import aware

        f = self.fixture
        pair_receipt = save({'synthetic_test_only':True,'current_model':{},'test_model':{}},
                            f,stage='prematch',track='current-test-pair',root=self.root,idempotency_key='pair')
        f = dict(f,cutoff_at=datetime.now(timezone.utc).isoformat())
        issue = self.issue(dict(self.payload,current_test_reference=pair_receipt),f)
        record = verify(json.loads(Path(issue['receipt']['path']).read_bytes()))
        original_pair = verify(json.loads(Path(pair_receipt['path']).read_bytes()))
        self.assertEqual(record['fixture'],issue['fixture'])
        self.assertEqual(record['payload'],issue['payload'])
        self.assertEqual(record['match_id'],original_pair['match_id'])
        self.assertEqual(original_pair['record_sha256'],record['payload']['current_test_reference']['sha256'])
        self.assertLessEqual(aware(original_pair['recorded_at']),aware(f['cutoff_at']))
        self.assertLessEqual(aware(f['cutoff_at']),aware(record['recorded_at']))
        self.assertLess(aware(record['recorded_at']),aware(f['kickoff_at']))

    def test_interrupted_batch_file_can_recover_without_overwriting_a_conflict(self):
        target = self.root/'private'/'issue.json'
        with patch('publication.deliver',side_effect=OSError('interrupted local write')), self.assertRaises(OSError):
            self.issue(out=target)
        self.assertFalse(target.exists())
        issued = self.issue(out=target)
        self.assertEqual(json.loads(target.read_bytes()),issued)
        self.assertEqual(self.issue(out=target),issued)
        self.assertEqual(len(list(self.root.rglob('[0-9]*.json'))),1)
        other = self.root/'another.json'
        other.write_bytes(b'separate original')
        with self.assertRaises(FileExistsError):
            self.issue(out=other)
        self.assertEqual(other.read_bytes(),b'separate original')
