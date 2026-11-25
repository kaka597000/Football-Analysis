"""Replay the frozen R10 independent folds using the pinned secondary Understat copy.

Only aggregate results leave memory. Supply the source tree's runtime in PYTHONPATH.
The default recovers the public v4 ZIP. --archive accepts identical local bytes.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import tarfile
import tempfile
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np


from research_program import territory_history as th
from research_program import territory_hurdle as hurdle
from research_program import territory_study as study
from research_program import continuation_scores as r7
from research_program import r8_score_study as control
from research_program import r10_shot_model as decision


URL = ('https://www.kaggle.com/api/v1/datasets/download/slehkyi/'
       'extended-football-stats-for-european-leagues-xg?datasetVersionNumber=4')
PIN = '430b2c76bd3607c6bc5b4430e803b95fb3da52332d09577a18712e4e432210ac'
PROTOCOL_SHA256 = 'aa3409f7ac03cb0893d2ef445be8ce78858676344a6d1355747f4aa4b9a22a9d'
ARCHIVES = {'inputs/independent.tar.gz': 'f5dafa480a2d7b5dc63dd911bcc326e291cd1dacb0e42054eff4f0bd166047ce',
            'replay/independent.tar.gz': '3e14e7b4660705614efe8090f0afb6aee02ea88ce156e6907440a127113dc5df'}
BOUNDS = ('2017-07-01', '2018-07-01', '2019-07-01')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(root):
    if root is None:
        return None
    paths = [root / 'inputs.json', root / 'metrics.json']
    for league in study.CODES:
        paths.extend(root / 'main' / league / name for name in ('fit.json', 'forward.json', 'forecasts.jsonl.gz'))
    return {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in paths}


def verified_tar(root, key):
    expected = json.loads((root / 'manifest.json').read_text())['files'][key]
    path = root / key
    if expected != ARCHIVES[key] or digest(path.read_bytes()) != expected:
        raise ValueError('R10 archive digest mismatch: ' + key)
    return tarfile.open(path)


def load_source(path):
    if path is None:
        req = urllib.request.Request(URL, headers={'User-Agent': 'R14-registered-sensitivity/1.0'})
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read()
    else:
        raw = path.read_bytes()
    if digest(raw) != PIN:
        raise ValueError('Kaggle v4 digest mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        member = archive.read('understat_per_game.csv')
    groups = defaultdict(list)
    for row in csv.DictReader(io.TextIOWrapper(io.BytesIO(member), encoding='utf-8-sig')):
        if row['league'] in th.LEAGUES and row['year'] in ('2015', '2016', '2017', '2018'):
            groups[th.LEAGUES[row['league']]].append(row)
    return groups, digest(member)


def alias_names(code):
    lookup = defaultdict(set)
    for group in th._SPELLINGS.get(code, ()):
        keys = set(map(th._name_key, group))
        for key in keys:
            lookup[key].update(keys)
    return lookup


def pair_fixtures(rows, games, code):
    """Resolve both names and venue first. Match fields below contain no measurements."""
    index = defaultdict(list)
    side_records = []
    for i, row in enumerate(rows):
        day = row['date'][:10]
        side = {'h': 'home', 'a': 'away'}[row['h_a']]
        name = row['team']
        index[day, side].append((i, name))
        # The secondary has no provider IDs. Raw club spelling is a league-local key.
        side_records.append({'competition': code, 'date': day, side: name, side + '_id': name})
    fixture_keys = [{k: g[k] for k in ('competition', 'date', 'home', 'away')} for g in games]
    _, prior = th.TerritoryHistory(side_records, code).walk(fixture_keys)
    aliases = alias_names(code)
    proposals, usage = [], Counter()
    audit = Counter()
    clock_counts = Counter(r['date'] for r in rows)
    for gi, (g, meta) in enumerate(zip(fixture_keys, prior, strict=True)):
        chosen, methods = [], []
        for side in th.SIDES:
            key = th._name_key(g[side])
            spellings = aliases.get(key, set()) | {key}
            field = index[g['date'], side]
            direct = [(i, n) for i, n in field if th._name_key(n) in spellings]
            old_id = meta[side + '_id']
            if old_id is not None:
                eligible = [(i, n) for i, n in field if n == old_id]
                if direct and not any(n == old_id for _, n in direct):
                    eligible = []
                elif len(direct) > 1 and meta[side + '_method'] != 'calendar':
                    eligible = []
                method = meta[side + '_method']
            elif meta[side + '_method'] == 'ambiguous':
                eligible, method = [], 'ambiguous'
            else:
                eligible = direct
                method = 'fixture_name'   # first source appearance may enter history AFTER today
            if len(eligible) != 1:
                break
            chosen.append(eligible[0][0])
            methods.append(method)
        if len(chosen) != 2:
            audit['unresolved_name_or_side'] += 1
        elif rows[chosen[0]]['date'] != rows[chosen[1]]['date']:
            audit['source_clock_disagreement'] += 1
        elif rows[chosen[0]]['team'] == rows[chosen[1]]['team']:
            audit['same_source_team'] += 1
        else:
            proposals.append((gi, *chosen, methods))
            usage.update(chosen)
    accepted = []
    for gi, hi, ai, methods in proposals:
        if usage[hi] != 1 or usage[ai] != 1:
            audit['nonunique_row_assignment'] += 1
            continue
        accepted.append((games[gi], rows[hi], rows[ai]))
        audit.update('accepted_side_' + m for m in methods)
        audit['joined_at_multi_fixture_timestamp'] += clock_counts[rows[hi]['date']] > 2
    audit.update(source_team_rows=len(rows), target_games=len(games), joined_fixtures=len(accepted),
                 unmatched_target_games=len(games) - len(accepted), unused_source_sides=len(rows) - 2 * len(accepted))
    return accepted, audit


def metric(row, name, count=False):
    try:
        v = float(row[name])
    except (KeyError, TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v >= 0 and (not count or v.is_integer()) else None


def evidence(pairs, code):
    events, audit = [], Counter()
    for g, h, a in pairs:
        # Internal join key is explicitly the R10 ID, never represented as an Understat ID.
        event = dict(competition=code, date=h['date'], source_match_id='r10:' + g['id'],
                     home=h['team'], away=a['team'], home_id=h['team'], away_id=a['team'], source_sha256=PIN)
        for side, own, opp in [('home', h, a), ('away', a, h)]:
            for out, field, mirror in [('xg', 'xG', 'xGA'), ('deep', 'deep', 'deep_allowed'),
                                       ('ppda_att', 'ppda_att', 'oppda_att'), ('ppda_def', 'ppda_def', 'oppda_def')]:
                value, check = (metric(r, f, field != 'xG') for r, f in [(own, field), (opp, mirror)])
                if value is None or check is None:
                    audit[out + '_missing_or_invalid'] += 1
                    value = None
                elif not math.isclose(value, check, rel_tol=1e-9, abs_tol=1e-10):
                    audit[out + '_mirror_disagreement'] += 1
                    value = None
                event[side + '_' + out] = value
                if value is not None:
                    audit[out + '_mirror_verified_sides'] += 1
            att, actions = (event[side + '_ppda_' + k] for k in ('att', 'def'))
            if att is None or actions is None or actions <= 0:
                audit['unusable_ppda_sides'] += 1
                event[side + '_ppda_att'] = event[side + '_ppda_def'] = None
            else:
                audit['paired_count_ppda_sides'] += 1
                coef = metric(own, 'ppda_coef')
                if coef is not None and not math.isclose(coef, att / actions, rel_tol=1e-5, abs_tol=1e-6):
                    audit['reported_coefficient_disagrees_with_counts'] += 1
        events.append(event)
    return events, audit


def source_history(rows, pairs, code):
    """Keep unpaired team appearances in the last-eight window with zero metric exposure."""
    events, audit = evidence(pairs, code)
    used = {(r['date'], r['h_a'], r['team']) for _, home, away in pairs for r in (home, away)}
    for row in rows:
        if (row['date'], row['h_a'], row['team']) in used:
            continue
        side = {'h': 'home', 'a': 'away'}[row['h_a']]
        events.append({'competition': code, 'date': row['date'], side: row['team'],
                       side + '_id': row['team'], 'source_sha256': PIN})
        audit['calendar_only_unjoined_team_rows'] += 1
    if 2 * len(pairs) + audit['calendar_only_unjoined_team_rows'] != len(rows):
        raise ValueError('secondary team appearances were duplicated or lost')
    return events, audit


def coverage(metadata):
    out = {'games': len(metadata), 'any_history': sum(m['available'] for m in metadata)}
    for name in th.METRICS:
        counts = [[m[s + '_exposure'][name] for s in th.SIDES] for m in metadata]
        out[name] = dict(both_teams=sum(min(c) > 0 for c in counts), any_team=sum(max(c) > 0 for c in counts),
                         historical_side_exposures=sum(sum(c) for c in counts))
    return out


def replay(code, rows, inputs, frozen):
    panel = inputs.extractfile('panels/' + code + '.jsonl').read()
    games = sorted(map(json.loads, panel.splitlines()), key=lambda g: (g['date'], g['id']))
    base = f'independent/{code}/'
    raw_control = frozen.extractfile(base + 'r7-control.json').read()
    fit10 = json.load(frozen.extractfile(base + 'fit.json'))
    if digest(panel) != fit10['panel_sha256'][0] or list(BOUNDS) != fit10['bounds']:
        raise ValueError('independent panel or bounds mismatch')
    refs = list(map(json.loads, gzip.decompress(frozen.extractfile(base + 'forecasts.jsonl.gz').read()).splitlines()))
    cut, test = [sum(g['date'] < b for g in games) for b in BOUNDS[:2]]
    if (cut, test - cut) != (fit10['train_games'], fit10['validation_games']) or len(refs) != len(games) - test:
        raise ValueError('independent fold sizes mismatch')
    if not all(g['independent_split_role'] == role for group, role in
               ((games[:cut], 'train'), (games[cut:test], 'weight_selection'), (games[test:], 'test')) for g in group):
        raise ValueError('archived calendar and registered roles disagree')
    if len({g['id'] for g in games}) != len(games) or games[-1]['date'] >= BOUNDS[2]:
        raise ValueError('duplicate target or out-of-window fixture')
    join, identity = pair_fixtures(rows, games, code)
    events, fields = source_history(rows, join, code)
    history = th.TerritoryHistory(events, code)
    x, meta = history.walk(games)
    if np.max(np.abs(x)) > 1 or not np.isfinite(x).all():
        raise ValueError('invalid design')
    for g, m in zip(games, meta, strict=True):
        if any(d >= g['date'] for s in th.SIDES for d in m[s + '_history_dates']):
            raise ValueError('same-day history reached a target')
    anchor, z, _ = r7.inputs(games, code)
    settings = json.loads(raw_control)
    priors = control.matrices(anchor[cut:], z[cut:], settings)
    train_y = np.asarray([(g['home_goals'], g['away_goals']) for g in games[:cut]])
    fits, doses = {}, {}
    val = priors[:test - cut]
    for arm in ('xg', 'full'):
        fits[arm] = hurdle.fit(anchor[:cut], x[:cut], train_y, xg_only=arm == 'xg')
        alternative = [hurdle.apply(p, f, fits[arm]) for p, f in zip(val, x[cut:test], strict=True)]
        doses[arm], _ = decision.choose(games[cut:test], val, alternative)
    scores = []
    for i, ref in enumerate(refs, start=test):
        g = games[i]
        if (g['id'], code, g['date'], g['home'], g['away'], g['source_sha256']) != (
                ref['id'], ref['cohort'], ref['date'], ref['home'], ref['away'], ref['source_sha256']):
            raise ValueError('test reference identity mismatch')
        if [g['home_goals'], g['away_goals']] != ref['actual'] or study.base.actual_line(g) != ref['line']:
            raise ValueError('frozen evaluation label or quote differs after identity match')
        comparison = ref['forecast']
        scores.append(study.record(g, 'independent2019', priors[i-cut], x[i], meta[i], fits, doses,
                                   comparison['r7'], comparison['market'], comparison['candidate']))
    stats = dict(panel_sha256=digest(panel), r7_control_sha256=digest(raw_control), joins=identity,
                 mirrors=fields, doses=doses, r7_selection=settings['selection'], fits=fits)
    joined_ids = {g['id'] for g, _, _ in join}
    for label, start, end in [('train', 0, cut), ('validation', cut, test), ('test', test, len(games))]:
        stats[label] = dict(start=games[start]['date'], end=games[end-1]['date'], **coverage(meta[start:end]),
                            contemporaneous_source_pairs=sum(g['id'] in joined_ids for g in games[start:end]))
    return scores, stats


def run(r10, protocol, *, archive=None, main_root=None):
    """Return only deterministic aggregate output. The optional main tree is read-only."""
    sealed = snapshot(main_root)
    if digest(protocol.read_bytes()) != PROTOCOL_SHA256:
        raise ValueError('registered R14 protocol digest mismatch')
    groups, member_hash = load_source(archive)
    all_scores, detail = [], {}
    with verified_tar(r10, 'inputs/independent.tar.gz') as inputs, verified_tar(r10, 'replay/independent.tar.gz') as frozen:
        for code in study.CODES:
            scores, detail[code] = replay(code, groups[code], inputs, frozen)
            all_scores.extend(scores)
            print(code, 'test fixtures', len(scores), flush=True)
    report = study.summarize(all_scores)
    result = dict(version='R14-secondary-Kaggle-v4-independent-1', source_url=URL, archive_sha256=PIN,
                  csv_member='understat_per_game.csv', csv_sha256=member_hash,
                  source_clock='Unzoned Understat source calendar day. No historical publication timestamp.',
                  source_identity='No provider IDs or opponent column. R10 ordered fixture names and league/date, '
                                  'with unique normalized names, documented league aliases or prior calendar links. '
                                  'Exact source timestamp agreement and one-to-one use of both source rows required.',
                  eligibility='Both source sides must be uniquely identified. Each statistic needs a valid own value '
                              'and agreeing opponent mirror. Pressing uses only raw paired integer att >= 0, def > 0. '
                              'Unpaired source team rows occupy their actual last-eight calendar slots without metrics. '
                              'Every original test fixture remains eligible for scoring.',
                  calibration='Per league, train both R14 hurdles on pre-date R7 input anchors in 2015/16–2016/17. '
                              'For validation and test use the R10 independent r7-control.json selection and fitted R7 '
                              'distribution. The 2017/18 validation alone chooses score, WDL and total doses. '
                              'Test 2018/19 once. No fitted parameters from modern R14.',
                  cold_start='Sources join only to supplied independent calendar beginning in 2015/16.',
                  split_dates=BOUNDS, r10_archives=ARCHIVES, protocol_sha256=PROTOCOL_SHA256,
                  leagues=detail, metrics=report)
    if snapshot(main_root) != sealed:
        raise ValueError('main changed during sensitivity')
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--r10', type=Path, required=True, help='R10 release root containing manifest.json, inputs/, replay/')
    ap.add_argument('--protocol', type=Path, required=True, help='registered R14 protocol.json')
    ap.add_argument('--out', type=Path, required=True, help='aggregate JSON file, or directory for early-aggregate.json')
    ap.add_argument('--main', type=Path, help='optional main replay root to checksum before and after, e.g. r14-final')
    ap.add_argument('--archive', type=Path, help='optional local copy of the pinned v4 ZIP')
    args = ap.parse_args(argv)
    out = args.out if args.out.suffix.lower() == '.json' else args.out / 'early-aggregate.json'
    roots = [args.r10, *([args.main] if args.main else [])]
    if any(out.resolve().is_relative_to(p.resolve()) for p in roots) or out.resolve() == args.protocol.resolve():
        ap.error('aggregate output must be separate from the supplied inputs')
    result = run(args.r10, args.protocol, archive=args.archive, main_root=args.main)
    content = json.dumps(result, indent=2, allow_nan=False, sort_keys=True) + '\n'
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', dir=out.parent, delete=False) as tmp:
        tmp.write(content)
    Path(tmp.name).replace(out)
    print('Aggregate SHA-256', digest(out.read_bytes()), flush=True)


if __name__ == '__main__':
    main()
