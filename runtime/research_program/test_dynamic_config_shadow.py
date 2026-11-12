"""Configuration reproducibility only; synthetic rows are not model evidence."""
import copy
import json
import math
import unittest
from unittest.mock import patch
import numpy as np
from research_program import challengers as subject


class DynamicConfigTests(unittest.TestCase):
    def model(self):
        return {'intercept':math.log(1.2), 'home_advantage':math.log(1.2),
                'teams':{'A':{'attack':.5,'defence':-.2,'date':'2026-01-01','season':'S'},
                         'B':{'attack':.2,'defence':.1,'date':'2026-01-01','season':'S'}},
                'config':copy.deepcopy(subject.CONFIG)}

    def fixture(self):
        return {'home':'A','away':'B','date':'2026-07-01','season':'S','neutral':False}

    def test_global_defaults_cannot_change_loaded_model(self):
        model = self.model(); before = json.dumps(model, sort_keys=True)
        expected = subject.rates(model, self.fixture())
        with patch.dict(subject.CONFIG, {'shrink_days':30., 'season_retention':.1}):
            np.testing.assert_array_equal(subject.rates(model,self.fixture()), expected)
        self.assertEqual(json.dumps(model, sort_keys=True), before)

    def test_two_serialized_configs_do_not_interfere(self):
        a = self.model(); b = copy.deepcopy(a); b['config']['shrink_days'] = 30.
        x = subject.rates(a,self.fixture()); y = subject.rates(b,self.fixture())
        self.assertFalse(np.array_equal(x,y)); np.testing.assert_array_equal(x,subject.rates(a,self.fixture()))

    def test_fit_owns_config_copy(self):
        row={'match_id':'synthetic','cohort':'eng.1','stage':'regular','date':'2026-01-01',
             'home':'A','away':'B','home_goals':2,'away_goals':0,'neutral':False,'season':'S'}
        cfg=copy.deepcopy(subject.CONFIG)
        # Isolate the numerical fit from the already tested source/scope boundary.
        with patch.object(subject,'prepare',return_value=[row]):
            model=subject.fit([row],cfg)
        cfg['shrink_days']=30.
        self.assertEqual(model['config']['shrink_days'],365.)
        self.assertIsNot(model['config'],subject.CONFIG)

    def test_explicit_support_not_global_mutation(self):
        with patch.dict(subject.CONFIG,{'max_goals':30}):
            self.assertEqual(subject.distribution([1.7,1.1])[0].shape,(81,81))
            self.assertEqual(subject.distribution([1.7,1.1],max_goals=30)[0].shape,(31,31))

    def test_default_math_unchanged(self):
        model=self.model(); r=subject.rates(model,self.fixture())
        retain=math.exp(-181/365)
        np.testing.assert_allclose(r,[math.exp(math.log(1.2)+math.log(1.2)+.6*retain),1.2],rtol=1e-14)

    def test_invalid_config_rejected_at_fit_boundary(self):
        cfg=copy.deepcopy(subject.CONFIG);cfg['shrink_days']=0
        with self.assertRaises(ValueError):subject.fit([],cfg)


if __name__=='__main__':unittest.main()
