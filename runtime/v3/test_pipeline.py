import copy,json,tempfile,unittest
from unittest.mock import patch
from datetime import datetime,timedelta,timezone
from pathlib import Path
import numpy as np
from pipeline import predict,freeze,evaluate,train_context,FEATURE_CONTRACT
from context import assess,source
from model import digest

# Synthetic records exercise code only; never used to claim model performance.
def fixture():
    now=datetime.now(timezone.utc);kick=now+timedelta(hours=2);q=now.isoformat()
    f=dict(match_id='test',cohort='TEST',home='A',away='B',phase='PreMatch',cutoff_at=q,kickoff_at=kick.isoformat(),
           total_line=2.25,identity_verified=True,context_audit={},sources=[dict(source_url='https://example.org/fixture',query_time=q)])
    def e(field):return dict(source_url='https://example.org/'+field,query_time=q,source_tier='L1',supports_field=[field])
    for side in ('home','away'):
        f['context_audit'][side]={
            'strength':dict(games=20,points=30,venue_games=10,venue_goals_for=15,venue_goals_against=10,competition='TEST',evidence=e('strength')),
            'motivation':dict(objective='relegation',points_gap_to_target=2,remaining_matches=10,draw_acceptable=False,evidence=e('motivation')),
            'schedule':dict(coverage=dict(all_competitions_verified=True,**{'from':(kick-timedelta(days=15)).isoformat(),'to':(kick+timedelta(days=8)).isoformat()}),matches=[dict(match_id='prior',kickoff_at=(kick-timedelta(days=3)).isoformat(),competition='CUP',status='finished',evidence=e('schedule')),dict(match_id='next',kickoff_at=(kick+timedelta(days=3)).isoformat(),competition='LEAGUE',status='scheduled',evidence=e('schedule'))],evidence=e('schedule')),
            'availability':dict(lineup_confirmed=True,missing_regular_starters=2,missing_key_attackers=1,missing_key_defenders=0,lineup_evidence=e('lineup'),evidence=e('availability')),
            'tactics':dict(verified=True,matchup_summary='Fixture test only',evidence=e('tactics')),
            'integrity':dict(verified=True,venue='Stadium',travel_summary='Verified test route',weather_summary='Verified test forecast',evidence=e('integrity'))}
    return f

def artifact(f,validated=True):
    m=dict(cohort='TEST',intercept=0.,home_advantage=.2,attack={'A':.1,'B':-.1},defence={'A':0.,'B':0.},
           observed_at=(datetime.fromisoformat(f['cutoff_at'])-timedelta(seconds=1)).isoformat(),nuisance={'poisson':0.},selected_family='poisson',temperature=1.)
    if validated:
        keys=sorted(assess(f)['features']);coefs=[0.]*len(keys);coefs[keys.index('home_missing_key_attackers')]=-.1
        m['context_coefficients']=dict(features=keys,mean=[0.]*len(keys),scale=[1.]*len(keys),coefficients=[coefs,[0.]*len(keys)],
            validated=True,feature_contract=FEATURE_CONTRACT,cohort='TEST',validated_family='poisson',validated_temperature=1.,observed_at=m['observed_at'])
    return m

class Tests(unittest.TestCase):
    def test_complete_feature_extraction_includes_cup_and_next_match(self):
        f=fixture();r=assess(f);self.assertTrue(r['complete']);self.assertEqual(r['features']['home_matches_previous14d'],1)
        self.assertEqual(r['features']['home_matches_next7d'],1);self.assertEqual(r['features']['home_kickoff_gap_hours_capped336'],72)
        self.assertAlmostEqual(r['features']['home_target_gap_per_remaining_match'],.2)

    def test_incomplete_schedule_not_zero_filled_or_applied(self):
        f=fixture();m=artifact(f);f['context_audit']['home']['schedule']['coverage']['all_competitions_verified']=False
        r=predict(m,f);self.assertEqual(r['recommendation']['status'],'OBSERVE');self.assertFalse(r['context_adjustment_applied'])
        self.assertNotIn('home_matches_previous14d',r['context_assessment']['features'])

    def test_later_data_false_source_and_future_result_leakage(self):
        for change in ('time','field','future'):
            f=fixture()
            if change=='time':f['context_audit']['home']['strength']['evidence']['query_time']=f['kickoff_at']
            if change=='field':f['context_audit']['home']['strength']['evidence']['supports_field']=['unrelated']
            if change=='future':f['context_audit']['home']['schedule']['matches'][1]['status']='finished'
            self.assertFalse(assess(f)['complete'])

    def test_official_lineup_required_and_unknown_not_assumed_healthy(self):
        f=fixture();f['context_audit']['away']['availability']['evidence']['source_tier']='L2'
        self.assertFalse(assess(f)['complete'])
        f=fixture();del f['context_audit']['away']['availability']['missing_key_attackers']
        self.assertFalse(assess(f)['complete'])

    def test_cup_missing_coverage_duplicate_and_unresolved_match(self):
        for change in ('coverage','duplicate','unresolved'):
            f=fixture();s=f['context_audit']['home']['schedule']
            if change=='coverage':s['coverage']['from']=f['cutoff_at']
            if change=='duplicate':s['matches'].append(copy.deepcopy(s['matches'][0]))
            if change=='unresolved':s['matches'][0]['status']='scheduled'
            self.assertFalse(assess(f)['complete'])

    def test_validated_context_changes_matrix_unvalidated_retains_baseline(self):
        f=fixture();base=predict(artifact(f,False),f);new=predict(artifact(f),f)
        self.assertFalse(base['context_adjustment_applied']);self.assertTrue(new['context_adjustment_applied'])
        self.assertLess(new['rates'][0],base['rates'][0]);self.assertNotEqual(base['matrix_hash'],new['matrix_hash'])
        self.assertAlmostEqual(np.asarray(new['matrix']).sum(),1);self.assertEqual(new['recommendation']['status'],'RESEARCH_ELIGIBLE')
        self.assertFalse(new['declared_targets']['achieved'])

    def test_manual_feature_overrides_rejected_and_market_conflict_observed(self):
        f=fixture();m=artifact(f);f['context_features']={'manual':1}
        with self.assertRaises(ValueError):predict(m,f)
        f=fixture();f['market_conflict']=True;self.assertEqual(predict(m,f)['recommendation']['status'],'OBSERVE')

    def test_training_refuses_empty_incomplete_and_non90_targets(self):
        with self.assertRaises(ValueError):train_context([])
        f=fixture();sample=dict(fixture=f,cohort='TEST',kickoff_at=f['kickoff_at'],cutoff_at=f['cutoff_at'],score_basis='extra_time',home_goals=1,away_goals=1,result_observed_at=(datetime.fromisoformat(f['kickoff_at'])+timedelta(hours=3)).isoformat())
        with self.assertRaises(ValueError):train_context([sample])
        sample['score_basis']='regulation_90';f['identity_verified']=False
        with self.assertRaises(ValueError):train_context([sample])

    def test_freeze_reproduces_matrix_and_never_overwrites(self):
        f=fixture();m=artifact(f);p=predict(m,f)
        with tempfile.TemporaryDirectory() as d:
            bad=copy.deepcopy(p);bad['matrix'][0][0]=.99
            with self.assertRaises(ValueError):freeze(bad,m,d)
            path=freeze(p,m,d);saved=json.loads(Path(path).read_text());self.assertFalse(saved['formal_kpi_eligible'])
            with self.assertRaises(FileExistsError):freeze(p,m,d)
            f['context_audit']['home']['availability']['lineup_confirmed']=False
            with self.assertRaises(ValueError):freeze(predict(m,f),m,d)

    def test_evaluation_cannot_count_qualification_as90_win_and_tracks_push(self):
        f=fixture();m=artifact(f);p=predict(m,f)
        with tempfile.TemporaryDirectory() as d:saved=json.loads(Path(freeze(p,m,d)).read_text())
        kick=datetime.fromisoformat(f['kickoff_at'])
        class Later(datetime):
            @classmethod
            def now(cls, tz=None):return (kick+timedelta(hours=4)).astimezone(tz or timezone.utc)
        self.mock_clock=patch('pipeline.datetime',Later);self.mock_clock.start();self.addCleanup(self.mock_clock.stop)
        saved['frozen_at']=(kick-timedelta(hours=1)).isoformat()
        saved['record_hash']=digest({k:v for k,v in saved.items() if k!='record_hash'})
        result=dict(match_id='test',home_goals=0,away_goals=0,score_basis='regulation_90',observed_at=(kick+timedelta(hours=3)).isoformat(),source_url='https://example.org/result')
        r=evaluate([saved],[result],eligible_count=5);self.assertEqual(r['coverage'],.2);self.assertEqual(r['score_top1']['n'],1)
        self.assertFalse(r['rows'][0]['wdl_hit']);self.assertTrue(r['target_attainment_not_proven'])
        result['observed_at']=(kick+timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):evaluate([saved],[result])
        result['observed_at']=(kick+timedelta(hours=3)).isoformat()
        result['score_basis']='shootout'
        with self.assertRaises(ValueError):evaluate([saved],[result])
        result['score_basis']='regulation_90'
        with self.assertRaises(ValueError):evaluate([saved],[result,result])
        pending=evaluate([saved],[]);self.assertEqual(pending['settled'],0);self.assertEqual(pending['pending'],['test'])
        changed=copy.deepcopy(saved);changed['primary']='9-9'
        with self.assertRaises(ValueError):evaluate([changed],[result])
        # Explicit push and half-loss are preserved, not counted as an 80% direction success.
        saved['total_line']=2.;saved['record_hash']=digest({k:v for k,v in saved.items() if k!='record_hash'})
        result.update(home_goals=1,away_goals=1)
        self.assertEqual(evaluate([saved],[result])['total']['settlements']['push'],1)
        saved['total_line']=2.25;saved['record_hash']=digest({k:v for k,v in saved.items() if k!='record_hash'})
        rr=evaluate([saved],[result]);self.assertEqual(sum(rr['total']['settlements'][k] for k in ('half_loss','half_win')),1)

if __name__=='__main__':unittest.main()
