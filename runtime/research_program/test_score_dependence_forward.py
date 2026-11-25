import json
import unittest

from research_program import score_dependence as model
from research_program import score_dependence_forward as issuance
from research_program import test_territory_forward as fixture


class DependenceForwardTests(unittest.TestCase):
    def test_originals_rank_and_retry_with_missing_source_and_prices(self):
        scene = fixture.TerritoryForwardTests()
        scene.setUp()
        self.addCleanup(scene.doCleanups)
        inputs = issuance.source.read(scene.study / 'inputs.json')
        (scene.study / 'inputs.json').write_text(json.dumps(dict(inputs, protocol_sha256=issuance.PROTOCOL_SHA)))
        doses = dict(context=dict(score=0., wdl=1., total=.5), constant=dict(score=1., wdl=0., total=1.))
        fits = {arm: dict(version=model.VERSION, features=list(features), beta=[.3]*len(features))
                for arm, features in (('context', model.FEATURES), ('constant', model.FEATURES[:1]))}
        for code in issuance.source.CODES:
            folder = scene.study / 'main' / code
            fit = folder / 'fit.json'
            fit.write_text(json.dumps(dict(competition=code, doses=doses)))
            recipe = dict(version=model.VERSION, competition=code, through=scene.date(-8), fit_sha256=issuance.sha(fit),
                          baseline_sha256=scene.baselines[code], doses=doses, fitted=fits)
            (folder / 'forward.json').write_text(json.dumps(recipe))

        def run():
            return issuance.run(*(scene.archives[k] for k in ('r5','r7','r10','r11')), scene.study, scene.events,
                                scene.out, scene.root/'ranks', freeze_root=scene.store)

        plan, ranks = run()
        read_record = issuance.source.ledger._read
        identity = read_record(scene.out, plan)['payload']['identity']
        self.assertEqual(identity['already_started'], ['test-expired'])
        selection = read_record(scene.root/'ranks', ranks)['payload']['identity']
        self.assertEqual(selection['source_registration'], plan['sha256'])
        self.assertEqual(sum(map(len, selection['selected'].values())), 5)
        for code, group in selection['selected'].items():
            for seat in group:
                item = read_record(scene.out, seat['first_issue'])['payload']
                self.assertEqual(seat['primary'], item['forecasts']['r7']['primary'])
                self.assertEqual(item['process']['home']['usable'], 0)
                if code == 'fra.1':
                    self.assertIsNone(item['line'])
                    self.assertIsNone(item['forecasts']['candidate']['total_side'])
        self.assertEqual(run(), (plan, ranks))
        self.assertEqual(list(scene.store.rglob('000002.json')), [])
        changed = scene.study/'main/eng.1/forward.json'
        recipe = json.loads(changed.read_bytes())
        recipe['doses']['context']['wdl'] = 0.
        changed.write_text(json.dumps(recipe))
        with self.assertRaisesRegex(ValueError, 'validation doses'):
            run()
