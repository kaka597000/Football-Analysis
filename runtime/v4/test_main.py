import copy,json,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
import numpy as np
from main_model import (VERSION,CONFIG,code_hash,history,recent_features,form_training,fit_core,
                        distribution,choose,predict,freeze,digest,quote_check)
from review import evaluate


def rows():
    start=datetime(2020,1,1,tzinfo=timezone.utc); out=[]
    for i in range(180):
        out.append({'match_id':str(i),'cohort':'TEST','date':(start+timedelta(days=i)).date().isoformat(),
                    'home':'A' if i%2 else 'B','away':'B' if i%2 else 'A',
                    'home_goals':2 if i%2 else i%3,'away_goals':i%2,'score_basis':'regulation_90',
                    'source_url':'https://example.org/result','observed_at':'2026-01-01T00:00:00+00:00','neutral':False})
    return out

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=rows(); cls.form=form_training(cls.rows); cls.core=fit_core(cls.rows)

    def artifact(self):
        return dict(version=VERSION,cohort='TEST',core=self.core,form=self.form,history=self.rows,
                    selected_recipe='form180',execution_recipe='form180',train_end=self.rows[-1]['date'],observed_at='2026-01-01T00:00:00+00:00',
                    built_at='2026-01-02T00:00:00+00:00',code_hash=code_hash(),config=CONFIG)

    def fixture(self):
        now=datetime.now(timezone.utc)
        return dict(match_id='future',cohort='TEST',home='A',away='B',phase='PreMatch',identity_verified=True,
                    cutoff_at=now.isoformat(),kickoff_at=(now+timedelta(days=2)).isoformat(),
                    sources=[dict(source_url='https://example.org/fixture',query_time=now.isoformat())])

    def test_features_exclude_target_and_same_day_results(self):
        r=self.rows[100]; before=recent_features(self.rows,r)
        changed=copy.deepcopy(self.rows)
        for row in changed:
            if row['date']>=r['date']: row['home_goals']=30;row['away_goals']=30
        np.testing.assert_array_equal(before,recent_features(changed,r))

    def test_training_offsets_strictly_prior(self):
        self.assertGreaterEqual(self.form['target_count'],100)
        for fold in self.form['folds']: self.assertLess(fold['fit_end'],fold['targets_start'])
        self.assertTrue(np.isfinite(self.form['coefficients']).all())

    def test_all_recipes_probability_consistency(self):
        from main_model import RECIPES,probabilities,total_settlement
        for recipe in RECIPES:
            m,meta=distribution(self.core,self.form,self.rows,self.fixture(),recipe)
            self.assertAlmostEqual(float(m.sum()),1,12);self.assertTrue(np.all(m>=0))
            self.assertAlmostEqual(float(sum(probabilities(m))),1,12)
            self.assertAlmostEqual(sum(total_settlement(m,2.25,'over').values()),1,12)
            if meta['form_log_adjustment']: self.assertLessEqual(max(map(abs,meta['form_log_adjustment'])),.35)

    def test_selection_retains_baseline_without_positive_evidence(self):
        metrics={'n':100,'primary_hits':12,'top1':.12,'score_log_loss':3.}
        self.assertEqual(choose({'baseline':metrics,'strength180':dict(metrics)}),'baseline')
        better=dict(metrics,primary_hits=14,top1=.14,score_log_loss=3.2)
        self.assertEqual(choose({'baseline':metrics,'strength180':better}),'baseline')
        better['score_log_loss']=2.9
        self.assertEqual(choose({'baseline':metrics,'strength180':better}),'strength180')

    def test_unverified_total_never_falls_back_to_2_5(self):
        p=predict(self.artifact(),self.fixture());self.assertIsNone(p['total'])
        f=self.fixture();f['total_line']=2.5
        with self.assertRaises(ValueError): predict(self.artifact(),f)
        f=self.fixture();f['total_quote']={'verified':False}
        with self.assertRaises(ValueError): predict(self.artifact(),f)

    def test_actual_quarter_quote_and_fixture_mapping(self):
        f=self.fixture();q=dict(verified=True,match_id=f['match_id'],source_url='https://example.org/market',
                bookmaker='Book',query_time=f['cutoff_at'],line=2.25,market='asian_total')
        f['total_quote']=q;p=predict(self.artifact(),f)
        self.assertEqual(p['total']['quote']['line'],2.25)
        self.assertEqual(p['total']['quote']['quoted_at_status'],'UNKNOWN')
        self.assertAlmostEqual(sum(p['total']['over'].values()),1,12)
        q['match_id']='other'
        with self.assertRaises(ValueError):predict(self.artifact(),f)

    def test_later_sources_artifacts_and_code_rejected(self):
        f=self.fixture();a=self.artifact();a['built_at']=f['kickoff_at']
        with self.assertRaises(ValueError):predict(a,f)
        a=self.artifact();a['code_hash']='wrong'
        with self.assertRaises(ValueError):predict(a,f)
        f['sources'][0]['known_at']=f['kickoff_at']
        with self.assertRaises(ValueError):predict(self.artifact(),f)

    def test_unseen_and_manual_context_rejected(self):
        f=self.fixture();f['home']='new'
        with self.assertRaises(ValueError):predict(self.artifact(),f)
        f=self.fixture();f['context_features']={'motivation':2}
        with self.assertRaises(ValueError):predict(self.artifact(),f)

    def test_research_freeze_tampering_and_overwrite(self):
        f=self.fixture();a=self.artifact();p=predict(a,f)
        with tempfile.TemporaryDirectory() as d:
            tamper=copy.deepcopy(p);tamper['wdl_reference']='客胜' if p['wdl_reference']!='客胜' else '主胜'
            with self.assertRaises(ValueError):freeze(a,tamper,d)
            path=freeze(a,p,d);saved=json.loads(Path(path).read_text())
            self.assertFalse(saved['formal_kpi_eligible']);self.assertEqual(evaluate([saved],[],1)['pending'],['future'])
            with self.assertRaises(FileExistsError):freeze(a,p,d)
            saved['primary']='9-9'
            with self.assertRaises(ValueError):evaluate([saved],[],1)

    def test_context_training_rejects_empty_or_invented_samples(self):
        from context_learning import train_context
        with self.assertRaises(ValueError): train_context(self.artifact(),[])
        with self.assertRaises(ValueError): train_context(self.artifact(),[{'forecast':predict(self.artifact(),self.fixture()),'result':{}}])

    def test_validated_context_applies_only_to_complete_official_evidence(self):
        from test_pipeline import fixture as complete_fixture
        from context_learning import CONTRACT,baseline_contract
        from context import assess
        f=complete_fixture();f.pop('total_line');a=self.artifact()
        before=predict(a,f);names=sorted(assess(f)['features']);coef=[0.]*len(names)
        coef[names.index('home_missing_key_attackers')]=-.1
        a['context_coefficients']=dict(features=names,mean=[0.]*len(names),scale=[1.]*len(names),
            coefficients=[coef,[0.]*len(names)],validated=True,feature_contract=CONTRACT,
            baseline_contract=baseline_contract(a),observed_at='2026-01-01T00:00:00+00:00')
        after=predict(a,f)
        self.assertTrue(after['context_numeric_adjustment_applied'])
        self.assertLess(after['rates']['means'][0],before['rates']['means'][0])
        self.assertAlmostEqual(np.asarray(after['matrix']).sum(),1)
        f['context_audit']['home']['availability']['lineup_confirmed']=False
        self.assertFalse(predict(a,f)['context_numeric_adjustment_applied'])
        a['context_coefficients']['baseline_contract']='wrong'
        with self.assertRaises(ValueError):predict(a,f)

    def test_real_sample_contract_and_temporal_context_learning(self):
        # Synthetic dated freezes test learning plumbing; never count as real performance evidence.
        from test_pipeline import fixture as complete_fixture
        from context_learning import baseline_contract,train_context
        a=self.artifact();base=complete_fixture();base.pop('total_line');samples=[]
        original=datetime.fromisoformat(base['kickoff_at'])
        def shift(value,delta):
            if isinstance(value,dict):return {k:shift(v,delta) for k,v in value.items()}
            if isinstance(value,list):return [shift(v,delta) for v in value]
            if isinstance(value,str) and 'T' in value:
                try:return (datetime.fromisoformat(value)+delta).isoformat()
                except ValueError:return value
            return value
        for i in range(200):
            kickoff=datetime(2021,1,1,12,tzinfo=timezone.utc)+timedelta(days=i)
            f=shift(base,kickoff-original);f['match_id']='context-'+str(i)
            f['context_audit']['home']['availability']['missing_key_attackers']=i%2
            forecast=dict(version=VERSION,freeze_status='NEW_MAIN_RESEARCH_FREEZE',input=f,
                generated_at=(kickoff-timedelta(hours=1)).isoformat(),frozen_at=(kickoff-timedelta(minutes=30)).isoformat(),
                recipe=a['execution_recipe'],baseline_contract=baseline_contract(a),context_numeric_adjustment_applied=False,
                model_built_at='2020-07-01T00:00:00+00:00',model_observed_at='2020-06-30T00:00:00+00:00',rates={'means':[1.05,.95]})
            forecast['record_hash']=digest(forecast)
            result=dict(match_id=f['match_id'],home_goals=0 if i%2 else 2,away_goals=0,score_basis='regulation_90',
                observed_at=(kickoff+timedelta(hours=3)).isoformat(),source_url='https://example.org/test-result')
            samples.append(dict(forecast=forecast,result=result))
        updated=train_context(a,samples); learned=updated['context_coefficients']
        self.assertEqual(learned['n'],140);self.assertEqual(learned['validation']['n'],60)
        self.assertTrue(learned['validated']);self.assertLess(learned['coefficients'][0][learned['features'].index('home_missing_key_attackers')],0)
        samples[0]['result']['score_basis']='after_extra_time'
        with self.assertRaises(ValueError):train_context(a,samples)

    def test_acceptance_gate_rejects_no_improvement(self):
        from main_model import comparison
        records=[{'match_id':str(i),'date':'2020-01-'+str(i+1).zfill(2),'hit':i%2==0} for i in range(20)]
        m=dict(top1=.5,records=records,score_log_loss=3.,wdl_brier=.6)
        self.assertFalse(comparison(m,m)['acceptance_gate_passed'])

    def test_review_90_minute_and_half_settlement_accounting(self):
        now=datetime.now(timezone.utc); kickoff=now-timedelta(days=2)
        # Explicit test fixture for accounting, not a production historical freeze.
        f=dict(version=VERSION,freeze_status='NEW_MAIN_RESEARCH_FREEZE',input=dict(match_id='review',
               cutoff_at=(kickoff-timedelta(hours=2)).isoformat(),kickoff_at=kickoff.isoformat()),
               generated_at=(kickoff-timedelta(hours=1)).isoformat(),frozen_at=(kickoff-timedelta(minutes=30)).isoformat(),
               primary='2-0',wdl_reference='主胜',total=dict(quote={'line':2.25},reference_direction='under'))
        f['record_hash']=digest(f)
        r=dict(match_id='review',home_goals=2,away_goals=0,score_basis='regulation_90',
               source_url='https://example.org/result',observed_at=(kickoff+timedelta(hours=3)).isoformat())
        out=evaluate([f],[r],3)
        self.assertEqual(out['total']['settlements']['half_win'],1);self.assertEqual(out['primary']['accuracy'],1)
        self.assertIsNone(out['total']['full_win_accuracy_excluding_push_and_half']);self.assertEqual(out['coverage'],1/3)
        r['score_basis']='after_extra_time'
        with self.assertRaises(ValueError):evaluate([f],[r],3)
        with self.assertRaises(ValueError):evaluate([f,f],[],3)

if __name__=='__main__':unittest.main()
