import json
import unittest

from research_program import draw_preserving_forward as forward
from research_program import score_dependence_forward as r21
from research_program import test_territory_forward as fixtures
from research_program.goal_state_study import sha


class DrawPreservingForwardTests(unittest.TestCase):
    def test_same_parents_real_line_missing_line_and_immutable_rank(self):
        scene = fixtures.TerritoryForwardTests()
        scene.setUp()
        self.addCleanup(scene.doCleanups)
        parent_study = scene.study
        study = scene.root / 'draw-study'
        study.mkdir()
        inputs, _ = r21.source._document(parent_study / 'inputs.json')
        def save(path, value):
            path.write_text(json.dumps(value), encoding='utf-8')
        save(parent_study / 'inputs.json', dict(inputs, protocol_sha256=r21.PROTOCOL_SHA))
        save(study / 'inputs.json', dict(protocol_sha256=forward.PROTOCOL_SHA,
                                         r21_inputs_sha256=sha(parent_study / 'inputs.json')))
        doses = dict(context=dict(score=0.5, wdl=1., total=0.5), constant=dict(score=0., wdl=0., total=0.))
        weights = {arm: dict(version=r21.model.VERSION, features=list(fields), beta=[.25] * len(fields))
                   for arm, fields in [('context', r21.model.FEATURES), ('constant', r21.model.FEATURES[:1])]}
        for code in forward.source.CODES:
            old = parent_study / 'main' / code
            new = study / 'main' / code
            new.mkdir(parents=True)
            save(old / 'fit.json', dict(competition=code, doses=doses, validation_through=scene.date(-11)))
            old_fit = sha(old / 'fit.json')
            save(old / 'forward.json', dict(version=r21.model.VERSION, competition=code, through=scene.date(-8),
                                            fit_sha256=old_fit, baseline_sha256=scene.baselines[code],
                                            doses=doses, fitted=weights))
            save(new / 'fit.json', dict(version=forward.model.VERSION, competition=code, dose=0. if code=='eng.1' else 1.,
                                        validation_through=scene.date(-11), r21_fit_sha256=old_fit))
        archives = [scene.archives[k] for k in ('r5','r7','r10','r11')]
        original, _ = r21.run(*archives, parent_study, scene.events, scene.out, scene.root/'r21-ranks',
                              freeze_root=scene.store)
        out, seats = scene.root/'draw-firsts', scene.root/'draw-seats'
        def issue():
            return forward.run(*archives, study, parent_study, scene.out, scene.events, out, seats,
                               freeze_root=scene.store)
        plan, rank = issue()
        read = forward.source.ledger._read
        identity = read(out, plan)['payload']['identity']
        self.assertEqual(identity['r21_registration'], original['sha256'])
        self.assertEqual(identity['already_started'], ['test-expired'])
        registered = forward.source.FirstIssues.load(out)
        previous = forward.source.FirstIssues.load(scene.out)
        for mid, entry in registered.matches.items():
            payload = read(out, entry)['payload']
            parent = read(scene.out, previous.matches[mid])
            self.assertEqual(payload['r21_first_issue'], parent['record_sha256'])
            self.assertEqual(payload['forecasts']['r21'], parent['payload']['forecasts']['candidate'])
            self.assertEqual(payload['forecasts']['candidate']['wdl'], payload['forecasts']['r7']['wdl'])
            self.assertEqual(payload['forecasts']['candidate']['total_side'], payload['forecasts']['r7']['total_side'])
            if mid == 'test-eng.1':
                self.assertEqual(payload['forecasts']['candidate'], payload['forecasts']['r7'])
            if mid == 'test-fra.1':
                self.assertIsNone(payload['line'])
        self.assertEqual(read(seats, rank)['payload']['identity']['source_registration'], plan['sha256'])
        self.assertEqual(issue(), (plan, rank))
        self.assertFalse(list(scene.store.rglob('000002.json')))
        old_path = scene.out / next(iter(previous.matches.values()))['file']
        malformed = json.loads(old_path.read_bytes())
        malformed['payload']['line'] = 7.5
        save(old_path, malformed)
        with self.assertRaises(ValueError):
            issue()
