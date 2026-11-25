"""Issue R22 against the original, matched R7 and R21 five-league firsts."""
import argparse
from functools import partial
import json
from pathlib import Path

from research.analysis_freeze import DEFAULT_ROOT, aware
from . import continuation_forward as anchor
from . import draw_preserving as model
from . import r10_shot_model as decision
from . import ranked_score_study as ranked
from . import score_dependence
from . import score_dependence_forward as r21
from . import territory_forward as source
from .paired_kpi_inference import _decisions


ROOT = Path(__file__).resolve().parents[2]
TRACK = 'research-r22-draw-preserving-20261125'
PROTOCOL = ROOT / 'research/20261125-r22-draw-preserving/protocol.json'
PROTOCOL_SHA = '71ffd263c1d1835ef008d594f39e6e6112c0902b2b872887ab080fb443ea594c'
COMPARATORS = ('r7', 'r21', 'market', 'projected_r21_dose', 'unprotected_matched')


def configure(study, events, protocol, archives, *, r21_study, earlier):
    spec, protocol_hash = source._document(protocol)
    inputs, input_hash = source._document(study / 'inputs.json')
    weights, old_identity, manifests = r21.configure(r21_study, events, r21.PROTOCOL, archives)
    registered = earlier.registration['payload']
    if (protocol_hash != PROTOCOL_SHA or inputs['protocol_sha256'] != protocol_hash
            or spec['version'] != model.VERSION or tuple(spec['competitions']) != source.CODES
            or inputs['r21_inputs_sha256'] != old_identity['study_inputs_sha256']
            or registered['track'] != r21.TRACK
            or any(registered['identity'].get(key) != value for key, value in old_identity.items())):
        raise ValueError('R22 protocol, study or source R21 registration differs')
    for mid, entry in earlier.matches.items():
        record = source.ledger._read(earlier.folder, entry)
        earlier.match(registered['fixtures'][mid], aware(record['recorded_at']))
    identity = dict(version=model.VERSION, formal_kpi_eligible=False, competitions=list(source.CODES),
                    pairs={arm + '_vs_candidate': [arm, 'candidate'] for arm in COMPARATORS},
                    protocol_sha256=protocol_hash, study_inputs_sha256=input_hash,
                    events_sha256=old_identity['events_sha256'],
                    archive_manifest_sha256=old_identity['archive_manifest_sha256'],
                    r21_study_inputs_sha256=old_identity['study_inputs_sha256'],
                    r21_registration=earlier.registration['record_sha256'],
                    r21_recipe_sha256=old_identity['recipe_sha256'], recipe_sha256={})
    recipes = {}
    for code in source.CODES:
        fit, fit_hash = source._document(study / 'main' / code / 'fit.json')
        parent = weights[code]
        dose = fit['dose']
        if (fit['competition'] != code or fit['version'] != model.VERSION
                or type(dose) not in (float, int) or dose not in decision.DOSES
                or fit['r21_fit_sha256'] != parent['fit_sha256']
                or fit['validation_through'] != source.read(r21_study / 'main' / code / 'fit.json')['validation_through']
                or fit['validation_through'] >= parent['through']):
            raise ValueError('R22 fitted dose, validation date or R21 weights differ: ' + code)
        recipes[code] = dict(competition=code, dose=dose, through=parent['through'],
                             baseline_sha256=parent['baseline_sha256'], r21=parent)
        identity['recipe_sha256'][code] = fit_hash
    return recipes, identity, manifests


def forecast(fixture, parents, league, recipe, identity, cutoff, *, earlier):
    baseline, history, provenance = league
    game, raw, original, previous = source.verified_parents(fixture, parents, baseline, recipe, cutoff)
    old = earlier.match(fixture, cutoff)
    carried = old['payload']
    prices = raw['payload']
    control = original['payload']['forecasts']['candidate']
    code = fixture['cohort']
    if (carried['version'] != score_dependence.VERSION or carried['r7_first_issue'] != original['record_sha256']
            or carried['odds_source_first_issue'] != raw['record_sha256']
            or carried['r10_first_issue'] != previous['record_sha256']
            or carried['recipe_sha256'] != identity['r21_recipe_sha256'][code]
            or carried['training_through'] != recipe['through'] or carried['forecasts']['r7'] != control
            or carried['line'] != prices['line'] or carried['prices'] != prices['prices']
            or carried['market_evidence'] != prices['market_evidence']
            or carried['forecasts']['market'] != prices['forecasts']['market']):
        raise ValueError('R21 first is attached to different inputs: ' + fixture['match_id'])
    day = cutoff.date().isoformat()
    x, exposure = history.context(game, day)
    if exposure['cutoff'] != day or any(entry['date'] >= day for side in ('home', 'away')
                                        for entry in exposure[side]['games']):
        raise ValueError('R22 team history reaches the issue cutoff')
    prior = anchor.matrix(game, baseline)
    parent = recipe['r21']
    full = score_dependence.matrix(prior, x, parent['fitted']['context'])
    picks, matrices = model.choices(prior, full, recipe['dose'], parent['doses']['context']['score'],
                                    control, prices['line'])
    picks.update(r7=control, r21=carried['forecasts']['candidate'], market=prices['forecasts']['market'])
    for pick in picks.values():
        _decisions(pick, None, prices['line'])
    cells = [tuple(map(int, s.split('-'))) for s in picks['candidate']['top3']]
    probs = [float(matrices['candidate'][cell]) for cell in cells]
    return dict(version=model.VERSION, formal_kpi_eligible=False, home_name=game['home'], away_name=game['away'],
                forecasts=picks, line=prices['line'], prices=prices['prices'], market_evidence=prices['market_evidence'],
                features=x.tolist(), process=exposure, score_dose=recipe['dose'],
                r21_score_dose=parent['doses']['context']['score'], quality=dict(p_first=probs[0], p_three=sum(probs)),
                training_through=recipe['through'], history_cutoff=day, baseline_sha256=recipe['baseline_sha256'],
                recipe_sha256=identity['recipe_sha256'][code], r21_recipe_sha256=identity['r21_recipe_sha256'][code],
                protocol_sha256=identity['protocol_sha256'], events_sha256=identity['events_sha256'],
                odds_cutoff_at=raw['fixture']['cutoff_at'], odds_source_first_issue=raw['record_sha256'],
                r7_first_issue=original['record_sha256'], r21_first_issue=old['record_sha256'], **provenance)


def run(r5, r7, r10, r11, study, r21_study, r21_firsts, events, out, ranks, *, freeze_root=DEFAULT_ROOT):
    earlier = source.FirstIssues.load(r21_firsts)
    plan = source.run(r5, r7, r10, r11, study, events, PROTOCOL, out, freeze_root=freeze_root, track=TRACK,
                      configure=partial(configure, r21_study=r21_study, earlier=earlier),
                      forecast=partial(forecast, earlier=earlier), history_type=score_dependence.Design,
                      population_index=1)
    seats = ranked.lock(out, ROOT / 'configs/analysis-scope.json', ROOT / 'project-goals.json',
                        ranks, freeze_root=freeze_root)
    return plan, seats


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('r5', 'r7', 'r10', 'r11', 'study', 'r21-study', 'r21-firsts', 'events', 'out', 'ranks'):
        parser.add_argument('--' + field, type=Path, required=True)
    parser.add_argument('--freeze-root', type=Path, default=Path(DEFAULT_ROOT))
    print(json.dumps(run(**vars(parser.parse_args())), ensure_ascii=False))
