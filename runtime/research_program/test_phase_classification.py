import copy
import unittest
from research_program.phase_classification import classify
from competition_scope import training_rows


def fixture(code='uefa.europa', stage='league-phase'):
    return {'cohort':code,'stage':stage,'score_basis':'regulation_90','neutral':False,
            'evidence':{'summary_url':f'https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/summary?event=1',
                        'summary_sha256':'a'*64,'status':'STATUS_FULL_TIME','regulation_periods':2}}


class Tests(unittest.TestCase):
    def test_explicit_phase_and_summary_required(self):
        for stage in (None,'invented','regular'):
            self.assertFalse(classify(fixture(stage=stage))['complete'])
        for key in ('summary_url','summary_sha256','status','regulation_periods'):
            row=fixture();del row['evidence'][key]
            self.assertFalse(classify(row)['complete'])
        row=fixture();row['evidence']['status']='STATUS_FINAL_AET'
        self.assertFalse(classify(row)['complete'])

    def test_national_identity_and_no_phase_pooling(self):
        world=classify(fixture('fifa.world','group-stage'))
        self.assertTrue(world['complete'])
        self.assertEqual(world['classification']['entity'],'national')
        self.assertEqual(classify(fixture())['classification']['entity'],'club')
        with self.assertRaisesRegex(ValueError,'one competition'):
            training_rows([fixture(),fixture(stage='final')])

    def test_unknown_identity_and_wrong_provider_refused(self):
        self.assertFalse(classify(fixture('invented.cup'))['complete'])
        row=fixture();row['evidence']['summary_url']='https://example.org/report'
        self.assertFalse(classify(row)['complete'])

    def test_legacy_group_stage_does_not_pool_with_league_phase(self):
        for code in ('uefa.europa', 'uefa.europa.conf'):
            old=fixture(code, 'group-stage');new=fixture(code, 'league-phase')
            self.assertTrue(classify(old)['complete'])
            self.assertNotEqual(classify(old)['route_key'],classify(new)['route_key'])
            with self.assertRaisesRegex(ValueError,'one competition'):
                training_rows([old,new])
