import copy
import unittest
from datetime import datetime, timedelta, timezone
from competition_router import classify, validate_training, format_audit, empirical_profile
import test_main
from main_model import predict


def fixture(code='eng.1', stage=None):
    now=datetime.now(timezone.utc)
    f=dict(competition=code,cohort=code,neutral=False,
           cutoff_at=now.isoformat(),kickoff_at=(now+timedelta(days=2)).isoformat())
    if stage: f['stage']=stage
    return f


def official(f):
    return dict(source_url='https://example.org/official-rules',source_tier='L1',
                supports_field=['competition_classification'],query_time=f['cutoff_at'])


class RouterTests(unittest.TestCase):
    def test_registered_categories_separate_levels_and_cups(self):
        self.assertEqual(classify(fixture('eng.1'))['classification']['level'],'tier1')
        self.assertEqual(classify(fixture('eng.2'))['classification']['level'],'tier2')
        self.assertFalse(classify(fixture('jpn.emperor'))['complete'])
        cup=classify(fixture('jpn.emperor','round3'))
        self.assertEqual(cup['classification']['kind'],'domestic_cup')
        self.assertIn('aggregate_before_match',cup['requirements'])

    def test_unknown_is_never_inferred_from_name(self):
        f=fixture('England Women U17 Cup')
        self.assertFalse(classify(f)['complete'])
        self.assertIsNone(classify(f)['route_key'])

    def test_explicit_women_and_youth_need_official_evidence(self):
        f=fixture('women-u17-cup','group')
        f['competition_classification']=dict(competition='women-u17-cup',gender='women',age='U17',
                level='international',entity='national',kind='national_competitive',evidence=official(f))
        c=classify(f)
        self.assertTrue(c['complete']);self.assertIn('edition_age_and_overage_rules',c['requirements'])
        f['competition_classification']['evidence']['query_time']=f['kickoff_at']
        with self.assertRaises(ValueError):classify(f)

    def test_mixed_gender_age_level_or_stage_training_rejected(self):
        for field,value in [('gender','women'),('age','U17'),('level','tier2'),('stage','playoff')]:
            records=[]
            for i in range(2):
                r=dict(cohort='custom',competition='custom',stage='regular',observed_at='2026-01-01T00:00:00+00:00')
                c=dict(competition='custom',gender='men',age='senior',level='tier1',entity='club',kind='league',stage='regular')
                if i:c[field]=value
                if field=='stage' and i:r['stage']=value
                c['evidence']=dict(source_url='https://example.org/rules',source_tier='L1',supports_field=['competition_classification'],query_time=r['observed_at'])
                r['competition_classification']=c;records.append(r)
            with self.assertRaises(ValueError):validate_training(records)

    def test_registered_identity_cannot_be_overridden(self):
        f=fixture();c=dict(classify(f)['classification']);c['gender']='women';c['evidence']=official(f)
        f['competition_classification']=c
        with self.assertRaises(ValueError):classify(f)

    def test_provider_season_labels_not_stages_and_playoffs_not_assumed_regular(self):
        self.assertEqual(classify(fixture('jpn.1','2025-japanese-j1-league'))['classification']['stage'],'regular')
        self.assertFalse(classify(fixture('usa.1'))['complete'])
        self.assertEqual(classify(fixture('usa.1','all-star-game'))['classification']['kind'],'friendly')
        self.assertFalse(classify(fixture('jpn.league_cup','cup'))['complete'])
        self.assertEqual(classify(fixture('fifa.friendly'))['classification']['stage'],'friendly')

    def test_youth_league_requires_edition_age_rules(self):
        f=fixture('youth-league','regular')
        f['competition_classification']=dict(competition='youth-league',gender='men',age='U17',
                level='tier1',entity='club',kind='league',evidence=official(f))
        route=classify(f)
        self.assertTrue(format_audit(f,route))
        f['competition_rules']=dict(competition='youth-league',stage='regular',neutral=False,
                score_basis='regulation_90',qualification_state='League points',rotation_summary='Verified squad',
                age_eligibility_rule='Edition U17 eligibility verified',evidence=official(f))
        self.assertEqual(format_audit(f,route),[])

    def test_cup_rules_require_leg_aggregate_and_90_minute_target(self):
        f=fixture('uefa.champions','quarterfinal');route=classify(f)
        self.assertTrue(format_audit(f,route))
        f['competition_rules']=dict(competition='uefa.champions',stage='quarterfinal',neutral=False,
            format='two_leg',leg_number=2,aggregate_before_match=[0,1],away_goals_rule=False,
            extra_time_possible=True,penalties_possible=True,score_basis='regulation_90',
            qualification_state='Home trails aggregate by one',rotation_summary='Official squad reviewed',evidence=official(f))
        self.assertEqual(format_audit(f,route),[])
        f['competition_rules']['score_basis']='after_extra_time'
        self.assertTrue(format_audit(f,route))
        f['competition_rules']['score_basis']='regulation_90';f['competition_rules']['leg_number']=1
        self.assertTrue(format_audit(f,route))

    def test_profile_excludes_future_same_day_late_observed_and_other_routes(self):
        f=fixture();end=datetime.fromisoformat(f['kickoff_at']).date()
        records=[]
        for i in range(65):
            records.append(dict(cohort='eng.1',date=(end-timedelta(days=i+1)).isoformat(),
                observed_at=f['cutoff_at'],home_goals=1,away_goals=1))
        records += [dict(records[0],date=end.isoformat(),home_goals=20),
                    dict(records[0],observed_at=f['kickoff_at'],home_goals=20),
                    dict(records[0],cohort='esp.1',home_goals=20)]
        p=empirical_profile(records,f)
        self.assertEqual(p['n'],65);self.assertEqual(p['mean_total_goals'],2)
        self.assertEqual(p['possession_style'],'UNKNOWN');self.assertFalse(p['numeric_adjustment_applied'])

    def test_prediction_rejects_wrong_stage_and_unknown_venue(self):
        t=test_main.Tests();t.rows=test_main.rows()
        from main_model import fit_core,form_training
        t.core=fit_core(t.rows);t.form=form_training(t.rows)
        f=t.fixture();f['stage']='playoff'
        with self.assertRaisesRegex(ValueError,'route mismatch'):predict(t.artifact(),f)
        f=t.fixture();del f['neutral']
        with self.assertRaisesRegex(ValueError,'neutral venue'):predict(t.artifact(),f)
        from main_model import history
        incomplete=copy.deepcopy(t.rows);del incomplete[0]['neutral']
        with self.assertRaisesRegex(ValueError,'historical neutral venue'):history(incomplete)


if __name__=='__main__':unittest.main()
