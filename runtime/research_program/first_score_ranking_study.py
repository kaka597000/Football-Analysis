"""Train competition-specific seat rankings and evaluate them on untouched later R7 slates."""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory

from . import first_score_ranking as model
from .continuation_study import put
from .goal_state_study import sha, write_new
from .paired_kpi_inference import _actual, _bootstrap, _decisions, _line, compare
from .r8_score_study import read


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / 'research/20261125-r16-selection/protocol.json'
SCOPE = ROOT / 'configs/analysis-scope.json'
RULES = ('raw', 'calibrated')
DRAWS, SEED = 2000, 20261125


def panel(r7, files, code):
    name = 'models/' + code + '.tar.gz'
    if sha(r7 / name) != files[name]:
        raise ValueError('original R7 archive checksum differs for ' + code)
    with tarfile.open(r7 / name) as tar:
        packed = tar.extractfile('main/forecasts.jsonl.gz').read()
    rows = [json.loads(b) for b in gzip.decompress(packed).splitlines()]
    seen = set()
    for row in rows:
        model.day(row['date'])
        actual = _actual(row)
        if (not row['id'] or row['id'] in seen or row['cohort'] != code or not row['stage'] or actual is None
                or row['window'] not in ('evaluation', 'recent')):
            raise ValueError('invalid original R7 population in ' + code)
        _decisions(row['forecast']['candidate'], actual, _line(row))
        seen.add(row['id'])
    return sorted(rows, key=lambda r: (r['date'], r['id'])), hashlib.sha256(packed).hexdigest()


def design(row):
    return model.from_first(row['forecast'], row['quality']['candidate'])


def label(row):
    outcome = '-'.join(map(str, row['actual']))
    return outcome == row['forecast']['candidate']['primary']


def fit(rows):
    weights = model.fit([(design(r), label(r)) for r in rows])
    dates = sorted(r['date'] for r in rows)
    return dict(weights, first_date=dates[0] if dates else None, through=dates[-1] if dates else None)


def predictions(rows, weights):
    groups = defaultdict(list)
    for row in rows:
        x = design(row)
        pick = row['forecast']['candidate']
        groups[row['date']].append(dict(id=row['id'], primary=pick['primary'], top3=pick['top3'],
            actual=row['actual'], raw=x['p_first'], calibrated=model.predict(x, weights), features=x['features']))
    slates = []
    for date, group in sorted(groups.items()):
        by_id = {r['id']: r for r in group}
        selections = {rule: model.select(group, rule) for rule in RULES}
        outcomes = {}
        for rule, ids in selections.items():
            seats = []
            for mid in ids:
                original = by_id[mid]
                score = '-'.join(map(str, original['actual']))
                seats.append(dict(id=mid, primary=original['primary'], first_hit=original['primary'] == score,
                                  ordered_three_hit=score in original['top3']))
            outcomes[rule] = dict(seats=seats, sweep=all(s['first_hit'] for s in seats) if len(seats) == 3 else None)
        slates.append(dict(date=date, cohort=rows[0]['cohort'], population=len(group), fixtures=group,
                           selected=selections, results=outcomes))
    return slates


def summarize(slates):
    full = [g for g in slates if g['population'] >= 3]
    result = dict(batches=len(slates), full_batches=len(full), short_batches=len(slates)-len(full),
                  scheduled_slots=3*len(slates), filled_slots=sum(min(3,g['population']) for g in slates),
                  fixtures=sum(g['population'] for g in slates), rules={})
    for rule in RULES:
        counts = []
        for seat in range(3):
            entries = [g['results'][rule]['seats'][seat] for g in slates if g['population'] > seat]
            counts.append(dict(seat=seat + 1, filled=len(entries), first_hits=sum(x['first_hit'] for x in entries),
                               ordered_three_hits=sum(x['ordered_three_hit'] for x in entries)))
        sweeps = sum(g['results'][rule]['sweep'] for g in full)
        result['rules'][rule] = dict(seats=counts, first_hits=sum(x['first_hits'] for x in counts),
            ordered_three_hits=sum(x['ordered_three_hits'] for x in counts), complete_sweeps=sweeps,
            sweep_rate_complete=sweeps/len(full) if full else None, sweep_rate_all=sweeps/len(slates) if slates else None)
    gained = lost = 0
    blocks = defaultdict(lambda: [0, 0])
    for g in full:
        delta = int(g['results']['calibrated']['sweep']) - int(g['results']['raw']['sweep'])
        gained += delta > 0
        lost += delta < 0
        block = blocks[g['date']]
        block[0] += delta
        block[1] += 1
    result['paired'] = dict(gained=gained, lost=lost, tied=len(full)-gained-lost, net=gained-lost,
        changed_members=sum(len(set(g['selected']['raw']) - set(g['selected']['calibrated'])) for g in slates),
        changed_seat_positions=sum(a != b for g in slates for a,b in zip(g['selected']['raw'], g['selected']['calibrated'], strict=True)),
        date_clusters=len(blocks), ci95=_bootstrap(blocks, DRAWS, SEED, .95),
        discordant_complete_batches=gained+lost)
    seat_blocks = defaultdict(lambda: [0, 0])
    gains = losses = 0
    for g in slates:
        raw, calibrated = (g['results'][rule]['seats'] for rule in RULES)
        for a,b in zip(raw, calibrated, strict=True):
            delta = int(b['first_hit']) - int(a['first_hit'])
            gains += delta > 0
            losses += delta < 0
            seat_blocks[g['date']][0] += delta
            seat_blocks[g['date']][1] += 1
    result['descriptive_paired_seat_first'] = dict(gained=gains, lost=losses, net=gains-losses,
        eligible=result['filled_slots'], ci95=_bootstrap(seat_blocks, DRAWS, SEED, .95))
    return result


def study(rows, as_of):
    recent = [r for r in rows if r['window'] == 'recent']
    start = min((r['date'] for r in recent), default=None)
    prior = [r for r in rows if r['window'] == 'evaluation' and start and r['date'] < start]
    weights = fit(prior)
    slates = predictions(recent, weights)
    metrics = summarize(slates)
    metrics.update(evaluation_training_rows=len(prior), recent_from=start,
                   historical_evaluation_rows=sum(r['window']=='evaluation' for r in rows))
    metrics['same_fixtures'] = compare(recent, baseline='market', candidate='candidate', draws=DRAWS, seed=SEED) if recent else None
    future_fit = fit([r for r in rows if r['date'] < as_of])
    return dict(evaluation=weights, forward=future_fit), metrics, slates, recent


def run(r7, out, as_of):
    model.day(as_of)
    if out.exists():
        raise FileExistsError(out)
    if read(PROTOCOL)['version'] != model.VERSION:
        raise ValueError('unexpected ranking protocol')
    codes = [v['code'] for v in read(SCOPE)['competitions']]
    files = read(r7 / 'manifest.json')['files']
    metrics, weights, inputs, days, all_rows = {}, {}, {}, [], []
    for code in codes:
        rows, member_sha = panel(r7, files, code)
        recipe, results, slates, recent = study(rows, as_of)
        inputs[code] = dict(archive=files['models/' + code + '.tar.gz'], forecast_member=member_sha)
        weights[code], metrics[code] = recipe, results
        days.extend(slates)
        all_rows.extend(recent)
    summary = summarize(days)
    summary['same_fixtures'] = compare(all_rows, baseline='market', candidate='candidate', draws=DRAWS, seed=SEED)
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent) as tmp:
        dest = Path(tmp) / 'study'
        dest.mkdir()
        put(dest/'ranked-slates.jsonl.gz', days)
        write_new(dest/'weights.json', weights)
        write_new(dest/'metrics.json', dict(aggregate=summary, competitions=metrics, formal_stage_certified=False))
        write_new(dest/'inputs.json', dict(version=model.VERSION, protocol_sha256=sha(PROTOCOL), scope_sha256=sha(SCOPE),
            forward_training_before=as_of, r7_manifest_sha256=sha(r7/'manifest.json'), inputs=inputs))
        dest.rename(out)
    return {k:summary[k] for k in ('fixtures','batches','full_batches','rules','paired')}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--r7', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--forward-training-before', required=True)
    args = p.parse_args()
    print(json.dumps(run(args.r7, args.out, args.forward_training_before), ensure_ascii=False))
