"""Freeze R21 on the unstarted part of the original five-league R7 population."""
import argparse
import json
from pathlib import Path

from research.analysis_freeze import DEFAULT_ROOT
from . import all_scope_model as base
from . import continuation_forward as anchor
from . import r10_shot_model as decision
from . import ranked_score_study as ranked
from . import score_dependence as model
from . import territory_forward as source
from .goal_state_study import sha
from .paired_kpi_inference import _decisions


TRACK = 'research-r21-score-dependence-20261125'
ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / 'research/20261125-r21-score-dependence/protocol.json'
PROTOCOL_SHA = 'a4eb968e0965882c0844235fb0e9448a25a172f4a4ea8f16ec6d5e490439ed52'
PAIRS = {arm + '_vs_candidate': [arm, 'candidate'] for arm in ('r7', 'r10', 'market', 'constant', 'constant_matched')}
PAIRS['fixed_dependence_increment'] = ['fixed_constant', 'fixed_context']


def configure(study, events, protocol, archives):
    spec, stamp = source._document(protocol)
    inputs, inputs_hash = source._document(study / 'inputs.json')
    manifests, hashes = {}, {}
    for key, folder in archives.items():
        manifests[key], hashes[key] = source._document(folder / 'manifest.json')
    if (spec['version'] != model.VERSION or tuple(spec['competitions']) != source.CODES
            or stamp != PROTOCOL_SHA or inputs['protocol_sha256'] != stamp
            or inputs['events_sha256'] != sha(events) or inputs['extension_manifest_sha256'] != hashes['r11']):
        raise ValueError('R21 protocol or original study inputs disagree')
    identity = dict(version=model.VERSION, competitions=list(source.CODES), formal_kpi_eligible=False, pairs=PAIRS,
                    protocol_sha256=stamp, study_inputs_sha256=inputs_hash, events_sha256=inputs['events_sha256'],
                    extension_manifest_sha256=hashes['r11'], archive_manifest_sha256=hashes, recipe_sha256={})
    recipes = {}
    for code in source.CODES:
        folder = study / 'main' / code
        recipe, recipe_hash = source._document(folder / 'forward.json')
        history = source.read(folder / 'fit.json')
        if (recipe['competition'] != code or recipe['version'] != model.VERSION
                or recipe['fit_sha256'] != sha(folder / 'fit.json') or recipe['doses'] != history['doses']
                or set(recipe['fitted']) != {'context', 'constant'} or set(recipe['doses']) != set(recipe['fitted'])):
            raise ValueError('R21 recipe, validation doses or training fit differs: ' + code)
        for arm, names in (('context', model.FEATURES), ('constant', model.FEATURES[:1])):
            weights, doses = recipe['fitted'][arm], recipe['doses'][arm]
            if (weights['features'] != list(names) or weights['version'] != model.VERSION
                    or set(doses) != {'score', 'wdl', 'total'}
                    or any(type(v) not in (float, int) or v not in decision.DOSES for v in doses.values())):
                raise ValueError('R21 fit has unknown features or doses: ' + code)
        identity['recipe_sha256'][code] = recipe_hash
        recipes[code] = recipe
    return recipes, identity, manifests


def forecast(fixture, parents, league, recipe, identity, cutoff):
    baseline, history, provenance = league
    game, raw, published, previous = source.verified_parents(fixture, parents, baseline, recipe, cutoff)
    odds = raw['payload']
    control = published['payload']['forecasts']['candidate']
    day = cutoff.date().isoformat()
    x, detail = history.context(game, day)
    if detail['cutoff'] != day or any(entry['date'] >= day for s in ('home','away') for entry in detail[s]['games']):
        raise ValueError('R21 npxG history reaches its issue cutoff')
    grid = anchor.matrix(game, baseline)
    alternatives = {arm: model.matrix(grid, x, weights) for arm, weights in recipe['fitted'].items()}
    doses = recipe['doses']
    line = odds['line']
    picks = dict(r7=control, r10=previous['payload']['forecasts']['candidate'], market=odds['forecasts']['market'])
    picks['candidate'], heads = decision.decisions(grid, alternatives['context'], doses['context'], line, control)
    picks['constant'], _ = decision.decisions(grid, alternatives['constant'], doses['constant'], line, control)
    picks['constant_matched'], _ = decision.decisions(grid, alternatives['constant'], doses['context'], line, control)
    for arm, matrix in alternatives.items():
        picks['fixed_' + arm] = base.pick(matrix, line)
    for tips in picks.values():
        _decisions(tips, None, line)
    cells = [tuple(map(int, s.split('-'))) for s in picks['candidate']['top3']]
    probs = [float(heads['score'][cell]) for cell in cells]
    return dict(version=model.VERSION, formal_kpi_eligible=False, home_name=game['home'], away_name=game['away'],
                forecasts=picks, line=line, prices=odds['prices'], market_evidence=odds['market_evidence'],
                features=x.tolist(), process=detail, doses=doses, quality=dict(p_first=probs[0], p_three=sum(probs)),
                training_through=recipe['through'], history_cutoff=day, fit_sha256=recipe['fit_sha256'],
                baseline_sha256=recipe['baseline_sha256'], recipe_sha256=identity['recipe_sha256'][fixture['cohort']],
                protocol_sha256=identity['protocol_sha256'], events_sha256=identity['events_sha256'],
                odds_cutoff_at=raw['fixture']['cutoff_at'], odds_source_first_issue=raw['record_sha256'],
                r7_first_issue=published['record_sha256'], r10_first_issue=previous['record_sha256'], **provenance)


def run(r5, r7, r10, r11, study, events, out, ranks, *, freeze_root=DEFAULT_ROOT):
    plan = source.run(r5, r7, r10, r11, study, events, PROTOCOL, out, freeze_root=freeze_root,
                      track=TRACK, configure=configure, forecast=forecast, history_type=model.Design,
                      population_index=1)
    selection = ranked.lock(out, ROOT / 'configs/analysis-scope.json', ROOT / 'project-goals.json',
                            ranks, freeze_root=freeze_root)
    return plan, selection


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('r5', 'r7', 'r10', 'r11', 'study', 'events', 'out', 'ranks'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--freeze-root', type=Path, default=Path(DEFAULT_ROOT))
    args = parser.parse_args()
    print(json.dumps(run(**vars(args)), ensure_ascii=False))
