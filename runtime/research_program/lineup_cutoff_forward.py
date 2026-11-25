"""Offline R18 J1 weekend comparison. Preview by default, or issue one complete research batch."""

import argparse
from datetime import date, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import numpy as np

from publication import atomic_json
from research.analysis_freeze import aware, digest
from storage_lock import exclusive_lock
from . import continuation_forward as r7
from . import goal_state_forward as ledger
from . import lineup_cutoff as cutoff_study
from . import lineup_forward as r15
from . import lineup_study as study
from . import nov25_first_review as review
from .future_identity import fixture_identity
from .goal_state_study import sha
from .kpi_validation import _first
from .r8_score_study import extract, read
from .r9_score_study import verified_archive
from .territory_forward import FirstIssues, _document


VERSION = 'R18-J1-prior-fixture-information-1'
TRACK = 'research-r18-j1-prior-fixture-information-20261125'
CODE = study.CODE
R15_REGISTRATION_SHA = '45804bc3636bac38293bf037b887e8cb1e385fd93d981dbdafde419c15336fb0'
R15_POSITIONS_SHA = '6feb42e2d263aaacd47470a5cd759242de63e819167dcc321f94ff9e8e10aeb1'
WINDOW_SHA = '72c7037d45286dfc846f930a027b3b0b728cbc019180e1217b80d989e94e92f2'
CALENDAR_PROVENANCE_SHA = 'c507fd7b85b3f31b0f23a662ba6edd9cf52443e1b619ecfc9fa246b89a8a0b83'
DAYS = ('20261122', '20261123', '20261124', '20261125')
BOARD = 'https://site.api.espn.com/apis/site/v2/sports/soccer/jpn.1/scoreboard?dates='
BEIJING = ZoneInfo('Asia/Shanghai')
THROUGH = '2026-11-21'
AGREED = 'official_and_espn_ft_agree'
OFFICIAL_LOGS = ('raw/jleague-official/match-card-captures.jsonl', 'jleague-live-captures.jsonl')
SIDES = study.SIDES


def _now():
    return aware(ledger._now()).astimezone(timezone.utc)


def _windows(display):
    folder = display / 'registration'
    plan = ledger._read(folder, read(folder / 'plan.json'))
    _first(plan, r15.display_windows.TRACK + '-registration')
    if plan['record_sha256'] != WINDOW_SHA or sha(cutoff_study.PROTOCOL) != cutoff_study.PROTOCOL_SHA:
        raise ValueError('different #51 registration or R18 preregistered protocol')
    slates = plan['payload']['identity']['slates']
    population = plan['payload']['fixtures']
    early, weekend = ({m: population[m] for m in slates[k]} for k in ('nov25', 'nov28_29'))
    if (len(early) != 7 or len(weekend) != 10 or early.keys() & weekend.keys()
            or early.keys() | weekend.keys() != population.keys()
            or any(f['cohort'] != CODE or f['match_id'] != m for m, f in population.items())):
        raise ValueError('different registered J1 populations')
    return plan, early, weekend


def _raw(intake, row, at):
    if not aware(row['requested_at_utc']) <= aware(row['captured_at_utc']) <= at:
        raise ValueError('invalid acquisition interval: ' + row['url'])
    body = review.file_in(intake, row['path']).read_bytes()
    if len(body) != row['bytes'] or sha256(body).hexdigest() != row['sha256']:
        raise ValueError('raw acquisition length or SHA differs: ' + row['path'])
    return body


def _calendar_board(board, fixtures, *, annual=False):
    if [league['slug'] for league in board['leagues']] != [CODE]:
        raise ValueError('wrong J1 calendar league')
    grouped = {day: {} for day in DAYS}
    for event in board['events']:
        day = aware(event['date']).astimezone(BEIJING).strftime('%Y%m%d')
        if annual and day not in grouped:
            continue
        mid = 'espn:' + str(event['id'])
        if day not in grouped or mid not in fixtures or mid in grouped[day]:
            raise ValueError('unresolved, duplicate or out-of-scope calendar fixture: ' + mid)
        game, sides = review._game(event, fixtures[mid], False)
        if aware(event['date']) != aware(game['date']):
            raise ValueError('event and competition calendar clocks differ: ' + mid)
        grouped[day][mid] = dict(kickoff=event['date'], **{s: sides[s]['team']['displayName'] for s in SIDES})
    return grouped


def _calendar(intake, display, early, at, annual_path):
    audit_rel = 'audit/nov22-25-annual-calendar-audit.json'
    audit, audit_sha = _document(intake / audit_rel)
    audited_at = aware(audit['audit_created_at_utc'])
    provenance, provenance_sha = _document(display / 'registration/calendar-provenance.json')
    if provenance_sha != CALENDAR_PROVENANCE_SHA or audited_at > at:
        raise ValueError('calendar audit clock or original capture provenance differs')
    annual, = (row for row in provenance['later_capture'] if row['year'] == 2026)
    ref = annual['source_capture']
    original = audit['annual_calendar_source']
    body = (annual_path or Path(original['path'])).read_bytes()
    doc = json.loads(body)
    if (not sha256(body).hexdigest() == ref['sha256'] == annual['sha256'] == original['sha256']
            or not len(body) == ref['bytes'] == original['bytes']
            or not len(doc['events']) == original['event_count'] == ref['response_event_count'] < ref['limit']
            or ref['url'] != BOARD + '2026&limit=1000' or ref['http_status'] != 200
            or aware(ref['retrieved_at_utc']) > audited_at):
        raise ValueError('annual calendar bytes, clock, URL or coverage differ')
    annual_days = _calendar_board(doc, early, annual=True)
    counts = {d: len(v) for d, v in annual_days.items()}
    expected = {m: details for day in annual_days.values() for m, details in day.items()}
    claimed = {'espn:' + str(v['event_id']): dict(kickoff=v['kickoff_raw'], home=v['home'], away=v['away'])
               for v in audit['annual_calendar_events_in_scope']}
    if (expected.keys() != early.keys() or claimed != expected or len(claimed) != len(audit['annual_calendar_events_in_scope'])
            or counts != audit['scoreboard_event_counts_by_query_date']):
        raise ValueError('annual audit does not cover the exact registered calendar')
    log_name = audit['scoreboard_evidence_path']
    rows, log_bytes = review.dated_log(review.file_in(intake, log_name), audited_at)
    daily = {}
    for _, row in rows:
        day = row['date_query']
        if day in daily or day not in counts or row['url'] != BOARD + day or row['http_status'] != 200:
            raise ValueError('ambiguous or incorrect dated J1 board: ' + day)
        checked = _calendar_board(json.loads(_raw(intake, row, audited_at)), early)
        events = checked[day]
        listed = ['espn:' + str(e['event_id']) for e in row['events']]
        if (events != annual_days[day] or any(others for d, others in checked.items() if d != day)
                or len(events) != row['event_count'] or len(listed) != len(events) or set(listed) != events.keys()):
            raise ValueError('daily and annual J1 calendar differ: ' + day)
        daily[day] = {k: row[k] for k in ('requested_at_utc', 'captured_at_utc', 'url', 'path', 'sha256', 'bytes')}
        daily[day]['match_ids'] = sorted(events)
    if set(daily) != set(DAYS):
        raise ValueError('a dated J1 scoreboard is missing')
    return dict(timezone=BEIJING.key, counts=counts, daily=daily, annual=ref, annual_file=annual['path'],
                audit_file=audit_rel, audit_sha256=audit_sha, audit_created_at_utc=audit['audit_created_at_utc'],
                provenance_sha256=provenance_sha, scoreboard_log=log_name, scoreboard_log_sha256=sha256(log_bytes).hexdigest())


def _official(intake, early, at):
    pages = {mid: [] for mid in early}
    logs = {}
    for name in OFFICIAL_LOGS:
        path = intake / name
        if not path.is_file():
            continue
        rows, retained = review.dated_log(path, at)
        if retained:
            logs[name] = sha256(retained).hexdigest()
        for _, row in rows:
            code = row.get('match_code', row.get('jleague_match_code'))
            if code not in review.J1_CARDS or row.get('jleague_match_code', code) != code:
                raise ValueError('official acquisition has an unknown or conflicting match card code')
            mid = 'espn:' + review.J1_CARDS[code][0]
            if (row['url'] != review.J1_URL.format(code) or mid not in early
                    or row.get('event_id') is not None and str(row['event_id']) != mid.removeprefix('espn:')
                    or not aware(row['requested_at_utc']) <= aware(row['captured_at_utc']) <= at):
                raise ValueError('official match acquisition identity or clocks differ')
            if row['http_status'] != 200:
                continue
            stamp = aware(row['captured_at_utc']).astimezone(timezone.utc)
            suffix = ''
            if name == OFFICIAL_LOGS[1]:
                prefix = {'match-card-FT': '-FT-refresh-', 'postgame-state-check': '-postgame-check-'}.get(row.get('kind'), '-refresh-')
                suffix = prefix + stamp.strftime('%Y%m%dT%H%M%S%fZ')
            if row['path'] != f'raw/jleague-official/match-{code}{suffix}.html':
                raise ValueError('wrong official raw match card path')
            ref = {k: row[k] for k in ('url', 'path', 'sha256', 'bytes', 'requested_at_utc', 'captured_at_utc')}
            pages[mid].append(review.official_card(_raw(intake, row, at), dict(ref, code=code), early[mid]))
    for values in pages.values():
        values.sort(key=lambda page: (aware(page['source']['captured_at_utc']), page['source']['path']))
    return pages, logs


def _xi(doc, fixture, side):
    """Read the ESPN roster side with the original R15 four-group vocabulary."""
    if digest(study.POSITIONS) != R15_POSITIONS_SHA:
        raise ValueError('R18 requires the original signed-study R15 position vocabulary')
    groups = [v for v in doc.get('rosters') or [] if isinstance(v, dict) and v.get('homeAway') == side]
    team = groups[0].get('team') if len(groups) == 1 else None
    if not isinstance(team, dict) or 'espn:' + str(team.get('id')) != fixture[side]:
        return None, 'missing_or_mismatched_roster_side', 0
    players = []
    for row in groups[0].get('roster') or []:
        if not isinstance(row, dict) or row.get('starter') is not True:
            continue
        athlete = row['athlete'] if isinstance(row.get('athlete'), dict) else {}
        pos = row.get('position') or athlete.get('position') or {}
        if not isinstance(pos, dict):
            pos = {}
        players.append(dict(player_id=athlete.get('id'), starter=True, position_group=pos.get('displayName') or pos.get('name'),
                            position_abbreviation=pos.get('abbreviation')))
    return study._starters({side + '_roster': players}, side)


def _roster(fixture, captures):
    roster = dict(match_id=fixture['match_id'])
    coverage = {}
    for side in SIDES:
        roster[side + '_id'] = fixture[side].removeprefix('espn:')
        checks = [(_xi(doc, fixture, side), ref) for doc, ref in captures]
        proven = {digest(players): players for (players, _, _), _ in checks if players is not None}
        players = next(iter(proven.values())) if len(proven) == 1 else None
        status = 'complete' if players else 'conflicting_starters' if proven else checks[-1][0][1]
        roster[side + '_starters'] = players
        coverage[side] = dict(status=status, checks=[dict(sha256=r['sha256'], status=s, explicit_starters=n)
                                                    for (_, s, n), r in checks])
    if {p['id'] for p in roster['home_starters'] or []} & {p['id'] for p in roster['away_starters'] or []}:
        for side in SIDES:
            roster[side + '_starters'] = None
            coverage[side]['status'] = 'player_on_both_sides'
    return roster, coverage


def _evidence(intake, display, early, at, annual_path=None):
    calendar = _calendar(intake, display, early, at, annual_path)
    rows, retained = review.refresh_log(intake, at, with_bytes=True)
    captures = [(i, r) for i, r in rows if r['league_slug'] == CODE]
    statuses, labels, _, _ = review.inspect(intake, early, captures, at)
    pages, logs = _official(intake, early, at)
    logs['refreshes.jsonl'] = sha256(retained).hexdigest()
    events, rosters = {}, {}
    for mid, fixture in sorted(early.items()):
        status, label = statuses[mid], labels.get(mid)
        cross = review.official_crosscheck(pages[mid], status, label)
        verdict = cross['verdict']
        if verdict == AGREED and status['issues']:
            verdict = 'pending_conflict'
        entry = events[mid] = dict(fixture=fixture, verdict=verdict, issues=cross['issues'] + status['issues'],
                                   official=cross['latest_official'], official_status=cross['official_state'],
                                   espn_status=(status['latest_by_kind'].get('summary') or {}).get('status'),
                                   espn=(status['latest_by_kind'].get('summary') or {}).get('source'))
        if verdict != AGREED:
            continue
        bound = aware(fixture['kickoff_at']) + timedelta(minutes=90)
        official_at = aware(cross['official_final']['captured_at_utc'])
        espn_at = aware(label['observed_at'])
        if min(official_at, espn_at) < bound:
            raise ValueError('final captured before scheduled kickoff plus 90 minutes: ' + mid)
        goals = [label[s + '_goals'] for s in SIDES]
        # Reopen the very same retained FT summaries for the starting XIs. A live
        # summary or a separately transcribed roster cannot establish a post-match XI.
        docs = []
        for _, row in captures:
            if (row['kind'] != 'summary' or 'espn:' + str(row['event_id']) != mid or row['http_status'] != 200
                    or aware(row['captured_at_utc']) < espn_at):
                continue
            body = _raw(intake, row, at)
            doc = json.loads(body)
            game, sides = review._game(doc['header'], fixture, True)
            ref = {k: row[k] for k in ('kind', 'league_slug', 'url', 'path', 'bytes', 'sha256', 'requested_at_utc', 'captured_at_utc')}
            _, certified = review._observation(body, doc, fixture, game, sides, ref, intake, at)
            if certified and goals == [certified[s + '_goals'] for s in SIDES]:
                docs.append((doc, ref))
        if not docs:
            raise ValueError('missing certified raw final for roster readback: ' + mid)
        docs.sort(key=lambda pair: (aware(pair[1]['captured_at_utc']), pair[1]['path']))
        rosters[mid], coverage = _roster(fixture, docs)
        pair_at = max(espn_at, official_at).astimezone(timezone.utc)
        history_at = max(pair_at, aware(docs[-1][1]['captured_at_utc'])).astimezone(timezone.utc)
        entry.update(score=goals, official=cross['official_final'], espn=label['evidence'],
                     ft_pair_observed_at_utc=pair_at.isoformat(), history_observed_at_utc=history_at.isoformat(),
                     official_ko_interval=cross['kickoff_interval'], espn_actual_kickoff=status['kickoff'],
                     ft_summary_sources=[ref for _, ref in docs], roster=rosters[mid], roster_coverage=coverage)
    pending = sorted(early.keys() - rosters.keys())
    return dict(calendar=calendar, acquisition_log_sha256=logs, events=events, pending=pending,
                dual_ft90=len(rosters), prior_slots=len(early), weekend_slots=10), rosters


def _originals(path, temp, weekend, early, at):
    if path.is_file():
        extracted = temp / 'r15-firsts'
        extract(path, extracted)
        index, = extracted.rglob('receipts.json')
        path = index.parent
    index = read(path / 'receipts.json')
    for ref in [index['registration'], *index['matches'].values()]:
        target = review.file_in(path, ref['file'])
        if ref['file'] != ref['id'] + '.json' or target.parent != path.resolve():
            raise ValueError('R15 receipt outside its original issue directory')
    batch = FirstIssues.load(path)
    plan = _first(batch.registration, r15.TRACK + '-registration')
    if plan['record_sha256'] != R15_REGISTRATION_SHA:
        raise ValueError('different signed R15 registration from the original weekend batch')
    spec = plan['payload']['identity']
    old_at = aware(spec['inputs_cutoff_at']).astimezone(timezone.utc)
    earliest = min(aware(f['kickoff_at']) for f in weekend.values())
    if (spec['version'] != study.VERSION or spec['formal_kpi_eligible'] is not False or spec['competitions'] != [CODE]
            or spec['protocol_sha256'] != study.PROTOCOL_SHA256 or spec['slots_per_competition'] != 3
            or sorted(spec['already_started']) != sorted(early) or set(batch.matches) != set(weekend)
            or set(spec['payload_sha256']) != set(weekend) or spec['history_cutoff'] != old_at.date().isoformat()
            or earliest != aware(plan['fixture']['kickoff_at'])
            or not aware(spec['roster_snapshot_at']) <= old_at <= aware(plan['fixture']['cutoff_at'])
            or not aware(plan['recorded_at']) <= at < earliest
            or plan['match_id'] != r15.TRACK + '-plan:' + digest(plan['payload'])
            or plan.get('idempotency_key') != digest(plan['payload'])):
        raise ValueError('expected all ten original pre-update R15 firsts and their registered scope')
    records = {mid: batch.match(f, at) for mid, f in weekend.items()}
    for mid, rec in records.items():
        _first(rec, r15.TRACK)
        p = rec['payload']
        if (rec.get('idempotency_key') != plan['record_sha256'] or aware(rec['recorded_at']) >= earliest
                or aware(p['inputs_cutoff_at']) != old_at or p['history_cutoff'] != spec['history_cutoff']
                or digest({k: v for k, v in p.items() if k != 'registration_sha256'}) != spec['payload_sha256'][mid]):
            raise ValueError('R15 first differs from its registered payload or input clock: ' + mid)
    if spec['selected'] != {CODE: r15._seats({m: r['payload'] for m, r in records.items()})}:
        raise ValueError('R15 originally issued seats differ')
    return plan, records, old_at


def _history_coverage(games, path, meta, old_at):
    rows, checksum = study.read_rosters(path)
    stamp = aware(meta['roster_snapshot_at'])
    if (checksum != meta['rosters_sha256'] or stamp > old_at
            or any(aware(row[k]) > stamp for row in rows for k in ('source_query_time_utc', 'identity_source_query_time_utc')
                   if row.get(k))):
        raise ValueError('R15 original roster clocks or bytes differ')
    population = {g['id'] for g in games}
    covered = {'espn:' + str(r.get('source_match_id', r.get('match_id'))).removeprefix('espn:') for r in rows}
    missing = population - covered
    if missing:
        manifest = read(path.parent / 'snapshot-manifest.json')
        audit_file = path.parent / 'coverage-audit.json'
        if not audit_file.is_file() or sha(audit_file) != manifest.get('coverage_audit_sha256'):
            raise ValueError('R15 historical roster acquisition is incomplete')
        audit = read(audit_file)
        documented = {'espn:' + str(r['source_match_id']) for r in audit.get('missing_target_reasons', [])
                      if r.get('attempt_statuses') and r.get('reason') != 'not_attempted'}
        if missing != documented:
            raise ValueError('R15 historical target IDs without an acquisition or documented attempt')
    return dict(scheduled=len(games), normalized=len(population & covered), missing_after_attempt=sorted(missing))


def _training(folders, manifests, folder, roster_file, spec, old_at, root):
    games, rosters, anchors, _, validation, _, meta = cutoff_study.load(folders['r5'], folders['r7'], folder, roster_file, root)
    recipe, recipe_sha = _document(folder / 'forward.json')
    baseline, baseline_sha = _document(root / 'r7/main/forward.json')
    verified_archive(folders['r10'], manifests['r10'], r15.MODELS['r10'], root / 'r10')
    shot_folder = root / 'r10/main/jpn.1'
    shot, shot_sha = _document(shot_folder / 'forward.json')
    bound = dict(protocol_sha256=study.PROTOCOL_SHA256, recipe_sha256={CODE: recipe_sha}, r7_forward_sha256=baseline_sha,
                 r10_recipe_sha256=shot_sha, study_inputs_sha256=sha(folder / 'inputs.json'), fit_sha256=sha(folder / 'fit.json'))
    bound.update({k: meta[k] for k in ('panel_sha256', 'rosters_sha256', 'roster_manifest_sha256', 'roster_snapshot_at', 'roster_coverage')})
    if (any(spec.get(k) != v for k, v in bound.items()) or any(recipe.get(k) != v for k, v in meta.items())
            or (recipe['through'], games[-1]['date'], recipe['games'], recipe['features']) != (
                THROUGH, THROUGH, len(games), list(study.FEATURES))
            or not recipe['through'] < old_at.date().isoformat()
            or set(recipe['fitted']) != {'full', 'form'} or set(recipe['doses']) != {'full', 'form'}
            or recipe['doses'] != validation['doses'] or recipe['fit_sha256'] != bound['fit_sha256']
            or recipe['study_inputs_sha256'] != bound['study_inputs_sha256']
            or (recipe['competition'], recipe['version'], recipe['formal_kpi_eligible']) != (CODE, study.VERSION, False)
            or (baseline['version'], baseline['competition'], baseline['through'], baseline['fit_sha256']) != (
                r7.model.VERSION, CODE, THROUGH, meta['r7_fit_sha256'])
            or shot['r7_forward_sha256'] != baseline_sha or shot['through'] != THROUGH or shot['fit_sha256'] != meta['r10_fit_sha256']
            or meta['r10_fit_sha256'] != sha(shot_folder / 'fit.json')
            or meta['archive_sha256'] != {k: manifests[k]['files'][name] for k, name in r15.MODELS.items()}):
        raise ValueError('different R15 full-panel recipe, validation doses, source snapshot or R7/R10 weights')
    for arm, fitted in recipe['fitted'].items():
        doses = recipe['doses'][arm]
        if (fitted['training_games'] != len(games) or fitted['form_only'] is not (arm == 'form')
                or set(doses) != {'score', 'wdl', 'total'}
                or any(type(n) not in (int, float) or n not in study.decision.DOSES for n in doses.values())):
            raise ValueError('R15 full-panel weights or validation doses are invalid')
    coverage = _history_coverage(games, roster_file, meta, old_at)
    return games, rosters, anchors, recipe, baseline, bound, coverage


def _game(fixture, odds):
    game = r7.frozen_game(fixture, odds)
    game.update(date=aware(fixture['kickoff_at']).astimezone(timezone.utc).date().isoformat(),
                **{s + '_id': fixture[s].removeprefix('espn:') for s in SIDES})
    return game


def _same_quality(a, b):
    return a.keys() == b.keys() and all(
        np.shape(a[k]) == np.shape(b[k]) and np.allclose(a[k], b[k], rtol=1e-10, atol=1e-12) for k in a)


def _check_grid(game, baseline, seventh):
    prior = r7.matrix(game, baseline)
    pick = study.base.pick(prior, study.base.actual_line(game))
    quality = dict(p_first=float(prior.max()), p_three=float(np.partition(prior.ravel(), -3)[-3:].sum()),
                   wdl=[float(prior[mask].sum()) for mask in (study.base.H > study.base.A, study.base.H == study.base.A,
                                                             study.base.H < study.base.A)])
    if pick != seventh['forecasts']['candidate'] or not _same_quality(quality, seventh['quality']):
        raise ValueError('native R7 prematch grid did not reproduce its signed choices or probabilities: ' + game['id'])
    return prior


def _unfitted(game, baseline):
    day = date.fromisoformat(game['date']).toordinal()
    state = r7.model.load(baseline['state'])
    hl = r7.old.half_life(CODE)
    r7.base.advance(state['goals'], day, hl)
    grid, _, _ = r7.model.prepare(state, game, day, hl)
    return grid


def _prior_anchors(early, sources, baseline, baseline_sha):
    batches, observations, schedule, markets = sources
    games, anchors, receipts = [], [], {}
    for mid, f in sorted(early.items()):
        before = aware(f['kickoff_at']) - timedelta(microseconds=1)
        raw, seventh = (batches[k].match(f, before) for k in ('r5', 'r7'))
        odds, signed = raw['payload'], seventh['payload']
        r15._fixture_source(f, raw, schedule[mid], markets[mid], observations)
        game = _game(f, odds)
        if (signed['input_sha256'] != raw['record_sha256'] or signed['model_sha256'] != baseline_sha
                or signed['training_through'] != baseline['through'] or baseline['through'] >= game['date']
                or aware(raw['recorded_at']) > aware(seventh['fixture']['cutoff_at'])
                or signed['line'] != odds['line'] or odds['line'] != study.base.actual_line(game)
                or signed['forecasts']['market'] != odds['forecasts']['market']):
            raise ValueError('different original prematch anchor source or prices: ' + mid)
        _check_grid(game, baseline, signed)
        anchor = _unfitted(game, baseline)
        games.append(game)
        anchors.append(anchor)
        means = [float(anchor.sum(axis=1) @ np.arange(anchor.shape[0])),
                 float(anchor.sum(axis=0) @ np.arange(anchor.shape[1]))]
        receipts[mid] = dict(model_teams=odds['model_teams'],
                             r5_first_sha256=raw['record_sha256'], r7_first_sha256=seventh['record_sha256'],
                             r7_recorded_at=seventh['recorded_at'], r7_forward_sha256=baseline_sha,
                             prices_sha256=digest(odds['prices']), unfitted_anchor_sha256=digest(anchor.tolist()),
                             expected_goals=means)
    return games, anchors, receipts


def _extend(history, early, evidence, games, anchors, refs):
    for game in games:
        mid = game['id']
        event, ref = evidence['events'][mid], refs[mid]
        if event['verdict'] != AGREED or fixture_identity(event['fixture']) != fixture_identity(early[mid]):
            raise ValueError('unverified prior fixture: ' + mid)
        goals = event['score']
        game.update(home_goals=goals[0], away_goals=goals[1], score_basis='regulation_90')
        ref.update(evidence_sha256=digest(event), score=goals, espn_sha256=event['espn']['sha256'],
                   official_sha256=event['official']['sha256'], roster_source_sha256=[s['sha256'] for s in event['ft_summary_sources']],
                   residual=np.clip(np.subtract(goals, ref['expected_goals']), -3, 3).tolist())
    history.walk(games, anchors)


def _reproduce(f, signed, sources, recipe, baseline, history, spec, old_at):
    prev = signed['payload']
    generated = r15._forecast(f, sources, recipe, baseline, history, spec, old_at, spec['history_cutoff'])
    if spec.get('prior_display'):
        generated['prior_display'] = spec['prior_display']['slots'][f['match_id']]
    quality, features = generated.pop('quality'), generated.pop('features')
    published = {k: v for k, v in prev.items() if k not in ('quality', 'features', 'registration_sha256')}
    if (published != generated or not _same_quality(quality, prev['quality'])
            or np.shape(prev['features']) != np.shape(features) or not np.allclose(features, prev['features'], rtol=1e-10, atol=1e-12)):
        raise ValueError('pre-update R15 first or original exposure did not reproduce: ' + f['match_id'])
    raw, seventh = (sources[0][k].match(f, old_at) for k in ('r5', 'r7'))
    game = _game(f, raw['payload'])
    return game, _check_grid(game, baseline, seventh['payload'])


def _updated(game, prior, signed, recipe, history, refs, at):
    prev = signed['payload']
    # All seven updates have already passed their sub-day evidence cutoffs. Include
    # those completed today, keeping the target's own day strictly out of history.
    stop = min(date.fromisoformat(game['date']), at.date() + timedelta(days=1)).isoformat()
    x, process = history.context(game, cutoff=stop)
    originals = {k: prev['forecasts'][k] for k in ('r5', 'r7', 'r10', 'market')}
    tips, quality, _ = study.predictions(prior, x, prev['line'], originals, recipe['fitted'], recipe['doses'])
    forecasts = dict(originals, r15=prev['forecasts']['candidate'], candidate=tips['candidate'])
    if not any(recipe['doses']['full'].values()):
        if forecasts['candidate'] != forecasts['r15'] or not _same_quality(quality, prev['quality']):
            raise ValueError('zero-dose comparison diverged from the signed R15 first')
        quality = prev['quality']
    for arm in forecasts.values():
        r15._decisions(arm, None, prev['line'])
    delta = x - np.asarray(prev['features'])
    features = {side: {name: dict(before=prev['features'][i][j], after=float(x[i, j]), delta=float(delta[i, j]))
                       for j, name in enumerate(study.FEATURES)} for i, side in enumerate(SIDES)}
    copied = {k: prev[k] for k in ('home_name', 'away_name', 'home_id', 'away_id', 'line', 'prices', 'market_evidence',
              'market_mode', 'source_market_status', 'fixture_sources', 'source_slots', 'odds_source_first_issue', 'r7_first_issue',
              'r10_first_issue', 'recipe_sha256', 'r7_forward_sha256', 'r10_recipe_sha256', 'study_inputs_sha256', 'fit_sha256',
              'rosters_sha256', 'roster_manifest_sha256', 'panel_sha256', 'training_through', 'doses')}
    return dict(copied, version=VERSION, formal_kpi_eligible=False, protocol_sha256=cutoff_study.PROTOCOL_SHA,
                r15_registration_sha256=prev['registration_sha256'], r15_first_sha256=signed['record_sha256'],
                r15_recorded_at=signed['recorded_at'], r15_prior_display=prev.get('prior_display'), reference='signed_pre_update_R15_first',
                r15_inputs_cutoff_at=prev['inputs_cutoff_at'], inputs_cutoff_at=at.isoformat(), history_cutoff=stop,
                r7_grid=prior.tolist(), r7_grid_sha256=digest(prior.tolist()), forecasts=forecasts, quality=quality, r15_quality=prev['quality'],
                features=features, process_before=prev['process'], process_after=process,
                features_changed=not np.allclose(x, prev['features'], rtol=1e-10, atol=1e-12),
                new_history=refs)


def _prepare(folders, folder, roster_file, firsts, intake, display, annual_path, at, temp, *, allow_pending=False):
    window, early, weekend = _windows(display)
    if at >= min(aware(f['kickoff_at']) for f in weekend.values()):
        raise ValueError('cutoff reaches the earliest registered weekend kickoff')
    plan, originals, old_at = _originals(firsts, temp, weekend, early, at)
    evidence, new_rosters = _evidence(intake, display, early, at, annual_path)
    if evidence['pending'] and not allow_pending:
        raise ValueError('awaiting dual FT90 for original J1 fixtures: ' + ', '.join(evidence['pending']))
    if set(new_rosters) != early.keys() - set(evidence['pending']):
        raise ValueError('prior fixture coverage and roster calendar slots differ')
    spec = plan['payload']['identity']
    documents = {k: _document(p / 'manifest.json') for k, p in folders.items()}
    manifests = {k: d for k, (d, _) in documents.items()}
    if {k: h for k, (_, h) in documents.items()} != spec['archive_manifest_sha256']:
        raise ValueError('original R15 source archive manifests differ')
    sources = r15._sources(temp, folders, manifests)
    parents = {k: b.registration['record_sha256'] for k, b in sources[0].items()}
    r10_plan = sources[0]['r10'].registration
    all_j1 = {m: f for m, f in r10_plan['payload']['fixtures'].items() if f['cohort'] == CODE}
    if (parents != spec['source_registrations'] or parents['r10'] != window['payload']['identity']['original_registration']
            or all_j1.keys() != (early.keys() | weekend.keys()) or any(
                fixture_identity(f) != fixture_identity(all_j1[m]) for m, f in (early | weekend).items())):
        raise ValueError('original R5/R7/R10 registrations or weekend identities differ')
    if spec.get('prior_display') and spec['prior_display'] != r15._display_slots(display, r10_plan, old_at):
        raise ValueError('original R15 display reference differs from #51')
    games, rosters, anchors, recipe, baseline, bound, coverage = _training(folders, manifests, folder, roster_file, spec, old_at, temp)
    if {g['id'] for g in games} & (early.keys() | weekend.keys()):
        raise ValueError('historical R15 calendar overlaps a prospective fixture')
    history = study.LineupHistory(rosters | new_rosters)
    history.walk(games, anchors)
    targets = {m: _reproduce(f, originals[m], sources, recipe, baseline, history, spec, old_at) for m, f in weekend.items()}
    additions, original_anchors, refs = _prior_anchors(early, sources, baseline, bound['r7_forward_sha256'])
    identity = dict(version=VERSION, protocol_sha256=cutoff_study.PROTOCOL_SHA, formal_kpi_eligible=False, competitions=[CODE],
                    pairs={'vs_signed_r15': ['r15', 'candidate']}, inputs_cutoff_at=at.isoformat(), window='nov28_29',
                    display_registration_sha256=window['record_sha256'], source_registrations=parents,
                    archive_manifest_sha256=spec['archive_manifest_sha256'], r15_registration_sha256=plan['record_sha256'],
                    r15_inputs=bound, r15_history_coverage=coverage, r15_selected=spec['selected'], doses=recipe['doses'],
                    evidence=evidence, training_through=recipe['through'], r15_position_tokens=study.POSITIONS,
                    slots_per_competition=3, scheduled=len(weekend),
                    unpriced=sorted(m for m, r in originals.items() if not any(r['payload']['prices'].values())),
                    without_total_line=sorted(m for m, r in originals.items() if r['payload']['line'] is None))
    if evidence['pending']:
        identity.update(eligible=0, filled=0, prior_anchors=refs, reproduced_r15={m: dict(
            r15_first_sha256=originals[m]['record_sha256'], r7_grid_sha256=digest(grid.tolist()),
            candidate=originals[m]['payload']['forecasts']['candidate'], quality=originals[m]['payload']['quality'])
            for m, (_, grid) in targets.items()})
        return weekend, {}, identity
    _extend(history, early, evidence, additions, original_anchors, refs)
    payloads = {m: _updated(game, prior, originals[m], recipe, history, refs, at) for m, (game, prior) in targets.items()}
    selected = r15._seats(payloads)
    ranking = sorted(weekend, key=lambda m: (-payloads[m]['quality']['p_first'], m))
    old_rank = sorted(weekend, key=lambda m: (-originals[m]['payload']['quality']['p_first'], m))
    original_seats = {s['match_id']: s['seat'] for s in spec['selected'][CODE]}
    for rank, mid in enumerate(ranking, 1):
        original_rank = old_rank.index(mid) + 1
        payloads[mid].update(rank=rank, seat=rank if rank <= 3 else None, r15_rank=original_rank,
                             r15_seat=original_seats.get(mid))
    identity.update(selected={CODE: selected}, eligible=len(ranking), filled=len(selected),
                    r15_ranking=old_rank, ranking=ranking, new_history_sha256=digest(refs),
                    payload_sha256={m: digest(p) for m, p in payloads.items()})
    return weekend, payloads, identity


def _check_issued(canonical, fixtures, payloads, identity):
    folder = canonical / 'issued'
    index = read(folder / 'receipts.json')
    plan = _first(ledger._read(folder, index['registration']), TRACK + '-registration')
    body = dict(track=TRACK, fixtures=fixtures, identity=identity)
    earliest = min(aware(f['kickoff_at']) for f in fixtures.values())
    if (plan['payload'] != body or plan['match_id'] != TRACK + '-plan:' + digest(body)
            or plan['idempotency_key'] != digest(body) or aware(plan['recorded_at']) < aware(identity['inputs_cutoff_at'])
            or set(index['matches']) != set(fixtures)):
        raise ValueError('different canonical R18 first registration or population')

    def check_ref(ref, record):
        scope = {k: record[k] for k in ('match_id', 'track', 'stage')}
        if (read(canonical / 'scopes' / digest(scope) / '000001.json') != record
                or ref['file'] != record['freeze_id'] + '.json' or ref['recorded_at'] != record['recorded_at']
                or ref['recorded_at_utc'] != aware(record['recorded_at']).astimezone(timezone.utc).isoformat()
                or aware(record['recorded_at']) >= earliest):
            raise ValueError('invalid R18 first receipt, UTC issue time or canonical record')

    check_ref(index['registration'], plan)
    for mid, ref in index['matches'].items():
        rec = _first(ledger._read(folder, ref), TRACK)
        if (rec['match_id'] != mid or rec['idempotency_key'] != plan['record_sha256']
                or rec['fixture'] != dict(fixtures[mid], cutoff_at=plan['recorded_at'])
                or rec['payload'] != dict(payloads[mid], registration_sha256=plan['record_sha256'])
                or identity['payload_sha256'][mid] != digest(payloads[mid])):
            raise ValueError('canonical R18 match differs from its registered first: ' + mid)
        check_ref(ref, rec)
    return index


def _save(canonical, fixtures, payloads, identity):
    earliest = min(aware(f['kickoff_at']) for f in fixtures.values())
    if canonical.exists():
        return _check_issued(canonical, fixtures, payloads, identity)
    if _now() >= earliest:
        raise ValueError('too late to open the weekend batch')
    with TemporaryDirectory(dir=canonical.parent, prefix='.' + TRACK + '-') as tmp:
        batch = Path(tmp) / 'batch'
        folder, scopes = batch / 'issued', batch / 'scopes'
        plan = ledger.register(list(fixtures.values()), identity, TRACK, folder, freeze_root=scopes)
        matches = {m: ledger.issue(plan, dict(f, cutoff_at=plan['recorded_at']), payloads[m], TRACK, folder, freeze_root=scopes)
                   for m, f in fixtures.items()}
        for ref in [plan, *matches.values()]:
            ref['recorded_at_utc'] = aware(ref['recorded_at']).astimezone(timezone.utc).isoformat()
        atomic_json(folder / 'receipts.json', dict(registration=plan, matches=matches))
        index = _check_issued(batch, fixtures, payloads, identity)
        if _now() >= earliest:
            raise ValueError('batch crossed the earliest weekend kickoff before publication')
        batch.rename(canonical)
    return index


def _export(canonical, out):
    source = canonical / 'issued'
    if out.exists() and any(out.iterdir()):
        names = {p.name for p in source.iterdir()}
        if {p.name for p in out.iterdir()} != names or any(
                not (out / n).is_file() or sha(out / n) != sha(source / n) for n in names):
            raise ValueError('existing output differs from the canonical firsts')
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent, prefix='.r18-export-') as tmp:
        stage = Path(tmp) / 'issued'
        shutil.copytree(source, stage)
        stage.rename(out)


def run(r5, r7_dir, r10, study_dir, rosters, r15_firsts, intake, display_windows, out=None, *,
        annual_calendar=None, issue=False, freeze_root=ledger.DEFAULT_ROOT):
    folders = dict(r5=Path(r5), r7=Path(r7_dir), r10=Path(r10))
    fixed = (folders, Path(study_dir), Path(rosters), Path(r15_firsts), Path(intake).resolve(), Path(display_windows),
             Path(annual_calendar) if annual_calendar else None)
    if not issue:
        with TemporaryDirectory() as tmp:
            f, p, identity = _prepare(*fixed, _now(), Path(tmp), allow_pending=True)
        return dict(mode='preview' if p else 'preview_pending', fixtures=f, payloads=p, identity=identity)
    if out is None:
        raise ValueError('an R18 output directory is required for issuance')
    out = Path(out).resolve()
    store = Path(freeze_root).resolve()
    canonical = store / TRACK
    protected = [Path(p).resolve() for p in (*folders.values(), study_dir, r15_firsts, intake, display_windows)]
    if (out.is_relative_to(canonical) or canonical.is_relative_to(out)
            or any(out.is_relative_to(p) or canonical.is_relative_to(p) for p in protected)):
        raise ValueError('R18 output and canonical store must be separate from the original archives')
    # Validate all prerequisites before creating even the track lock in a new store.
    # A competing issuer may finish during preparation; in that case replay its original cutoff.
    with TemporaryDirectory() as tmp:
        old = read(canonical / 'issued/receipts.json') if canonical.exists() else None
        at = aware(ledger._read(canonical / 'issued', old['registration'])['payload']['identity']['inputs_cutoff_at']) if old else _now()
        prepared = _prepare(*fixed, at, Path(tmp))
    store.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(store / ('.' + TRACK + '.lock')):
        if not old and canonical.exists():
            with TemporaryDirectory() as tmp:
                ref = read(canonical / 'issued/receipts.json')['registration']
                at = aware(ledger._read(canonical / 'issued', ref)['payload']['identity']['inputs_cutoff_at'])
                prepared = _prepare(*fixed, at, Path(tmp))
        if out.exists() and (not out.is_dir() or any(out.iterdir()) and not canonical.exists()):
            raise ValueError('output already contains an unbound first or other files')
        index = _save(canonical, *prepared)
        _export(canonical, out)
        return dict(mode='issued', track=TRACK, registration=index['registration'], matches=index['matches'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('intake', 'display-windows'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('r5', 'r7', 'r10', 'study', 'rosters', 'r15-firsts', 'out'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--annual-calendar', type=Path, help='relocated raw annual ESPN board with the original byte hash')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check-evidence', action='store_true', help='read only the calendar and seven source pairs')
    modes.add_argument('--issue', action='store_true', help='sign all ten research firsts before the earliest kickoff')
    parser.add_argument('--freeze-root', type=Path, default=Path(ledger.DEFAULT_ROOT), help='parent of the independent immutable R18 store')
    a = parser.parse_args()
    if a.check_evidence:
        _, early, _ = _windows(a.display_windows)
        at = _now()
        report, _ = _evidence(a.intake.resolve(), a.display_windows, early, at, a.annual_calendar)
        result = dict(mode='evidence_only', as_of_utc=at.isoformat(), **report)
    else:
        required = ('r5', 'r7', 'r10', 'study', 'rosters', 'r15_firsts') + (('out',) if a.issue else ())
        missing = ['--' + k.replace('_', '-') for k in required if getattr(a, k) is None]
        if missing:
            parser.error('required for this mode: ' + ', '.join(missing))
        result = run(a.r5, a.r7, a.r10, a.study, a.rosters, a.r15_firsts, a.intake, a.display_windows, a.out,
                     annual_calendar=a.annual_calendar, issue=a.issue, freeze_root=a.freeze_root)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
