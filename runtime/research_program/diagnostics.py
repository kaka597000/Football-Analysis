"""Inspect retained walk-forward matrices; descriptive, never a promotion claim."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2_test.data_contract import digest


def binary(values):
    """Calibration bias is mean probability minus observed event frequency."""
    if not values:
        return {'n': 0, 'mean_probability': None, 'event_rate': None, 'bias': None,
                'brier': None, 'ece': None, 'bins': []}
    p, y = np.asarray(values, float).T
    bins = []
    for index in range(10):
        mask = np.minimum((p * 10).astype(int), 9) == index
        if mask.any():
            bins.append({'n': int(mask.sum()), 'probability': float(p[mask].mean()),
                         'event_rate': float(y[mask].mean())})
    return {'n': len(p), 'mean_probability': float(p.mean()), 'event_rate': float(y.mean()),
            'bias': float((p-y).mean()), 'brier': float(np.mean((p-y)**2)),
            'ece': sum(b['n']*abs(b['probability']-b['event_rate']) for b in bins)/len(p),
            'bins': bins}


def inspect_matrix(matrix, actual, primary):
    m = np.asarray(matrix, float)
    if m.ndim != 2 or not np.isfinite(m).all() or (m < 0).any() or not np.isclose(m.sum(), 1., atol=1e-8):
        raise ValueError('valid normalized score matrix required')
    if len(actual) != 2 or any(type(v) is not int or not 0 <= v <= 40 for v in actual):
        raise ValueError('regulation score required')
    mode = list(map(int, np.unravel_index(m.argmax(), m.shape)))
    if primary != mode:
        raise ValueError('retained statistical Primary must equal matrix mode')
    h, a = actual
    if h >= m.shape[0] or a >= m.shape[1]:
        raise ValueError('actual outside retained support')
    x, y = np.indices(m.shape)
    wdl = [float(m[x > y].sum()), float(m[x == y].sum()), float(m[x < y].sum())]
    actual_direction = 0 if h > a else 1 if h == a else 2
    primary_direction = 0 if mode[0] > mode[1] else 1 if mode[0] == mode[1] else 2
    means = [float((m*x).sum()), float((m*y).sum())]
    events = {'home_zero': [float(m[0, :].sum()), int(h == 0)],
              'away_zero': [float(m[:, 0].sum()), int(a == 0)]}
    for threshold in (3, 4, 5):
        for label, mask, observed in [('home', x >= threshold, h >= threshold),
                                      ('away', y >= threshold, a >= threshold),
                                      ('total', x+y >= threshold, h+a >= threshold)]:
            events[f'{label}_ge_{threshold}'] = [float(m[mask].sum()), int(observed)]
    exact = mode == actual
    direction_hit = int(np.argmax(wdl)) == actual_direction
    return {'means': means, 'mean_residuals': [means[0]-h, means[1]-a],
            'total_mean_residual': sum(means)-h-a, 'primary': mode,
            'primary_total_minus_mean': sum(mode)-sum(means), 'primary_hit': exact,
            'wdl_probabilities': wdl, 'wdl_hit': direction_hit,
            'primary_direction_hit': primary_direction == actual_direction,
            'mode_miss_direction_hit': not exact and direction_hit,
            'actual_probability': float(m[h, a]),
            'actual_rank': int((m > m[h, a]).sum())+1,
            'primary_probability': float(m[tuple(mode)]), 'events': events,
            'zero_selected_but_scored': [mode[0] == 0 and h > 0, mode[1] == 0 and a > 0],
            'scoring_selected_but_zero': [mode[0] > 0 and h == 0, mode[1] > 0 and a == 0]}


def summarize(rows, name):
    values = [r['models'][name] for r in rows]
    n = len(values)
    if not n:
        return {'n': 0}
    residuals = np.asarray([v['mean_residuals'] for v in values])
    return {'n': n, 'primary_hits': sum(v['primary_hit'] for v in values),
            'wdl_hits': sum(v['wdl_hit'] for v in values),
            'primary_direction_hits': sum(v['primary_direction_hit'] for v in values),
            'mode_miss_direction_hit': sum(v['mode_miss_direction_hit'] for v in values),
            'mean_bias_home_away': residuals.mean(axis=0).tolist(),
            'mean_mae_home_away': abs(residuals).mean(axis=0).tolist(),
            'total_mean_bias': float(np.mean([v['total_mean_residual'] for v in values])),
            'total_mean_mae': float(np.mean([abs(v['total_mean_residual']) for v in values])),
            'primary_total_minus_mean': float(np.mean([v['primary_total_minus_mean'] for v in values])),
            'zero_selected_but_scored': np.asarray([v['zero_selected_but_scored'] for v in values]).sum(axis=0).tolist(),
            'scoring_selected_but_zero': np.asarray([v['scoring_selected_but_zero'] for v in values]).sum(axis=0).tolist(),
            'calibration': {key: binary([v['events'][key] for v in values]) for key in values[0]['events']},
            'primary_calibration': binary([[v['primary_probability'], v['primary_hit']] for v in values])}


def compare(rows, name, baseline='current_recipe'):
    counts = Counter()
    for row in rows:
        a, b = row['models'][baseline], row['models'][name]
        label = ('both_hit' if a['primary_hit'] and b['primary_hit'] else
                 'current_only' if a['primary_hit'] else 'candidate_only' if b['primary_hit'] else 'both_miss')
        counts[label] += 1
        counts['primary_changed'] += a['primary'] != b['primary']
        counts['wdl_changed_right'] += not a['wdl_hit'] and b['wdl_hit']
        counts['wdl_changed_wrong'] += a['wdl_hit'] and not b['wdl_hit']
    result = {key: counts[key] for key in ('both_hit', 'current_only', 'candidate_only', 'both_miss',
                                          'primary_changed', 'wdl_changed_right', 'wdl_changed_wrong')}
    result['net_primary_hits'] = counts['candidate_only']-counts['current_only']
    return result


def run(source, output):
    source, output = Path(source).resolve(), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    all_rows, manifest, summaries = [], [], {}
    for folder in sorted(source.iterdir()):
        if not (folder/'predictions.json').is_file():
            continue
        records = json.loads((folder/'predictions.json').read_text())
        report = json.loads((folder/'report.json').read_text())
        expected = {v['origin']+'.npz': v['matrix_file_sha256'] for v in report['folds']}
        arrays = {}
        for filename in sorted({p['matrix_file'] for r in records for p in r['models'].values()}):
            path = (folder/filename).resolve()
            if not path.is_relative_to(folder.resolve()):
                raise ValueError('matrix path escapes cohort')
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if expected.get(filename) != sha:
                raise ValueError('retained fold file checksum mismatch')
            with np.load(path, allow_pickle=False) as data:
                arrays[filename] = {key: data[key] for key in data.files}
            manifest.append({'path': str(path), 'sha256': sha})
        rows, seen, slots = [], set(), set()
        for record in records:
            if record['match_id'] in seen:
                raise ValueError('duplicate match')
            seen.add(record['match_id'])
            row = {key: record[key] for key in ('match_id', 'date', 'cohort', 'stage', 'home', 'away', 'actual', 'fold')}
            row['models'] = {}
            for name, prediction in record['models'].items():
                slot = (name, prediction['matrix_file'], prediction['matrix_index'])
                if slot in slots:
                    raise ValueError('duplicate matrix slot')
                slots.add(slot)
                matrix = arrays[prediction['matrix_file']][name][prediction['matrix_index']]
                if digest(matrix.tolist()) != prediction['matrix_sha256']:
                    raise ValueError('retained prediction matrix checksum mismatch')
                row['models'][name] = inspect_matrix(matrix, record['actual'], prediction['primary'])
            rows.append(row)
        names = list(rows[0]['models']) if rows else []
        summaries[folder.name] = {'n': len(rows), 'models': {name: summarize(rows, name) for name in names},
                                  'paired': {name: compare(rows, name) for name in names if name != 'current_recipe'}}
        (output/(folder.name+'.json')).write_text(json.dumps({'summary': summaries[folder.name], 'rows': rows}, ensure_ascii=False, indent=2, allow_nan=False))
        all_rows.extend(rows)
    if not all_rows:
        raise ValueError('no retained predictions')
    names = list(all_rows[0]['models'])
    summary = {'schema': 'WCPP-WALK-FORWARD-ERROR-DIAGNOSTICS-1', 'n': len(all_rows),
               'source': str(source), 'cohorts': summaries,
               'aggregate': {name: summarize(all_rows, name) for name in names},
               'paired': {name: compare(all_rows, name) for name in names if name != 'current_recipe'},
               'matrix_files': manifest,
               'claims': {'prospective_validation': False, 'causal_error_decomposition': False,
                          'realizable_oracle_gain': False, 'active_model_changed': False},
               'notice': 'Mean residuals, event calibration and mode selection are overlapping descriptive diagnostics, not additive causes. Candidate-only/current-only are observed historical pairs, not future gains. No realized historical market line is inferred.'}
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
    lines = ['# 七联赛逐场误差诊断', '', '历史重构诊断；不是未来未见成绩。均值、零球、尾部与众数误差可重叠，不能相加为因果归因。', '',
             '|联赛|模型|n|Top1|WDL|比分错/方向对|总球均值偏差|众数总球−均值|零球偏差主/客|总3+/4+/5+偏差|',
             '|---|---|---:|---:|---:|---:|---:|---:|---|---|']
    for cohort, summary_co in summaries.items():
        for name, model in summary_co['models'].items():
            cal = model['calibration']
            zero = '/'.join(f"{cal[k]['bias']:+.3f}" for k in ('home_zero','away_zero'))
            tails = '/'.join(f"{cal[f'total_ge_{t}']['bias']:+.3f}" for t in (3,4,5))
            lines.append(f"|{cohort}|{name}|{model['n']}|{model['primary_hits']}|{model['wdl_hits']}|{model['mode_miss_direction_hit']}|{model['total_mean_bias']:+.3f}|{model['primary_total_minus_mean']:+.3f}|{zero}|{tails}|")
    lines.extend(['', '概率偏差=预测概率均值−实测比例；正值表示高估。JSON保留每场均值残差、零球、单队及总球尾部、实际比分概率/名次、众数及独立WDL结果。', '',
                  '候选独有命中和当前独有命中只解释本批历史差异；不得据此构造赛后oracle收益或宣称未来增益。无历史实盘报价，大小球不设虚拟2.5盘口。'])
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    result = run(args.source, args.out)
    print(json.dumps({'n': result['n'], 'cohorts': list(result['cohorts']), 'paired': result['paired']}))
