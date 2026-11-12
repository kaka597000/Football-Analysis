import copy
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np
from analysis_freeze import save, verify, digest
from score_distribution_audit import (audit_prediction, attach_pair, comprehensive_audit,
                                      pair_disagreement)


def poisson_pair():
    p = np.array([math.exp(-1.8)*1.8**i/math.factorial(i) for i in range(20)])
    p /= p.sum()
    return {'primary': [1,1], 'matrix': np.outer(p,p).tolist()}


class DistributionAuditTests(unittest.TestCase):
    def test_low_mode_is_not_small_total_distribution(self):
        prediction = poisson_pair(); before = copy.deepcopy(prediction)
        audit = audit_prediction(prediction)
        self.assertEqual(audit['joint_mode'], '1-1')
        self.assertAlmostEqual(audit['total_mean'], 3.6, places=9)
        self.assertGreater(audit['p_total_ge_3'], .6)
        self.assertEqual(audit['total_mode'], 3)
        self.assertIn('LOW_JOINT_MODE_WITH_MAJORITY_MASS_AT_THREE_PLUS', audit['flags'])
        self.assertEqual(audit['comprehensive_selection']['status'], 'NOT_PROVIDED')
        self.assertEqual(prediction, before)
        self.assertFalse(audit['primary_reordered'])

    def test_rectangular_axes_and_selected_zero_probability(self):
        audit = audit_prediction({'matrix': [[.1,.2,.3],[.1,.1,.2]]}, '0-2')
        np.testing.assert_allclose(audit['total_probabilities'], [.1,.3,.4,.2])
        self.assertAlmostEqual(audit['total_mean'], 1.7)
        self.assertAlmostEqual(audit['p_home_zero'], .6)
        self.assertAlmostEqual(audit['p_away_zero'], .2)
        selected = audit['comprehensive_selection']
        self.assertAlmostEqual(selected['model_probability'], .3)
        self.assertEqual(selected['probability_rank'], 1)
        self.assertAlmostEqual(selected['home_zero_comparison']['p1'], .4)
        outside = audit_prediction({'matrix': [[1.]]}, '1-0')
        self.assertEqual(outside['comprehensive_selection']['status'], 'OUTSIDE_MATRIX_SUPPORT')
        self.assertNotIn('model_probability', outside['comprehensive_selection'])

    def test_missing_abstain_tamper_and_atomic_attach(self):
        for p in [{'status':'ABSTAIN'}, {'primary': '1-0'}]:
            self.assertEqual(audit_prediction(p)['status'], 'UNAVAILABLE')
        for matrix in [[[.2]], [[-1,2]], [[float('nan')]]]:
            with self.assertRaises(ValueError):
                audit_prediction({'matrix':matrix})
        with self.assertRaises(ValueError):
            audit_prediction({'matrix':[[1]], 'matrix_hash':'not-a-hash'})
        pair = {'current_model':poisson_pair(),'test_model':{'matrix':[[.2]]}}
        before = copy.deepcopy(pair)
        with self.assertRaises(ValueError): attach_pair(pair)
        self.assertEqual(pair,before)

    def test_current_test_disagreement_is_diagnostic_only(self):
        same = {k: poisson_pair() for k in ('current_model', 'test_model')}
        before = copy.deepcopy(same)
        result = pair_disagreement(same)
        self.assertEqual(same, before)
        self.assertEqual(result['status'], 'READY')
        self.assertFalse(result['primary_disagreement'])
        self.assertAlmostEqual(result['jensen_shannon_nats'], 0)
        self.assertFalse(result['changes_primary'])
        self.assertFalse(result['formal_kpi_eligible'])
        different = copy.deepcopy(same)
        different['test_model']['matrix'] = np.fliplr(
            np.asarray(different['test_model']['matrix'])).tolist()
        different['test_model']['primary'] = [1, 2]
        result = pair_disagreement(different)
        self.assertTrue(result['primary_disagreement'])
        self.assertGreater(result['jensen_shannon_nats'], 0)
        attach_pair(different)
        self.assertEqual(different['current_test_disagreement'], result)

    def test_disagreement_requires_common_support(self):
        pair = {'current_model': {'primary': '0-0', 'matrix': [[1.0]]},
                'test_model': {'primary': '0-0', 'matrix': [[.5, .5]]}}
        with self.assertRaisesRegex(ValueError, 'identical score support'):
            pair_disagreement(pair)

    def evidence(self):
        return {'compared_scores':['1-0','1-1','2-1'],
                'zero_vs_one':{'away':{'reason':'synthetic support','counterevidence':'synthetic counterevidence'}},
                'three_plus_path':'synthetic chase path',
                'total_reason':'synthetic independent actual-line analysis'}

    def test_comparison_gate_preserves_manual_independence(self):
        pair = {k:poisson_pair() for k in ('current_model','test_model')}
        payload = {'primary':'1-0','total_direction':'小'}
        self.assertFalse(comprehensive_audit(payload,pair)['top3_ready'])
        payload['selection_evidence']=self.evidence()
        self.assertTrue(comprehensive_audit(payload,pair)['top3_ready'])
        self.assertEqual(payload['primary'],'1-0')
        self.assertEqual(pair['current_model']['primary'],[1,1])
        payload['selection_evidence']['zero_vs_one']['away']['counterevidence']=''
        self.assertFalse(comprehensive_audit(payload,pair)['top3_ready'])
        payload['selection_evidence']=[]
        with self.assertRaises(ValueError): comprehensive_audit(payload,pair)

    def fixture(self):
        now=datetime.now(timezone.utc)
        return {'match_id':'synthetic-audit-test',
                'kickoff_at':(now+timedelta(hours=2)).isoformat(),
                'cutoff_at':now.isoformat()}

    def refresh(self, fixture):
        fixture=copy.deepcopy(fixture)
        fixture['cutoff_at']=datetime.now(timezone.utc).isoformat()
        return fixture

    def test_freeze_checks_reference_time_hash_identity_and_top3(self):
        with tempfile.TemporaryDirectory() as root:
            fixture=self.fixture()
            pair={k:poisson_pair() for k in ('current_model','test_model')}
            pair_ref=save(pair, fixture, stage='prematch', track='current-test-pair', root=root)
            payload={'primary':'1-0','wdl':'主胜','total_direction':'小','total_line':2.5,
                     'current_test_reference':pair_ref}
            fixture=self.refresh(fixture)
            original=copy.deepcopy(payload)
            comp_ref=save(payload,fixture,stage='prematch',track='comprehensive',root=root)
            record=verify(json.loads(Path(comp_ref['path']).read_text()))
            self.assertFalse(record['selection_audit']['top3_ready'])
            self.assertEqual(record['payload'],original)
            item={k:payload[k] for k in ('primary','wdl','total_direction','total_line')}
            item.update(rank=1, match_id=fixture['match_id'], comprehensive_reference=comp_ref)
            batch={'top3':[item]}; batch_fixture=self.refresh(fixture);batch_fixture['match_id']='synthetic-batch'
            with self.assertRaisesRegex(ValueError,'resolved score'):
                save(batch,batch_fixture,stage='prematch',track='top3-selection',root=root)
            # A supported zero score remains permitted; there is no mandatory +1 rule.
            payload['selection_evidence']=self.evidence()
            fixture=self.refresh(fixture)
            good=save(payload,fixture,stage='prematch',track='comprehensive',root=root)
            item['comprehensive_reference']=good
            batch_fixture=self.refresh(batch_fixture)
            receipt=save(batch,batch_fixture,stage='prematch',track='top3-selection',root=root)
            self.assertEqual(verify(json.loads(Path(receipt['path']).read_text()))['selection_audit']['n'],1)
            item['primary']='2-1'
            with self.assertRaisesRegex(ValueError,'differs'):
                save(batch,self.refresh(batch_fixture),stage='prematch',track='top3-selection',root=root)
            for key,value in [('sha256','bad'),('path',comp_ref['path'])]:
                bad=copy.deepcopy(payload);bad['current_test_reference'][key]=value
                with self.assertRaises(ValueError):
                    save(bad,self.refresh(fixture),stage='prematch',track='comprehensive',root=root)
            wrong_time=copy.deepcopy(fixture); wrong_time['cutoff_at']='2000-01-01T00:00:00+00:00'
            with self.assertRaisesRegex(ValueError,'unavailable'):
                save(payload,wrong_time,stage='prematch',track='comprehensive',root=root)
            wrong_id=self.refresh(fixture);wrong_id['match_id']='another-match'
            with self.assertRaisesRegex(ValueError,'identity'):
                save(payload,wrong_id,stage='prematch',track='comprehensive',root=root)
            # Old records without new metadata retain their exact hash contract.
            old={'schema':'WCPP-ISSUED-FREEZE-1','payload':{'primary':'0-0'}}
            old['record_sha256']=digest(old)
            self.assertEqual(verify(old),old)

    def candidate_fixture(self):
        return dict(self.fixture(), competition='uefa.nations',
                    cohort='uefa.nations:A:league-phase', home='espn:1',
                    away='espn:2', phase='PreMatch')

    def candidate_pair(self, fixture):
        pair = {'active_route': False, 'formal_kpi_eligible': False}
        for role in ('current', 'test'):
            prediction = poisson_pair()
            prediction.update(role=role, version='synthetic-'+role,
                              artifact_sha256='a'*64,
                              matrix_hash=digest(prediction['matrix']),
                              input=copy.deepcopy(fixture),
                              generated_at=datetime.now(timezone.utc).isoformat(),
                              status='TRAINED_CANDIDATE_RESEARCH_ONLY',
                              formal_kpi_eligible=False)
            pair[role] = prediction
        return pair

    def research_payload(self, reference):
        return {'primary': '1-0', 'wdl': '主胜', 'total_direction': '小',
                'total_line': 2.5, 'candidate_reference': reference,
                'selection_scope': 'candidate_research',
                'selection_evidence': self.evidence()}

    def test_candidate_research_top3_is_explicit_and_separate(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = self.candidate_fixture()
            pair = self.candidate_pair(fixture)
            before = copy.deepcopy(pair)
            ref = save(pair, fixture, stage='prematch',
                       track='nations-paired-candidates', root=root)
            raw = Path(ref['path']).read_bytes()
            payload = self.research_payload(ref)
            comp = save(payload, self.refresh(fixture), stage='prematch',
                        track='comprehensive', root=root)
            audit = verify(json.loads(Path(comp['path']).read_text()))['selection_audit']
            self.assertTrue(audit['top3_ready'])
            self.assertFalse(audit['formal_kpi_eligible'])
            self.assertFalse(audit['active_model_promotion'])
            item = {k: payload[k] for k in ('primary', 'wdl', 'total_line', 'total_direction')}
            item.update(rank=1, match_id=fixture['match_id'], comprehensive_reference=comp)
            batch = {'top3': [item]}
            batch_fixture = dict(self.refresh(fixture), match_id='synthetic-batch')
            with self.assertRaisesRegex(ValueError, 'scope'):
                save(batch, batch_fixture, stage='prematch', track='top3-selection', root=root)
            batch['selection_scope'] = 'candidate_research'
            receipt = save(batch, batch_fixture, stage='prematch', track='top3-selection', root=root)
            record = verify(json.loads(Path(receipt['path']).read_text()))
            self.assertFalse(record['selection_audit']['formal_kpi_eligible'])
            self.assertEqual(pair, before)
            self.assertEqual(Path(ref['path']).read_bytes(), raw)
            # A candidate reference alone cannot silently activate a new path.
            del payload['selection_scope']
            receipt = save(payload, self.refresh(fixture), stage='prematch',
                           track='comprehensive', root=root)
            self.assertFalse(json.loads(Path(receipt['path']).read_text())['selection_audit']['top3_ready'])

    def test_candidate_research_requires_identity_chronology_and_original_matrix(self):
        mutations = [
            lambda p: p['test'].update(role='current'),
            lambda p: p.update(active_route=True),
            lambda p: p['test'].update(matrix_hash='b'*64),
            lambda p: p['test']['input'].update(home='espn:99'),
            lambda p: p['test']['input'].update(cohort='uefa.nations:B:league-phase'),
            lambda p: p['test'].update(generated_at='2100-01-01T00:00:00Z'),
            lambda p: p['test'].update(status='ABSTAIN'),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate), tempfile.TemporaryDirectory() as root:
                fixture = self.candidate_fixture()
                pair = self.candidate_pair(fixture)
                mutate(pair)
                ref = save(pair, fixture, stage='prematch', track='nations-paired-candidates', root=root)
                with self.assertRaises(ValueError):
                    save(self.research_payload(ref), self.refresh(fixture),
                         stage='prematch', track='comprehensive', root=root)

    def test_research_comparisons_and_abstentions_keep_existing_gate(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = self.candidate_fixture()
            ref = save(self.candidate_pair(fixture), fixture, stage='prematch',
                       track='nations-paired-candidates', root=root)
            for primary, evidence in [('1-0', {}), (None, self.evidence())]:
                payload = self.research_payload(ref)
                payload.update(primary=primary, selection_evidence=evidence)
                receipt = save(payload, self.refresh(fixture), stage='prematch',
                               track='comprehensive', root=root)
                audit = json.loads(Path(receipt['path']).read_text())['selection_audit']
                self.assertFalse(audit['top3_ready'])


if __name__=='__main__': unittest.main()
