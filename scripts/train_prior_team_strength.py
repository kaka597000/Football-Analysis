"""Replay prior-match team ratings, select on one season, and emit a forward state."""

import argparse
from collections import defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
import json
from pathlib import Path

import numpy as np
from scipy.stats import poisson

from train_kpi_candidate import DIRECTIONS, read_examples, score_arrays, signature
from research_program import kpi_score_candidate as form
from research_program import prior_team_strength as strength
from research_program.future_validation import read, write_new
from research_program.paired_kpi_inference import compare


def historical_markets(source, rows, cache, jobs):
    ids = [r['match_id'] for r in rows if r.get('market')]
    filename = cache / 'market.npy'
    meta = {'source_hash': signature(source), 'n': len(ids), 'matrix_version': 'WCPP-MARKET-ONLY-1',
            'ids': ids}
    if filename.exists():
        if read(cache / 'market.json') != meta:
            raise ValueError('market cache identities or source bytes differ')
        matrices = np.load(filename, mmap_mode='r', allow_pickle=False)
    else:
        cache.mkdir(parents=True, exist_ok=True)
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            matrices = np.stack(list(pool.map(form.quotes_matrix, (r for r in rows if r.get('market')),
                                              chunksize=64)))
        np.save(filename, matrices, allow_pickle=False)
        write_new(cache / 'market.json', meta)
    if matrices.shape != (len(ids), *form.SHAPE):
        raise ValueError('unexpected market matrix shape')
    return dict(zip(ids, matrices))


def replay(rows, updates, lag_days=0):
    """Release one whole day of completed matches only after its date becomes eligible."""
    rates = np.empty((len(rows), 2, len(strength.ARMS)))
    states = defaultdict(strength.LeagueStrength)
    pending = deque()

    def release():
        day, groups = pending.popleft()
        for league, games in groups.items():
            states[league].update_day(day, games)

    for iso, group in groupby(enumerate(rows), key=lambda pair: pair[1]['game_date']):
        day = date.fromisoformat(iso)
        while pending and pending[0][0] < day - timedelta(days=lag_days):
            release()
        groups, coefficients = defaultdict(list), {}
        for i, row in group:
            league = row['cohort']
            state = states[league]
            if league not in coefficients:
                coefficients[league] = state.coefficients(day)
            home, away = row['home'], row['away']
            rates[i] = state.rates(coefficients[league], home, away)
            groups[league].append((home, away, updates[i]))
        pending.append((day, groups))
    while pending:
        release()
    return rates, states


def batch_poisson(rates):
    support = np.arange(strength.SHAPE[0])
    probabilities = poisson.pmf(support, rates[:, :, None])
    matrix = probabilities[:, 0, :, None] * probabilities[:, 1, None, :]
    mass = matrix.sum(axis=(1, 2))
    if not np.isfinite(mass).all() or np.any(abs(1. - mass) > 1e-8):
        raise ValueError('material score probability outside support')
    return matrix / mass[:, None, None]


def evaluate(matrices, actual, lines, forecast_rows, arm):
    """Rows without a current market have no total. Other picks remain evaluable."""
    available = np.isfinite(matrices[:, 0, 0])
    priced = available & np.isfinite(lines)
    metrics = dict(primary=0, top3=0, wdl=0, wins=0, nonpush=0, n=int(available.sum()))
    if priced.any():
        pred = score_arrays(matrices[priced], actual[priced], lines[priced])
        top, wdl, side = pred['top'], pred['wdl'], pred['ou']
        width = matrices.shape[2]
        for j, i in enumerate(np.flatnonzero(priced)):
            forecast_rows[i]['forecast'][arm] = {
                'top3': [f'{idx//width}-{idx%width}' for idx in top[j]],
                'wdl': DIRECTIONS[wdl[j]], 'total_side': 'over' if side[j] > 0 else 'under'}
        metrics.update(primary=int(pred['exact'].sum()), top3=int(pred['three'].sum()),
                       wdl=int(pred['wdl_hit'].sum()), wins=int((pred['settlement'] == 2).sum()),
                       nonpush=int((pred['settlement'] != 0).sum()))
    for i in np.flatnonzero(available & ~priced):
        pick = strength.selections(matrices[i], None)
        forecast_rows[i]['forecast'][arm] = {k: pick[k] for k in ('top3', 'wdl')}
        home, away = actual[i]
        result = f'{home}-{away}'
        direction = 'home' if home > away else 'away' if home < away else 'draw'
        metrics['primary'] += int(pick['primary'] == result)
        metrics['top3'] += int(result in pick['top3'])
        metrics['wdl'] += int(pick['wdl'] == direction)
    matrix = matrices[available]
    if not len(matrix):
        return metrics
    home, away = actual[available].T
    gh, ga = np.indices(matrix.shape[1:])
    zero = matrix[:, 0].sum(axis=1) + matrix[:, :, 0].sum(axis=1) - matrix[:, 0, 0]
    tail = matrix[:, gh+ga >= 4].sum(axis=1)
    mode = matrix.reshape(len(matrix), -1).argmax(axis=1)
    metrics.update(logloss=float(-np.log(matrix[np.arange(len(matrix)), home, away]).mean()),
                   one_one_first=int((mode == matrix.shape[2]+1).sum()),
                   zero_side_brier=float(np.mean((zero - ((home == 0) | (away == 0)))**2)),
                   high_total_brier=float(np.mean((tail - (home+away >= 4))**2)))
    return metrics


def objective(values):
    return (values['primary'], values['top3'], values['wdl'], values['wins'] / max(1, values['nonpush']),
            -values.get('logloss', float('inf')))


def run(args):
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    examples, population = read_examples(args.source)
    rows, partitions, actual, features = zip(*examples)
    actual, partitions = np.array(actual), np.array(partitions)
    markets = historical_markets(args.source, rows, args.cache, args.jobs)
    history = read(args.form_artifact)
    if (form.artifact_identity(history) != history['artifact_sha256']
            or history['selection_end'] >= min(r['game_date'] for r in rows if r['season'] == '2526')):
        raise ValueError('incompatible six-game control')
    x = np.stack(features)
    has_history = x[:, :, form.FEATURE_NAMES.index('own_n')].min(axis=1) > 0
    has_xg = x[:, :, form.FEATURE_NAMES.index('own_xg_n')].min(axis=1) > 0
    rates_form = form.expected_form(history['form_xg'], x)
    if (~has_xg).any():
        goals_features = x[~has_xg].copy()
        form.mask_xg(goals_features)
        rates_form[~has_xg] = form.expected_form(history['form_goals'], goals_features)
    home_index, away_index = np.indices(form.SHAPE)
    updates = []
    for row, goals in zip(rows, actual):
        quoted = markets.get(row['match_id'])
        implied = None if quoted is None else [(quoted * i).sum() for i in (home_index, away_index)]
        xg = row.get('xg_understat') or row.get('xg')
        shot_quality = [xg['home'], xg['away']] if xg and all(xg.get(k) is not None for k in ('home','away')) else None
        updates.append(strength.observations(goals, implied, shot_quality))
    rates, states = replay(rows, updates)
    delayed, _ = replay(rows, updates, lag_days=7)
    result = {'population': population, 'protocol_sha256': signature(args.protocol),
              'history_sha256': signature(args.source), 'incumbent_sha256': history['artifact_sha256'],
              'periods': {}}
    chosen = None
    for part in ('select', 'known_holdout', 'final'):
        idx = np.flatnonzero(partitions == part)
        truth = actual[idx]
        data = [rows[i] for i in idx]
        lines = np.array([r['market']['total']['line'] if r.get('market') else np.nan for r in data])
        ledger = [{'id': r['match_id'], 'date': r['game_date'], 'cohort': r['cohort'],
                   'actual': score.tolist(), 'line': None if np.isnan(line) else float(line), 'forecast': {}}
                  for r, score, line in zip(data, truth, lines)]
        metrics = {}
        def measure(name, matrices):
            metrics[name] = evaluate(matrices, truth, lines, ledger, name)

        incumbent = batch_poisson(rates_form[idx])
        incumbent[~has_history[idx]] = np.nan
        measure('six_match_xg', incumbent)
        for c, name in enumerate(strength.ARMS):
            measure(name, batch_poisson(rates[idx, :, c]))
        if chosen is None:
            chosen = max(strength.ARMS, key=lambda k: objective(metrics[k]))
        chosen_col = strength.ARMS.index(chosen)
        reference = np.full((len(data), *form.SHAPE), np.nan)
        for j, row in enumerate(data):
            if row['match_id'] in markets:
                reference[j] = markets[row['match_id']]
        measure('market', reference)
        extra = strength.SHAPE[0] - form.SHAPE[0]
        padded = np.pad(reference, ((0, 0), (0, extra), (0, extra)))
        measure('market_plus_strength', .75*padded + .25*batch_poisson(rates[idx, :, chosen_col]))
        measure('seven_day_lag', batch_poisson(delayed[idx, :, chosen_col]))
        if part == 'select':
            use_mix = objective(metrics['market_plus_strength']) > objective(metrics['market'])
            result.update(selected_strength=chosen, use_market_mix=use_mix,
                          selected_no_quote_including_incumbent=max((*strength.ARMS,'six_match_xg'),
                                                                    key=lambda k: objective(metrics[k])))
            write_new(out/'selection.json', {k:v for k,v in result.items() if k != 'periods'} | {'scores':metrics})
        result['periods'][part] = metrics
        comparisons = {'strength_vs_six': ('six_match_xg', chosen), 'strength_vs_market': ('market', chosen),
                       'blend_vs_market': ('market','market_plus_strength'), 'lag_vs_strength': (chosen,'seven_day_lag')}
        paired = {k:compare(ledger, baseline=a, candidate=b) for k,(a,b) in comparisons.items()}
        with (out / (part+'-paired.jsonl')).open('x') as handle:
            for row in ledger:
                handle.write(json.dumps(row, separators=(',', ':'), allow_nan=False)+'\n')
        write_new(out/(part+'-intervals.json'), paired)

    artifact = {'version':strength.VERSION, 'selected_strength':chosen, 'use_market_mix':use_mix,
                'selection_end':max(r['game_date'] for r in rows if r['season']=='2425'),
                'built_at':datetime.now(timezone.utc).isoformat(), 'source_sha256':signature(args.source),
                'protocol_sha256':signature(args.protocol), 'team_map':{},
                'states':{k:v.snapshot() for k,v in states.items()}}
    artifact['artifact_sha256'] = form.artifact_identity(artifact)
    write_new(out/'state.json', artifact)
    write_new(out/'summary.json', result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'form-artifact', 'protocol', 'out', 'cache'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--jobs', default=4, type=int)
    run(parser.parse_args())
