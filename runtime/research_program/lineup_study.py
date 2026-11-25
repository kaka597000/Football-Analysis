"""Replay the registered J1 prior-starters study on its original R5, R7 and R10 calendars."""

import argparse
from collections import Counter, defaultdict
from datetime import timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory

import numpy as np

from research.analysis_freeze import aware
from . import all_scope_model as base
from . import continuation_scores as r7
from . import continuation_study as archive
from . import r10_shot_model as decision
from . import r8_score_study as control
from . import territory_hurdle as hurdle
from .all_scope_study import check_panel
from .goal_state_study import read_lines, sha, write_new
from .lineup_history import FEATURES, SIDES, VERSION, LineupHistory
from .paired_kpi_inference import compare
from .r9_score_study import verified_archive

CODE = 'jpn.1'
PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261125-r15-j1/protocol.json'
BOUNDS = ('2024-01-01', '2025-01-01', '2026-01-01')
MODE = 'retrospective_prior_match_rosters'
POSITIONS = {'G': 'G', 'GK': 'G', 'GOALKEEPER': 'G', 'D': 'D', 'DEFENDER': 'D',
             'M': 'M', 'MIDFIELDER': 'M', 'F': 'F', 'FORWARD': 'F'}


def fit(anchors, design, scores, *, form_only):
    """The R14 implementation's two-column switch selects team form for J1."""
    fitted = hurdle.fit(anchors, design, scores, xg_only=form_only)
    del fitted['xg_only']
    fitted.update(version=VERSION, estimator=hurdle.VERSION, form_only=form_only,
                  features=list(FEATURES[:2] if form_only else FEATURES))
    return fitted


def apply(prior, features, fitted):
    if (fitted['version'] != VERSION or fitted['estimator'] != hurdle.VERSION
            or fitted['features'] != list(FEATURES[:2] if fitted['form_only'] else FEATURES)):
        raise ValueError('unexpected J1 hurdle fit or feature order')
    return hurdle.apply(prior, features, dict(fitted, xg_only=fitted['form_only']))


def _schedule(games, r5, manifest):
    """Recover ordered team IDs from the scoreboard bytes referenced by each R5 row."""
    index_name = 'evidence/source-index.json'
    if sha(r5 / index_name) != manifest['files'][index_name]:
        raise ValueError('R5 source index checksum mismatch')
    index = control.read(r5 / index_name)
    by_source = defaultdict(list)
    for g in games:
        by_source[g['source_sha256']].append(g)
    by_archive = defaultdict(list)
    for key in by_source:
        by_archive['evidence/' + index[key]['archive']].append(key)
    for name, keys in by_archive.items():
        if sha(r5 / name) != manifest['files'][name]:
            raise ValueError('R5 scoreboard archive checksum mismatch: ' + name)
        with tarfile.open(r5 / name) as tar:
            for key in keys:
                body = gzip.decompress(tar.extractfile(index[key]['member']).read())
                if sha256(body).hexdigest() != key:
                    raise ValueError('R5 scoreboard bytes differ: ' + key)
                board = json.loads(body)
                if [league['slug'] for league in board['leagues']] != [CODE]:
                    raise ValueError('scoreboard is outside J1')
                events = {'espn:' + str(e['id']): e for e in board['events']}
                if len(events) != len(board['events']):
                    raise ValueError('duplicate event in R5 scoreboard')
                for g in by_source[key]:
                    event = events[g['id']]
                    fixture, = event['competitions']
                    sides = {s['homeAway']: s for s in fixture['competitors']}
                    status = fixture['status']['type']
                    if (str(fixture['id']) != str(event['id']) or set(sides) != {'home', 'away'} or len(fixture['competitors']) != 2
                            or status['name'] != 'STATUS_FULL_TIME' or status['completed'] is not True
                            or g['score_basis'] != 'regulation_90'
                            or aware(fixture['date']).astimezone(timezone.utc).date().isoformat() != g['date']):
                        raise ValueError('R5 regulation schedule mismatch: ' + g['id'])
                    for side, row in sides.items():
                        if row['team']['displayName'] != g[side] or int(row['score']) != g[side + '_goals']:
                            raise ValueError('R5 scoreboard side or score differs: ' + g['id'])
                        g[side + '_id'] = str(row['team']['id'])
                    if g['home_id'] == g['away_id']:
                        raise ValueError('identical R5 team IDs: ' + g['id'])
                    g['kickoff_at'] = fixture['date']


def _starters(record, side):
    if 'roster_side_counts' in record and record['roster_side_counts'].get(side) != 1:
        return None, 'missing_or_ambiguous_roster_side', 0
    players = record.get(side + '_roster', record.get(side + '_starters'))
    if not isinstance(players, list):
        return None, 'missing_side', 0
    selected = [p for p in players if isinstance(p, dict) and p.get('starter') is True]
    count = len(selected)
    if count != 11:
        return None, 'not_eleven_explicit_starters', count
    verified = {}
    for p in selected:
        pid = p.get('player_id', p.get('id'))
        if type(pid) not in (str, int) or not str(pid).strip() or str(pid).strip() in verified:
            return None, 'missing_or_duplicate_player_id', count
        positions = {POSITIONS[token] for key in ('position', 'position_group', 'position_abbreviation')
                     if (token := str(p.get(key, '')).strip().upper()) in POSITIONS}
        if len(positions) != 1:
            return None, 'unknown_or_ambiguous_position', count
        verified[str(pid).strip()] = positions.pop()
    return [dict(id=pid, position=pos) for pid, pos in sorted(verified.items())], 'complete', count


def _event_ok(row, game):
    stamp = row.get('date', '')
    if not isinstance(stamp, str):
        return False
    try:
        day = stamp if len(stamp) == 10 else aware(stamp).astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return False
    source = row.get('source_sha256')
    side_counts = row.get('competitor_side_counts')
    return (row.get('competition') == CODE and day == game['date'] and row.get('completed') is True
            and row.get('event_status', row.get('status')) == 'STATUS_FULL_TIME'
            and row.get('regulation_final_status') is not False and row.get('lineup_is_post_match_evidence') is not False
            and not any(row.get(k) is True for k in ('extra_time_or_shootout', 'extra_time_indicated', 'shootout_indicated'))
            and row.get('regulation_periods') in (None, 2) and row.get('r5_membership_verified') is not False
            and (side_counts is None or side_counts == {'home': 1, 'away': 1})
            and (not row.get('duplicate_or_missing_sides') or 'roster_side_counts' in row)
            and row.get('identity_match') is not False and isinstance(source, str) and len(source) == 64
            and all(ch in '0123456789abcdef' for ch in source)
            and all(str(row.get(side + '_id')) == game[side + '_id']
                    and str(row.get(side + '_goals')) == str(game[side + '_goals']) for side in SIDES))


def roster_inputs(games, rows):
    """Keep unusable evidence out of the history, with an audit entry for every R5 slot."""
    by_id = defaultdict(list)
    for row in rows:
        key = str(row.get('source_match_id', row.get('match_id', ''))).removeprefix('espn:')
        by_id['espn:' + key].append(row)
    rosters, audit = {}, {}
    for g in games:
        mid = g['id']
        sources = by_id.pop(mid, [])
        valid = [r for r in sources if _event_ok(r, g)]
        entry = dict(source_rows=len(sources), rejected_rows=len(sources)-len(valid),
                     source_sha256=sorted({r['source_sha256'] for r in valid}),
                     sources=[{k: r.get(k) for k in ('source_sha256', 'source_url', 'source_query_time_utc',
                                                      'identity_source_sha256')} for r in sources])
        roster = dict(match_id=mid, home_id=g['home_id'], away_id=g['away_id'])
        for side in SIDES:
            options = [_starters(r, side) for r in valid]
            players, status, count = options[0] if options else (None, 'identity_mismatch' if sources else 'missing_event', 0)
            if any(v != options[0] for v in options[1:]):
                players, status = None, 'conflicting_sources'
            roster[side + '_starters'] = players
            entry[side] = dict(status=status, explicit_starters=count,
                               attack=sum(p['position'] in ('M', 'F') for p in players or []),
                               defence=sum(p['position'] in ('G', 'D') for p in players or []))
        home, away = (roster[side + '_starters'] or [] for side in SIDES)
        if {p['id'] for p in home}.intersection(p['id'] for p in away):
            for side in SIDES:
                roster[side + '_starters'] = None
                entry[side].update(status='player_on_both_sides', attack=0, defence=0)
        if valid:
            rosters[mid] = roster
        audit[mid] = entry
    return rosters, audit, sum(len(v) for v in by_id.values())


def coverage(games, metadata, audit):
    sources = [audit[g['id']] for g in games]
    both = sum(all(s[side]['status'] == 'complete' for side in SIDES) for s in sources)
    sides = {}
    for side in SIDES:
        counts = Counter(s[side]['status'] for s in sources)
        history = [m[side] for m in metadata]
        sides[side] = dict(source_status=dict(counts), min_explicit_starters=min(s[side]['explicit_starters'] for s in sources),
                           min_form_games=min(h['form_games'] for h in history),
                           min_club_games_365=min(h['club_games_365'] for h in history))
        for group in ('attack', 'defence'):
            sides[side][group] = dict(source_group=sum(s[side][group] > 0 for s in sources),
                                      prior_rating=sum(h[group]['rating_available'] for h in history),
                                      prior_retention=sum(h[group]['retention_available'] for h in history))
    any_side = sum(any(s[side]['status'] == 'complete' for side in SIDES) for s in sources)
    status = 'COMPLETE' if both == len(games) else 'PARTIAL' if any_side else 'NO_VERIFIED_ROSTERS'
    return dict(fixtures=len(games), status=status, source_rows=sum(s['source_rows'] for s in sources),
                rejected_source_rows=sum(s['rejected_rows'] for s in sources),
                missing_source_fixtures=sum(not s['source_rows'] for s in sources),
                both_complete_rosters=both, any_complete_side=any_side, sides=sides,
                both_prior_rosters=sum(m['both_prior_rosters'] for m in metadata),
                personnel_neutral=sum(m['personnel_neutral'] for m in metadata),
                all_neutral=sum(m['all_neutral'] for m in metadata))


def _controls(games, rows5, rows7, rows10):
    """Align by original event ID and verify the price, regulation label and published picks."""
    ids = {g['id'] for g in games}
    indices = []
    for rows in (rows5, rows7, rows10):
        idx = {r['id']: r for r in rows}
        if set(idx) != ids or len(idx) != len(rows):
            raise ValueError('published J1 controls differ from the complete holdout population')
        indices.append(idx)
    result = []
    for g in games:
        mid = g['id']
        window = 'evaluation' if g['date'] < BOUNDS[2] else 'recent'
        identity = dict(id=mid, cohort=CODE, date=g['date'], stage=g['stage'], home=g['home'], away=g['away'],
                        actual=[g['home_goals'], g['away_goals']], line=base.actual_line(g),
                        source_sha256=g['source_sha256'], window=window)
        fifth, seventh, tenth = (idx[mid] for idx in indices)
        for ref in (fifth, seventh, tenth):
            if any(ref[k] != v for k, v in identity.items()):
                raise ValueError('published J1 identity, regulation score or market line differs: ' + mid)
        a, b, c = (r['forecast'] for r in (fifth, seventh, tenth))
        if b['candidate'] != c['r7'] or not a['market'] == b['market'] == c['market']:
            raise ValueError('published same-market or R7 controls disagree: ' + mid)
        result.append((identity, dict(r5=a['candidate'], r7=b['candidate'], r10=c['candidate'], market=b['market'])))
    return result


def predictions(prior, features, line, originals, fitted, doses):
    """Use the issued R7 picks for every unchanged head, in replay and prospectively."""
    original = originals['r7']
    full = apply(prior, features, fitted['full'])
    form = apply(prior, features, fitted['form'])
    candidate, heads = decision.decisions(prior, full, doses['full'], line, original)
    form_only, _ = decision.decisions(prior, form, doses['form'], line, original)
    matched, _ = decision.decisions(prior, form, doses['full'], line, original)
    local = base.pick(prior, line)
    local_candidate, _ = decision.decisions(prior, full, doses['full'], line, local)
    tips = dict(originals, candidate=candidate, form_only=form_only, form_matched_dose=matched,
                fixed_full=original if full is prior else base.pick(full, line),
                fixed_form=original if form is prior else base.pick(form, line),
                local_r7=local, local_candidate=local_candidate)
    cells = [tuple(map(int, s.split('-'))) for s in candidate['top3']]
    score = heads['score']
    quality = dict(p_first=float(score[cells[0]]), p_three=float(sum(score[c] for c in cells)),
                   wdl=np.bincount(base.RESULT.ravel(), weights=heads['wdl'].ravel(), minlength=3).tolist())
    return tips, quality, heads


def _forecast(reference, prior, features, exposure, fitted, doses):
    identity, originals = reference
    tips, quality, heads = predictions(prior, features, identity['line'], originals, fitted, doses)
    quality['p_observed'] = float(heads['score'][tuple(identity['actual'])])
    return dict(identity, forecast=tips, quality=dict(candidate=quality), features=features.tolist(), process=exposure)


def selected_days(rows):
    by_date = defaultdict(list)
    for row in rows:
        by_date[row['date']].append(row)
    result = []
    for day, fixtures in sorted(by_date.items()):
        ordered = sorted(fixtures, key=lambda r: (-r['quality']['candidate']['p_first'], r['id']))
        selected = []
        for seat, row in enumerate(ordered[:3], start=1):
            p = row['forecast']['candidate']
            actual = '-'.join(map(str, row['actual']))
            selected.append(dict(seat=seat, id=row['id'], p_first=row['quality']['candidate']['p_first'],
                                 primary=p['primary'], top3=p['top3'], actual=actual,
                                 first_hit=actual == p['primary'], three_hit=actual in p['top3']))
        sweep = all(r['first_hit'] for r in selected) if len(selected) == 3 else None
        result.append(dict(date=day, window=fixtures[0]['window'], fixtures=len(fixtures),
                           selected=selected, three_first_sweep=sweep))
    return result


def _hit_rates(seats):
    first = sum(r['first_hit'] for r in seats)
    three = sum(r['three_hit'] for r in seats)
    n = len(seats)
    return dict(selected=n, first_hits=first, ordered_three_hits=three, first_rate=first / n if n else None,
                ordered_three_rate=three / n if n else None)


def _rank_summary(days):
    seats = [r for d in days for r in d['selected']]
    full = [d for d in days if d['three_first_sweep'] is not None]
    hits = sum(d['three_first_sweep'] for d in full)
    return dict(days=len(days), fixtures=sum(d['fixtures'] for d in days), full_days=len(full),
                short_days=len(days)-len(full), three_first_sweeps=hits,
                three_first_sweep_rate=hits / len(full) if full else None, individual=_hit_rates(seats),
                per_seat={str(i): _hit_rates([r for r in seats if r['seat'] == i]) for i in range(1, 4)})


def summarize(rows, days):
    scores, comparisons = {}, {}
    pairs = {arm: (arm, 'candidate') for arm in ('r5', 'r7', 'r10', 'market', 'form_only', 'form_matched_dose')}
    pairs.update(fixed_increment=('fixed_form', 'fixed_full'), same_runtime=('local_r7', 'local_candidate'))
    for label, (baseline, candidate) in pairs.items():
        paired = compare(rows, baseline=baseline, candidate=candidate)
        scores.update(paired['tracks'])
        comparisons[label] = dict(baseline=baseline, candidate=candidate, metrics=paired['paired'])
    return dict(mode=MODE, formal_kpi_eligible=False, population=len(rows), measured_lines=sum(r['line'] is not None for r in rows),
                scores=scores, paired=comparisons, bootstrap=paired['bootstrap'],
                ranking=dict(all=_rank_summary(days), full=_rank_summary([d for d in days if d['fixtures'] >= 3]),
                             short=_rank_summary([d for d in days if d['fixtures'] < 3])))


def replay(games, roster_rows, originals, settings, inputs, out):
    cut, test, recent = (sum(g['date'] < boundary for g in games) for boundary in BOUNDS)
    if not 0 < cut < test < recent < len(games) or settings['training_games'] != cut:
        raise ValueError('incomplete registered J1 folds')
    references = _controls(games[test:], *originals)
    rosters, audit, outside = roster_inputs(games, roster_rows)
    anchors, anchor_features, _ = r7.inputs(games, CODE)
    design, metadata = LineupHistory(rosters).walk(games, anchors)
    if design.shape != (len(games), 2, len(FEATURES)) or len(metadata) != len(games):
        raise ValueError('lineup history lost original J1 targets')
    folds = dict(train=slice(0, cut), validation=slice(cut, test),
                 evaluation=slice(test, recent), recent=slice(recent, None))
    by_fold = {k: coverage(games[s], metadata[s], audit) for k, s in folds.items()}
    overall = coverage(games, metadata, audit)
    archive.put(out / 'roster-coverage.jsonl.gz',
                (dict(id=g['id'], date=g['date'], fold=fold, source=audit[g['id']], prior=m)
                 for fold, part in folds.items() for g, m in zip(games[part], metadata[part], strict=True)))
    inputs.update(version=VERSION, competition=CODE, mode=MODE, formal_kpi_eligible=False,
                  roster_coverage=overall['status'], roster_rows=len(roster_rows), outside_panel_rows=outside)
    scores = np.asarray([[g['home_goals'], g['away_goals']] for g in games])
    priors = control.matrices(anchors[cut:], anchor_features[cut:], settings)
    validation = priors[:test-cut]
    fitted, doses, trials = {}, {}, {}
    for arm in ('full', 'form'):
        fitted[arm] = fit(anchors[:cut], design[:cut], scores[:cut], form_only=arm == 'form')
        adjusted = [apply(m, x, fitted[arm]) for m, x in zip(validation, design[cut:test], strict=True)]
        doses[arm], trials[arm] = decision.choose(games[cut:test], validation, adjusted)
    write_new(out / 'fit.json', dict(version=VERSION, competition=CODE, bounds=list(BOUNDS), features=list(FEATURES),
              roster_coverage=by_fold['train']['status'], training_games=cut, training_through=games[cut-1]['date'],
              validation_games=test-cut, validation_through=games[test-1]['date'], fitted=fitted, doses=doses, trials=trials))
    records = []
    for i, ref in enumerate(references, start=test):
        g = games[i]
        row = _forecast(ref, priors[i-cut], design[i], metadata[i], fitted, doses)
        row.update(home_id=g['home_id'], away_id=g['away_id'], kickoff_at=g['kickoff_at'], sources=g['sources'],
                   bookmaker=g.get('bookmaker'), market_time_basis=g.get('market_time_basis'),
                   paired_market=g['paired_market'], h2h=g.get('h2h'), total=g.get('total'),
                   target_roster_audit=audit[g['id']])
        records.append(row)
    metrics, selections = {}, []
    for window in ('evaluation', 'recent'):
        group = [r for r in records if r['window'] == window]
        days = selected_days(group)
        metrics[window] = dict(summarize(group, days), study_roster_coverage=overall['status'], coverage=by_fold[window])
        selections.extend(days)
    archive.put(out / 'forecasts.jsonl.gz', records)
    archive.put(out / 'selected-days.jsonl.gz', selections)
    write_new(out / 'metrics.json', metrics)
    write_new(out / 'coverage.json', dict(overall=overall, folds=by_fold, outside_panel_rows=outside))
    write_new(out / 'inputs.json', inputs)
    # Only this prospective recipe sees the held-out labels. Its doses remain the 2024 choices.
    write_new(out / 'forward.json', dict(inputs, through=games[-1]['date'], games=len(games), features=list(FEATURES),
              study_inputs_sha256=sha(out / 'inputs.json'), fit_sha256=sha(out / 'fit.json'), doses=doses,
              fitted={arm: fit(anchors, design, scores, form_only=arm == 'form') for arm in fitted}))
    return {k: dict(population=v['population'], measured_lines=v['measured_lines'],
                    roster_coverage=v['coverage']['status']) for k, v in metrics.items()}


def read_rosters(path):
    body = path.read_bytes()
    return [json.loads(line) for line in body.splitlines() if line.strip()], sha256(body).hexdigest()


def snapshot_identity(path, rows, roster_hash, panel_hash, package_hash):
    body = path.read_bytes()
    snapshot = json.loads(body)
    if (snapshot['schema'], snapshot['events_jsonl_sha256'], snapshot['events_jsonl_rows'],
        snapshot['r5_panel_member_sha256'], snapshot['r5_panel_package_sha256']) != (
            'j1-lineups-history-intake-snapshot-v1', roster_hash, len(rows), panel_hash, package_hash):
        raise ValueError('roster snapshot manifest differs from JSONL or R5 panel')
    unique = {str(r.get('source_match_id', r.get('match_id', ''))).removeprefix('espn:') for r in rows}
    if 'unique_source_match_ids' in snapshot and len(unique) != snapshot['unique_source_match_ids']:
        raise ValueError('roster snapshot manifest differs from event IDs')
    return dict(roster_manifest_sha256=sha256(body).hexdigest(), roster_snapshot_at=snapshot['snapshot_as_of_utc'])


def run(r5, r7_dir, r10, rosters, out):
    if out.exists():
        raise FileExistsError(out)
    if control.read(PROTOCOL)['version'] != VERSION:
        raise ValueError('R15 protocol version mismatch')
    rows, roster_hash = read_rosters(rosters)
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent) as tmp:
        root = Path(tmp)
        bundles = dict(r5=(r5, 'models/jpn.1.tar.gz'), r7=(r7_dir, 'models/jpn.1.tar.gz'),
                       r10=(r10, 'replay/main.tar.gz'))
        manifests = {key: control.read(folder / 'manifest.json') for key, (folder, _) in bundles.items()}
        for key, (folder, archive_name) in bundles.items():
            verified_archive(folder, manifests[key], archive_name, root / key)
        panel = root / 'r5/panel.jsonl'
        baseline, shot = root / 'r7/main', root / 'r10/main/jpn.1'
        settings, shot_settings = (control.read(p / 'fit.json') for p in (baseline, shot))
        forward = control.read(baseline / 'forward.json')
        panel_hash = sha(panel)
        if ((settings['version'], settings['competition'], tuple(settings['bounds']), settings['source_panel_sha256']) !=
                (r7.VERSION, CODE, BOUNDS, panel_hash)
                or (shot_settings['version'], shot_settings['competition'], tuple(shot_settings['bounds']),
                    shot_settings['panel_sha256']) != (decision.VERSION, CODE, BOUNDS, [panel_hash])):
            raise ValueError('R5, R7 and R10 historical panels or calendars disagree')
        games = check_panel(panel, CODE)
        if (shot_settings['train_games'] != settings['training_games']
                or shot_settings['validation_games'] != sum(BOUNDS[0] <= g['date'] < BOUNDS[1] for g in games)):
            raise ValueError('R7 and R10 fold populations disagree')
        if (forward['version'], forward['competition'], forward['through'], forward['fit_sha256']) != (
                r7.VERSION, CODE, games[-1]['date'], sha(baseline / 'fit.json')):
            raise ValueError('R7 forward baseline differs from the historical study')
        _schedule(games, r5, manifests['r5'])
        fifth = [dict(r, window=w) for w in ('evaluation', 'recent')
                 for r in read_lines(root / 'r5/study' / (w + '.jsonl'))]
        originals = (fifth, archive.read_gzip(baseline / 'forecasts.jsonl.gz'), archive.read_gzip(shot / 'forecasts.jsonl.gz'))
        inputs = dict(protocol_sha256=sha(PROTOCOL), panel_sha256=panel_hash, rosters_sha256=roster_hash,
                      schedule_index_sha256=manifests['r5']['files']['evidence/source-index.json'],
                      r7_fit_sha256=sha(baseline / 'fit.json'), baseline_sha256=sha(baseline / 'forward.json'),
                      r10_fit_sha256=sha(shot / 'fit.json'),
                      archive_sha256={key: manifests[key]['files'][name] for key, (_, name) in bundles.items()})
        manifest_path = rosters.parent / 'snapshot-manifest.json'
        if manifest_path.is_file():
            inputs.update(snapshot_identity(manifest_path, rows, roster_hash, panel_hash, inputs['archive_sha256']['r5']))
        result_dir = root / 'study'
        result_dir.mkdir()
        result = replay(games, rows, originals, settings, inputs, result_dir)
        if sha(rosters) != roster_hash:
            raise ValueError('roster inputs changed during replay')
        result_dir.rename(out)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('r5', 'r7', 'r10', 'rosters', 'out'):
        parser.add_argument('--' + arg, type=Path, required=True)
    a = parser.parse_args()
    print(json.dumps(run(a.r5, a.r7, a.r10, a.rosters, a.out)))
