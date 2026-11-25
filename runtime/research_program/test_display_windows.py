"""Exercise the batch identity at different output paths and the short-slate ledger."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from research.analysis_freeze import digest
from research_program import display_windows as windows
from research_program import goal_state_forward as ledger
from research_program.goal_state_study import write_new
from research_program.r8_score_study import read


class WindowPublicationTest(TestCase):
    def test_earlier_native_first_requires_the_exact_registered_receipt(self):
        kickoff = '2030-08-01T19:00:00+00:00'
        track = 'current-test-pair'
        record = dict(track=track, stage='prematch', revision=1, evaluation_slot='FIRST_PREMATCH',
                      previous_sha256=None, match_id='espn:42', recorded_at='2030-07-01T11:00:00+00:00',
                      fixture=dict(cutoff_at='2030-07-01T10:00:00+00:00', kickoff_at=kickoff))
        record['record_sha256'] = digest(record)
        slot = dict(sha256=record['record_sha256'], recorded_at=record['recorded_at'])
        info = dict(earlier_firsts={'espn:42': {track:slot}})
        plan = dict(recorded_at='2030-07-20T10:00:00+00:00', payload=dict(identity=info))
        self.assertTrue(windows._bound_first(record, plan, track))
        slot['sha256'] = 'a' * 64
        with self.assertRaisesRegex(ValueError, 'earlier first differs'):
            windows._bound_first(record, plan, track)
        info['earlier_firsts'] = {}
        with self.assertRaisesRegex(ValueError, 'without its original binding'):
            windows._bound_first(record, plan, track)

    def test_legacy_execution_receipt_and_explicit_weight_must_agree(self):
        matrix = [[0.1, 0.2], [0.3, 0.4]]
        info = dict(model_inputs=dict(native_models={'Current': {'sha256': 'a' * 64}}))
        native = dict(matrix=matrix, matrix_hash=digest(matrix))
        pair = dict(match_id='espn:42', payload=dict(current_model=native,
                    execution_audit=dict(current=dict(artifact_sha256='a' * 64))))
        self.assertIs(windows._native_weight(info, pair, 'current', 'current_sha256'), native)
        native['source_artifact_sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'artifact or matrix'):
            windows._native_weight(info, pair, 'current', 'current_sha256')

    def test_ranking_breaks_ties_by_start_then_match_id(self):
        rows = [dict(match_id=k,kickoff_at=at,ranking_probability=p)
                for k,at,p in [('a','2030-01-01T12:00Z',.11),('b','2030-01-01T11:00Z',.11),
                               ('c','2030-01-01T11:00Z',.11),('d','2030-01-01T13:00Z',.12)]]
        self.assertEqual([r['match_id'] for r in windows.ranked(rows)], ['d','b','c'])

    def test_unissued_batch_is_visible_and_immutable_across_output_directories(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = root/'store'
            plan_dir = root/'plan'
            kickoff = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            f = dict(match_id='test:1',home='team:1',away='team:2',cohort='jpn.1',stage='regular',kickoff_at=kickoff)
            identity = dict(version='unit',slates={'first':['test:1']})
            plan = ledger.register([f],identity,windows.TRACK,plan_dir,freeze_root=storage)
            write_new(plan_dir/'plan.json',plan)
            entries = {'test:1':dict(unissued_reason='missing teams')}
            body = '赛事 test:1\n未出单　missing teams'
            first = root/'one'
            second = root/'two'
            for folder in (first,second):
                folder.mkdir()
            ref = windows.lock_publication(plan_dir,'first',entries,body,first,freeze_root=storage)
            self.assertEqual(windows.lock_publication(plan_dir,'first',entries,body,second,freeze_root=storage),ref)
            saved = ledger._read(first,ref)['payload']['identity']
            self.assertEqual(saved['selected'],[])
            self.assertEqual(saved['slots'],3)
            self.assertEqual(saved['rows']['test:1']['unissued_reason'],'missing teams')
            with self.assertRaisesRegex(ValueError,'first placement changed'):
                windows.lock_publication(plan_dir,'first',entries,body+' extra',(root/'new'),freeze_root=storage)
            self.assertEqual(read(first/'publication.json'),ref)
            with self.assertRaisesRegex(ValueError,'visible reason'):
                windows.lock_publication(plan_dir,'first',entries,'missing teams',first,freeze_root=storage)
