"""Fit, choose once on 2024/25, then replay locked goal-state forecasts on other seasons."""

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from research.analysis_freeze import digest
from .paired_kpi_inference import compare

FIVE = ('eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1')
PANELS = {'selection': (FIVE, '2425'), 'known': (FIVE, '2526'),
          'dutch': (('ned.1',), '2526'), 'english_second': (('eng.2',), '2526'),
          'portuguese': (('por.1',), '2526')}


def read_lines(path):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def write_new(path, obj):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(obj, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def training_game(row):
    return dict(match_id=row['match_id'], date=row['game_date'], cohort=row['cohort'],
                home=row['home'], away=row['away'], actual=[row['home_goals'], row['away_goals']],
                goals=[dict(half=g['half'], seconds=g['event_sec'], side=g['side']) for g in row['goals']])


def prices(row):
    # Both feeds contain archived category labels, without bookmaker quote clocks.
    if 'market_paths' in row:
        book = row['market_paths'].get('Bet365') or {}
        close = book.get('close') or {}
        return close.get('h2h'), close.get('total')
    book = (row.get('market') or {}).get('bet365') or {}
    close = book.get('close') or {}
    return close.get('h2h'), close.get('total_2_5')


def forecast_row(row, artifact, names, *, retained_quote=False):
    from . import goal_state_process as model
    from .prior_team_strength import selections

    h, a = row['home_goals'], row['away_goals']
    if row['score_basis'] != 'regulation_90' or type(h) != int or type(a) != int or min(h, a) < 0:
        raise ValueError('only finished regulation fixtures accepted in historical replay')
    if retained_quote:
        ref = {'original_freeze_sha256': row['freeze_sha256']}
        book = row['market']['bookmaker']
        h2h, total = row['market']['h2h'], row['market']['total']
    else:
        ref = row.get('source') or {k: row[k] for k in ('source_file', 'source_line', 'source_sha256')}
        book = 'Bet365'
        h2h, total = prices(row)
    result = dict(id=row['match_id'], cohort=row['cohort'], date=row['game_date'], home=row['home'],
                  away=row['away'], actual=[h, a], line=total['line'] if total else None,
                  quote={'bookmaker': book, 'h2h': h2h, 'total': total}, source=ref, forecast={})
    if not h2h or not total:
        return result
    matrices = model.matrices(artifact, h2h, total, row['game_date'])
    for name in names:
        m = matrices[name]
        pred = selections(m, total['line'])
        high = (np.add.outer(np.arange(len(m)), np.arange(len(m))) >= 4)
        top3_mass = sum(m[tuple(map(int, v.split('-')))] for v in pred['top3'])
        settlements = pred['total']['settlements'][pred['total']['direction']]
        result['forecast'][name] = dict(primary=pred['primary'], top3=pred['top3'], wdl=pred['wdl'],
            total_side=pred['total']['direction'], total_line=total['line'],
            p_primary=pred['primary_probability'], p_top3=float(top3_mass), p_wdl=pred['wdl_probabilities'][pred['wdl']],
            p_fullwin=settlements['win'], p_push=settlements['push'], p_four_plus=float(m[high].sum()),
            p_zero=float(m[0, 0]), p_actual=float(m[h, a]))
    return result


def diagnostics(rows, name):
    paired = [(r['forecast'][name], r['actual']) for r in rows if name in r['forecast']]
    if not paired:
        return {'n': 0, 'joint_log_loss': None}
    actual_four = sum(sum(s) >= 4 for _, s in paired)
    return dict(n=len(paired), four_plus_n=actual_four,
                mean_p_four_plus=np.mean([p['p_four_plus'] for p, _ in paired]).item(),
                brier_four_plus=np.mean([(p['p_four_plus'] - int(sum(s) >= 4))**2 for p, s in paired]).item(),
                mean_p_00=np.mean([p['p_zero'] for p, _ in paired]).item(),
                observed_00=sum(sum(s) == 0 for _, s in paired),
                mean_p_first=np.mean([p['p_primary'] for p, _ in paired]).item(),
                mean_p_top3=np.mean([p['p_top3'] for p, _ in paired]).item(),
                mean_p_wdl=np.mean([p['p_wdl'] for p, _ in paired]).item(),
                implied_total_nonpush=sum(p['p_fullwin'] for p, _ in paired)/sum(1-p['p_push'] for p, _ in paired),
                four_plus_top3_hits=sum(sum(s) >= 4 and f'{s[0]}-{s[1]}' in p['top3'] for p, s in paired),
                four_plus_top3_offered=sum(any(sum(map(int, v.split('-'))) >= 4 for v in p['top3'])
                                          for p, _ in paired),
                joint_log_loss=np.mean([-math.log(max(p['p_actual'], 1e-300)) for p, _ in paired]).item())


def run_panel(path, archive, panel, out, names, policy=None):
    from . import goal_state_process as model

    recent = panel == 'recent'
    leagues, season = (FIVE, '2627') if recent else PANELS[panel]
    rows = []
    with (out / (panel + '-predictions.jsonl')).open('x', encoding='utf-8') as stream:
        source = json.loads(path.read_bytes()) if recent else read_lines(path)
        for r in source:
            if (r['cohort'] not in leagues or (not recent and r.get('season_id', r.get('season')) != season)
                    or r['stage'] != 'regular'):
                continue
            row = forecast_row(r, archive, ('market', *model.CHALLENGERS) if policy else names, retained_quote=recent)
            if policy and row['forecast']:
                arms = row['forecast']
                row['forecast'] = {n: arms[n] for n in names}
                row['forecast']['composite'] = model.combine_heads(arms, policy)
            if recent:
                row['forecast']['issued'] = r['issued']
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')
            rows.append(row)
    paired = {name: compare(rows, baseline='market', candidate=name) for name in names if name != 'market'}
    if policy:
        paired['composite'] = compare(rows, baseline='market', candidate='composite')
    if recent:
        paired['composite_vs_original'] = compare(rows, baseline='issued', candidate='composite')
        paired['vs_original'] = compare(rows, baseline='issued', candidate=names[-1])
    stats = {name: diagnostics(rows, name) for name in names}
    result = dict(panel=panel, season=season, coverage=dict(Counter(r['cohort'] for r in rows)),
                  source_sha256=sha(path), artifact_sha256=digest(archive), comparisons=paired, diagnostics=stats)
    write_new(out / (panel + '-report.json'), result)
    return result, rows


def select_heads(result, rows, names):
    from . import goal_state_process as model

    def hits(name, metric):
        comparison = next(iter(result['comparisons'].values())) if name == 'market' else result['comparisons'][name]
        return comparison['tracks'][name]['metrics'][metric]['hits']
    policy = {key: max(names, key=lambda name: hits(name, metric)) for key, metric in
              [('first', 'score_top1'), ('wdl', 'wdl'), ('total', 'actual_ou')]}
    def backup_hits(name):
        return sum(f"{r['actual'][0]}-{r['actual'][1]}" in model.combine_heads(r['forecast'], dict(policy, backups=name))['top3']
                   for r in rows if r['forecast'])
    scores = {name: backup_hits(name) for name in names}
    policy['backups'] = max(names, key=scores.get)
    return policy, scores


def command(args):
    from . import goal_state_process as model

    out = args.out
    out.mkdir(exist_ok=True, parents=True)
    if args.phase == 'fit':
        games = [training_game(r) for r in read_lines(args.inputs)
                 if r.get('training_eligible') is True and r['cohort'] in FIVE
                 and r['score_basis'] == 'regulation_90' and r['stage'] == 'regular']
        model_file = model.fit(games, '2024-07-01', sha(args.inputs))
        write_new(out / 'artifact.json', model_file)
        return
    artifact = json.loads((out / 'artifact.json').read_text())
    if args.phase == 'selection':
        names = ('market', *model.CHALLENGERS)
        result, rows = run_panel(args.inputs, artifact, 'selection', out, names)
        def order(name):
            stats = result['comparisons'][name]['tracks'][name]['metrics']
            return (*[stats[k]['hits'] for k in ('score_top1', 'score_top3', 'wdl', 'actual_ou')],
                    -result['diagnostics'][name]['joint_log_loss'])
        choice = max(model.CHALLENGERS, key=order)
        policy, scores = select_heads(result, rows, names)
        write_new(out / 'choice.json', dict(selected=choice, heads=policy, conditional_backup_hits=scores, artifact_sha256=digest(artifact),
                  selection_report_sha256=sha(out / 'selection-report.json')))
    else:
        choice = json.loads((out / 'choice.json').read_text())
        if (choice['artifact_sha256'] != digest(artifact)
                or choice['selection_report_sha256'] != sha(out / 'selection-report.json')):
            raise ValueError('selected artifact or selection results have changed')
        run_panel(args.inputs, artifact, args.phase, out, ('market', choice['selected']), choice['heads'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['fit', 'recent', *PANELS])
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    command(parser.parse_args())
