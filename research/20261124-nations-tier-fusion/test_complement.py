from copy import deepcopy
from contextlib import redirect_stderr
from io import StringIO
import sys
import unittest
from unittest.mock import patch

import numpy as np

import complement
import complement_run
from complement import (BINS, direction_metrics, fit_selector, margin_bin, oracle, original_roster,
                        select_source)


def fixture(tier='A', date='2026-11-12', *, market=None):
    base=np.array([[.40,.37],[.13,.10]])
    return dict(tier=tier,date=date,cutoff=date+'T12:00:00Z',base=base,
                market=market if market is not None else np.array([[.20,.57],[.13,.10]]))


def label(score):
    return dict(score=score,observed_at='2026-11-14T21:50:00Z',committed_at='2026-11-15T07:55:00Z')


def pick(primary,three=None):
    return dict(primary=primary,top3=three or [primary,'1-0','0-1'],wdl='home',total=None)


class SourceChoice(unittest.TestCase):
    def test_train_uses_a_hard_switch_with_earlier_discordance(self):
        fixtures={str(i):fixture(date='2026-11-'+str(12+i%2)) for i in range(4)}
        labels={str(i):label(s) for i,s in enumerate(([0,1],[0,1],[0,0],[1,1]))}
        trained=fit_selector(fixtures,labels)
        self.assertEqual(trained['route'],{BINS[0]:'market',BINS[1]:'team'})
        future=fixture(date='2026-11-16')
        self.assertEqual(select_source(future,trained),'market')
        self.assertEqual(select_source(dict(future,cutoff='2026-11-10T12:00:00Z'),trained),'team')
        self.assertEqual(select_source(dict(future,market=None),trained),'team')

    def test_sparse_early_signal_has_a_team_fallback(self):
        d={str(i):fixture() for i in range(5)}
        labels={k:label([0,1]) for k in d}
        self.assertEqual(fit_selector(d,labels)['route'][BINS[0]],'team')
        d['4']['date']='2026-11-13'
        labels={k:label([1,1]) for k in d}
        stopped=fit_selector(d,labels)
        self.assertEqual(stopped['disposition'],'STOP_NO_EARLY_RESCUE')
        self.assertEqual(select_source(fixture(date='2026-11-17'),stopped),'team')

    def test_known_after_fit_and_held_out_rows_rejected(self):
        row=fixture()
        for labels in ({'a':dict(label([1,0]),committed_at='2026-11-15T08:00:01Z')},
                       {'a':dict(label([1,0]),observed_at='2026-11-15T08:00:01Z')},
                       {'a':label([1,0]),'b':label([0,0])}):
            with self.assertRaises(ValueError):
                fit_selector({'a':row},labels)
        with self.assertRaises(ValueError):
            fit_selector({'a':dict(row,date='2026-11-15')},{'a':label([1,0])})

    def test_market_confidence_is_one_relative_rank_margin(self):
        row=fixture()
        self.assertEqual(margin_bin(row['base'],row['market']),BINS[0])
        self.assertEqual(margin_bin(row['market'],row['base']),BINS[1])
        self.assertIsNone(margin_bin(row['base'],row['base']))
        with self.assertRaises(ValueError):
            margin_bin(row['base'],np.array([[.2,.8]]))

    def test_roster_never_loads_protected_queue_or_post_window_archives(self):
        with patch.object(complement,'frozen_nations_roster',return_value={'a':fixture()}) as roster, \
                patch.object(complement,'merge_original_markets',side_effect=lambda repo,x:x) as markets:
            self.assertEqual(set(original_roster('repo')),{'a'})
            roster.assert_called_once_with('repo')
            markets.assert_called_once()
        with patch.object(complement,'frozen_nations_roster',return_value={'a':fixture(date='2026-11-23')}), \
                patch.object(complement,'merge_original_markets',side_effect=lambda repo,x:x):
            with self.assertRaisesRegex(ValueError,'outside preregistered'):
                original_roster('repo')

    def test_selector_lock_requests_only_early_labels(self):
        rows = {str(i):dict(fixture(date='2026-11-'+d), pair_sha=str(i), issue_sha=None, line=None)
                for i,d in enumerate(('12','13','15','17'))}
        early = {'0':label([0,1]), '1':label([0,0])}
        with patch.object(complement_run,'certified',return_value=(early,{})) as cert:
            locked = complement_run.original_selector_lock('repo',rows)
        self.assertTrue(cert.call_args.kwargs['training'])
        self.assertEqual(set(cert.call_args.args[1]),set(early))
        self.assertEqual([r['id'] for r in locked['late']], ['2','3'])

    def test_grade_rejects_mutable_ref_before_any_archive_is_opened(self):
        args = ['prog','grade','--data','missing','--out','unused','--seal-commit','main']
        with patch.object(sys,'argv',args), patch.object(complement_run,'original_roster') as read, redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                complement_run.main()
        read.assert_not_called()


class ComplementCounts(unittest.TestCase):
    def setUp(self):
        self.roles=('team','market','xg')
        choices=[({'team':'0-0','market':'0-1','xg':'1-0'},'0-1'),
                 ({'team':'1-1','market':'1-1','xg':'1-0'},'1-1'),
                 ({'team':'0-0','market':'0-1'},'2-2'),
                 ({'team':'2-0'},'2-0'),
                 ({'team':'3-0','xg':'1-0'},None)]
        self.rows=[(dict(choices={k:pick(p) for k,p in c.items()}),s) for c,s in choices]

    def test_union_vs_unique_and_missing_are_distinct(self):
        out=oracle(self.rows,self.roles)
        self.assertEqual((out['registered'],out['scored'],out['two_or_more']),(5,4,3))
        self.assertEqual((out['covered_by_any_first'],out['shared_winning_matches'],out['all_available_missed']),(3,1,1))
        self.assertEqual(out['hits_with_only_one_available'],1)
        self.assertEqual(out['by_role']['team']['exclusive_among_available'],0)
        self.assertEqual(out['by_role']['market']['exclusive_among_available'],1)
        self.assertEqual(out['by_role']['xg']['scored'],2)

    def test_complete_subsets_use_a_common_denominator(self):
        out=oracle(self.rows,self.roles,complete=True)
        self.assertEqual(out['registered'],2)
        self.assertEqual(out['additional_vs_best_single'],0)
        self.assertTrue(all(v['scored']==2 for v in out['by_role'].values()))
        no_author=oracle(self.rows,('team','author'),complete=True)
        self.assertEqual(no_author['scored'],0)
        self.assertIsNone(no_author['oracle_rate'])

    def test_same_source_variants_cannot_add_an_independent_rescue(self):
        rows=[(dict(choices={'team':pick('0-0'),'market':pick('0-0'),'repair':pick('2-0')}),'2-0')]
        self.assertEqual(oracle(rows,self.roles)['covered_by_any_first'],0)
        self.assertEqual(oracle(rows,self.roles+('repair',))['covered_by_any_first'],1)

    def test_residual_matrix_three_is_not_first_score_oracle(self):
        rows=[(dict(choices={'team':pick('0-0',['0-0','1-0','0-1']),'market':pick('1-1',['1-1','1-0','2-0'])}),'2-0')]
        out=oracle(rows,('team','market'),complete=True)
        self.assertEqual(out['covered_by_any_first'],0)
        self.assertEqual(out['all_missed_inside_one_matrix_three'],1)
        self.assertEqual(out['all_missed_outside_every_matrix_three'],0)

    def test_total_push_and_unobserved_line_keep_separate_denominators(self):
        chosen=pick('1-0',['1-0','1-1','2-0'])
        base=pick('1-1',['1-1','0-0','1-0'])
        rows=[]
        for line in (1.,.75,None):
            c=deepcopy(chosen); b=deepcopy(base)
            if line is not None:
                c['total']='over'
                b['total']='under'
            rows.append((dict(selector_source='market',choices={'team':b,'selector':c},line=line),'1-0'))
        result=direction_metrics(rows)
        self.assertEqual(result['arms']['selector']['top1'],dict(hits=3,n=3))
        self.assertEqual(result['arms']['selector']['actual_line_full_win'],dict(hits=0,n=1))
        self.assertEqual(result['asian_five_states']['selector']['PUSH'],1)
        self.assertEqual(result['asian_five_states']['selector']['HALF_WIN'],1)
        self.assertEqual(result['flips']['top1']['gained'],3)


if __name__=='__main__':
    unittest.main()
