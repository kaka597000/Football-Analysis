"""Update an isolated league state from newly finished days and replay each before updating."""

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from itertools import groupby
import json
from pathlib import Path

import numpy as np

from train_prior_team_strength import signature
from research_program import prior_team_strength as model
from research_program.future_validation import read, write_new
from research_program.kpi_score_candidate import artifact_identity
from research_program.market_baseline import quote_only_matrices
from research_program.paired_kpi_inference import compare
from research_program.score_form import WINDOW, poisson_matrix


def append_form(recent, row):
    xg = row.get('xg_understat') or row.get('xg') or {}
    teams = recent.setdefault(row['cohort'], {})
    for own, other in (('home','away'),('away','home')):
        values = teams.setdefault(row[own], [])
        values.append({'date':row['game_date'], 'gf':row[own+'_goals'], 'ga':row[other+'_goals'],
                       'xgf':xg.get(own), 'xga':xg.get(other)})
        del values[:-WINDOW]


def simple(picks):
    if picks is None:
        return {}
    return {'primary':picks['primary'], 'top3':picks['top3'], 'wdl':picks['wdl'],
            'total_side':(picks.get('total') or {}).get('direction')}


def main(args):
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    model_state, form = read(args.state), read(args.form_artifact)
    for artifact in (model_state, form):
        if artifact_identity(artifact) != artifact['artifact_sha256']:
            raise ValueError('input artifact identity mismatch')
    if signature(args.history) != model_state['source_sha256']:
        raise ValueError('history does not match the fitted state')
    with args.history.open() as handle:
        history = sorted((json.loads(line) for line in handle), key=lambda r:r['game_date'])
    states = {k:model.LeagueStrength.restore(v) for k,v in model_state['states'].items()}
    forward_states = {k:model.LeagueStrength.restore(v) for k,v in model_state['states'].items()}
    recent = {}
    for row in history:
        append_form(recent,row)
    forward_recent = deepcopy(recent)
    selected = read(args.heads)
    if selected['strength'] != model_state['selected_strength']:
        raise ValueError('policy selected for a different team-strength arm')
    model_state.update(heads=selected['heads'], recent=recent, team_map=read(args.team_map),
                       six_match={k:form[k] for k in ('form_goals','form_xg')},
                       form_sha256=form['artifact_sha256'], parent_artifact_sha256=model_state['artifact_sha256'],
                       selected_heads_sha256=signature(args.heads), update_sha256=signature(args.update))
    updates = sorted(read(args.update),key=lambda r:(r['game_date'],r['match_id']))
    if len({r['match_id'] for r in updates}) != len(updates):
        raise ValueError('duplicate match in state update')
    ledger = []
    for iso, rows in groupby(updates,key=lambda r:r['game_date']):
        day = date.fromisoformat(iso)
        day_rows = list(rows)
        grouped, forward_groups = defaultdict(list), defaultdict(list)
        for row in day_rows:
            if row['score_basis'] != 'regulation_90' or row['stage'] != 'regular':
                raise ValueError('regular league regulation scores required')
            cohort, home, away = (row[k] for k in ('cohort','home','away'))
            state = states[cohort]
            rates = state.rates(state.coefficients(day),home,away)
            team = poisson_matrix(rates[:,model.ARMS.index(model_state['selected_strength'])],model.SHAPE)
            old = model.six_match_matrix(model_state,cohort,home,away,day)
            market = row.get('market')
            line = market['total']['line'] if market else None
            team_picks = model.selections(team,line)
            incumbent = model.selections(old,line) if old is not None else None
            head_picks = model.allocate_picks(incumbent,team_picks,model_state['heads'])
            forecasts = {'strength':simple(team_picks),'six_match_xg':simple(incumbent),
                         'independent_heads':simple(head_picks),'issued_comprehensive':row.get('issued') or {}}
            market_rate = None
            if market:
                _, full = quote_only_matrices(market['h2h'],market['total'])
                h,a = np.indices(full.shape)
                market_rate = [(full*h).sum(),(full*a).sum()]
                forecasts['market'] = simple(model.selections(full,line))
                clipped = full[:team.shape[0],:team.shape[1]]
                blended = .75 * clipped / clipped.sum() + .25*team
                forecasts['market_plus_strength'] = simple(model.selections(blended,line))
            forecasts['deployed_research_rule'] = forecasts[
                ('market_plus_strength' if model_state['use_market_mix'] else 'market') if market
                else 'independent_heads']
            goals = [row['home_goals'],row['away_goals']]
            ledger.append({'id':row['match_id'],'cohort':cohort,'date':iso,'actual':goals,'line':line,
                           'forecast':forecasts})
            xg = row.get('xg')
            # The new xG archive was checked after this weekend. Only the future
            # state may use it, even when the replay's next target was a day later.
            grouped[cohort].append((home,away,model.observations(goals,market_rate)))
            forward_groups[cohort].append((home,away,model.observations(
                goals,market_rate,[xg['home'],xg['away']] if xg else None)))
        for cohort,games in grouped.items():
            states[cohort].update_day(day,games)
            forward_states[cohort].update_day(day,forward_groups[cohort])
        for row in day_rows:
            append_form(recent,dict(row, xg=None, xg_understat=None))
            append_form(forward_recent,row)
    model_state.update(states={k:v.snapshot() for k,v in forward_states.items()},
                       recent=forward_recent,built_at=datetime.now(timezone.utc).isoformat())
    model_state['artifact_sha256'] = artifact_identity(model_state)
    write_new(out/'forward-artifact.json',model_state)
    with (out/'recent-paired.jsonl').open('x') as handle:
        for row in ledger:
            handle.write(json.dumps(row,separators=(',',':'))+'\n')
    if ledger:
        write_new(out/'recent-comparisons.json',
                  {base+'_vs_'+candidate:compare(ledger,baseline=base,candidate=candidate)
                   for base,candidate in (('market','independent_heads'),('six_match_xg','independent_heads'),
                                          ('issued_comprehensive','independent_heads'),
                                          ('issued_comprehensive','deployed_research_rule'))})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('state','history','form-artifact','heads','team-map','update','out'):
        parser.add_argument('--'+name,type=Path,required=True)
    main(parser.parse_args())
