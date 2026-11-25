"""Check or issue R23 on the 45 original R22 first-issue fixtures."""

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta, timezone
import json
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import numpy as np

from publication import deliver
from research.analysis_freeze import DEFAULT_ROOT, aware, digest, verify
from storage_lock import exclusive_lock
from . import draw_preserving_forward as r22
from . import penalty_opportunity as model
from . import penalty_opportunity_study as study
from . import ranked_score_study as ranked
from . import territory_forward as source
from .goal_state_study import read_lines, sha
from .paired_kpi_inference import _decisions


TRACK = 'research-r23-penalty-opportunity-20261125'
R22_POPULATION = 45
PAIRS = {f'{arm}_vs_candidate': [arm, 'candidate'] for arm in ('r7', 'r22', 'market', 'full_dose')}
DIRECTIONS = ('wdl', 'total_side', 'total_line')
FIRST_ARCHIVES = (
    ('r5', 'forward-first-issues.tar.gz', 'issued'),
    ('r7', 'first-issues.tar.gz', ''),
    ('r10', 'forward/first-issues.tar.gz', ''),
)
ledger = source.ledger
BEIJING = ZoneInfo('Asia/Shanghai')


@dataclass
class League:
    baseline: dict
    baseline_sha256: str
    calendar: list
    provenance: dict


@dataclass
class LockedInputs:
    identity: dict
    fixtures: dict
    parents: dict
    missing: dict
    leagues: dict
    events: list


def _leagues(roots, manifests, events, work):
    source.verified_archive(roots['r11'], manifests['r11'], 'extension.tar.gz', work / 'extension')
    extension = defaultdict(list)
    for game in read_lines(work / 'extension/fixture-panels.jsonl'):
        extension[game['competition']].append(game)
    clocks = {s['league']: s['acquired_at_utc'] for s in source.read(work / 'extension/manifest.json')['sources']}
    fetched = defaultdict(list)
    for row in events:
        clock = row.get('source_fetched_at_utc') or row.get('source_fetched_at')
        if clock:
            fetched[source.LEAGUES.get(row['competition'], row['competition'])].append(aware(clock))

    leagues = {}
    for code in source.CODES:
        for key in ('r5', 'r7'):
            source.verified_archive(roots[key], manifests[key], f'models/{code}.tar.gz', work / code / key)
        panel = work / code / 'r5/panel.jsonl'
        folder = work / code / 'r7/main'
        baseline, baseline_hash = source._document(folder / 'forward.json')
        fit, fit_hash = source._document(folder / 'fit.json')
        panel_hash = sha(panel)
        if (baseline['competition'] != code or baseline['version'] != source.r7.model.VERSION
                or baseline['fit_sha256'] != fit_hash or fit['source_panel_sha256'] != panel_hash):
            raise ValueError('R7 baseline or R5 panel differs from its locked fit: ' + code)
        through = max(g['date'] for g in extension[code])
        calendar = source._calendar(panel, extension[code], dict(competition=code, through=through), baseline)
        leagues[code] = League(baseline, baseline_hash, calendar, dict(
            panel_sha256=panel_hash, r7_training_through=baseline['through'], extension_acquired_at=clocks[code],
            events_fetched_through=max(fetched[code]).isoformat() if fetched[code] else None))
    return leagues


def _market_gaps(record):
    odds = record['payload']
    prices, evidence = odds['prices'], odds['market_evidence']
    line = odds['line']
    gaps = []
    for market, sides in (('h2h', ('home', 'draw', 'away')), ('total', ('over', 'under'))):
        if prices[market] is None:
            gaps.append(market + '_prices_missing')
            if market == 'total' and line is not None:
                raise ValueError('actual totals line has no original prices: ' + record['match_id'])
            continue
        quote = evidence.get(market) or {}
        if (source.base._prices(prices[market], sides) is None
                or not re.fullmatch(r'[0-9a-f]{64}', quote.get('source_sha256') or '')
                or not quote.get('bookmaker') or not quote.get('source_url') or not quote.get('observed_at')
                or (market == 'total' and line is None)):
            raise ValueError('invalid original R5 prices or source evidence: ' + record['match_id'])
        if not quote.get('quoted_at'):
            gaps.append(market + '_quote_clock_missing')
        elif aware(quote['quoted_at']) > aware(quote['observed_at']):
            raise ValueError('quote time follows its original observation: ' + record['match_id'])
    return gaps


def _parent(fixture, batches, earlier, league, r22_recipe):
    mid = fixture['match_id']
    last_prematch = aware(fixture['kickoff_at']) - timedelta(microseconds=1)
    old = earlier.match(fixture, last_prematch)
    game, raw, original, previous = source.verified_parents(
        fixture, batches, league.baseline, dict(baseline_sha256=league.baseline_sha256),
        aware(old['fixture']['cutoff_at']))
    carried, odds = old['payload'], raw['payload']
    control = original['payload']['forecasts']['candidate']
    if (carried['version'] != r22.model.VERSION or carried['formal_kpi_eligible'] is not False
            or carried['r7_first_issue'] != original['record_sha256']
            or carried['odds_source_first_issue'] != raw['record_sha256']
            or carried['odds_cutoff_at'] != raw['fixture']['cutoff_at']
            or carried['baseline_sha256'] != league.baseline_sha256 or carried['recipe_sha256'] != r22_recipe
            or carried['training_through'] != league.calendar[-1]['date']
            or date.fromisoformat(carried['training_through']) >= aware(old['fixture']['cutoff_at']).astimezone(timezone.utc).date()
            or carried['forecasts']['r7'] != control or carried['forecasts']['market'] != odds['forecasts']['market']
            or any(carried[k] != odds[k] for k in ('prices', 'line', 'market_evidence'))):
        raise ValueError('R22 first issue is attached to different parents or prices: ' + mid)
    line = odds['line']
    for pick in (control, carried['forecasts']['candidate'], odds['forecasts']['market']):
        _decisions(pick, None, line)
    if control['total_line'] != line or (line is None and control['total_side'] is not None):
        raise ValueError('R7 directions differ from the original actual line: ' + mid)
    return game, raw, original, previous, old, _market_gaps(raw)


def _locked(data_root, events_path, work):
    spec, stamp = source._document(study.PROTOCOL)
    if (stamp != study.PROTOCOL_SHA or spec['version'] != model.VERSION
            or tuple(spec['competitions']) != source.CODES):
        raise ValueError('R23 protocol differs from the preregistration')
    package, manifest, _, roots, manifests, event_archive, lock = study._archives(data_root)
    events = study._observations(event_archive, events_path)
    hashes = {key: sha(folder / 'manifest.json') for key, folder in roots.items()}
    batches = []
    for key, archive, prefix in FIRST_ARCHIVES:
        source.verified_archive(roots[key], manifests[key], archive, work / key)
        batches.append(source.FirstIssues.load(work / key / prefix))
    study._checked(package, manifest, 'forward/receipts.json')
    firsts = source.FirstIssues.load(package / 'forward')
    reg = firsts.registration
    parent = reg['payload']['identity']
    fixtures = reg['payload']['fixtures']
    registrations = {key + '_registration': batch.registration['record_sha256']
                     for (key, _, _), batch in zip(FIRST_ARCHIVES, batches, strict=True)}
    r5_plan, r7_plan, r10_plan = (batch.registration['payload']['identity'] for batch in batches)
    if (reg['payload']['track'] != r22.TRACK or reg['record_sha256'] != lock['official_forward_registration_sha256']
            or parent['version'] != r22.model.VERSION or parent['archive_manifest_sha256'] != hashes
            or parent['events_sha256'] != study.EVENTS_SHA or len(fixtures) != R22_POPULATION
            or {f['cohort'] for f in fixtures.values()} != set(source.CODES)
            or any(parent.get(k) != v for k, v in registrations.items())
            or (r5_plan['model'], r7_plan['version'], r10_plan['version']) !=
            (source.base.VERSION, source.r7.model.VERSION, source.decision.VERSION)):
        raise ValueError('R22 original registration, 45-fixture scope or parent registrations differ')
    leagues = _leagues(roots, manifests, events, work)
    parent_recipes = parent['recipe_sha256']
    for code, league in leagues.items():
        if (r7_plan['recipe_sha256'][code] != league.baseline_sha256
                or sha(study._checked(package, manifest, f'replay/reference/main/{code}/fit.json')) != parent_recipes[code]):
            raise ValueError('R7 or R22 recipe differs from the original registration: ' + code)

    parents, missing = {}, {}
    all_sources = list(zip((k for k, _, _ in FIRST_ARCHIVES), batches, strict=True)) + [('r22', firsts)]
    for mid, fixture in fixtures.items():
        if mid != fixture['match_id'] or fixture['home'] == fixture['away']:
            raise ValueError('R22 fixture registration identity mismatch: ' + mid)
        absent = [key + '_first_issue_missing' for key, batch in all_sources
                  if mid not in batch.matches or not (batch.folder / batch.matches[mid]['file']).is_file()]
        if absent:
            missing[mid] = absent
            continue
        study._checked(package, manifest, 'forward/' + firsts.matches[mid]['file'])
        code = fixture['cohort']
        parents[mid] = _parent(fixture, batches, firsts, leagues[code], parent_recipes[code])
    identity = dict(version=model.VERSION, competitions=list(source.CODES), formal_kpi_eligible=False, pairs=PAIRS,
                    protocol_sha256=stamp, events_sha256=study.EVENTS_SHA, archive_manifest_sha256=hashes,
                    r22_data_commit=study.R22_DATA, r22_source_commit=study.R22_SOURCE,
                    r22_manifest_sha256=study.R22_MANIFEST_SHA, r22_registration=reg['record_sha256'],
                    r22_source_lock_sha256=manifest['files']['source-lock.json'],
                    r22_inputs_sha256=manifest['files']['replay/reference/inputs.json'],
                    r21_inputs_sha256=parent['r21_study_inputs_sha256'],
                    r14_archive_sha256=lock['r21_parent']['understat_archive_sha256'],
                    extension_sha256=manifests['r11']['files']['extension.tar.gz'],
                    r22_recipe_sha256=parent_recipes, **registrations)
    return LockedInputs(identity, fixtures, parents, missing, leagues, events)


def _recipes(folder, locked):
    inputs, inputs_hash = source._document(folder / 'inputs.json')
    for key in ('protocol_sha256', 'events_sha256'):
        if inputs[key] != locked.identity[key]:
            raise ValueError('training inputs differ from the locked ' + key)
    known_inputs = dict(locked.identity, core_sha256=sha(Path(model.__file__)), study_sha256=sha(Path(study.__file__)))
    for key in ('version', 'archive_manifest_sha256', 'r22_manifest_sha256', 'r22_data_commit', 'r22_source_commit',
                'r22_source_lock_sha256', 'r22_inputs_sha256', 'r21_inputs_sha256', 'extension_sha256',
                'r14_archive_sha256', 'core_sha256', 'study_sha256'):
        if key in inputs and inputs[key] != known_inputs[key]:
            raise ValueError('training inputs differ from the locked ' + key)

    recipes, hashes, designs = {}, {}, {}
    for code, league in locked.leagues.items():
        directory = folder / 'main' / code
        recipe, recipe_hash = source._document(directory / 'forward.json')
        fitted, dose = recipe['fitted'], recipe['dose']
        beta = fitted['beta']
        artifacts = recipe.get('inputs', {})
        baseline_sha = recipe.get('baseline_sha256', artifacts.get('r7_recipe_sha256'))
        if (recipe['version'] != model.VERSION or recipe['competition'] != code
                or recipe['through'] != league.calendar[-1]['date']
                or baseline_sha != league.baseline_sha256
                or recipe.get('training_games', len(league.calendar)) != len(league.calendar)
                or fitted.get('training_rows', len(league.calendar)) != len(league.calendar)
                or recipe.get('protocol_sha256', locked.identity['protocol_sha256']) != locked.identity['protocol_sha256']
                or type(dose) not in (int, float) or dose not in (0., .5, 1.)
                or fitted.get('version', model.VERSION) != model.VERSION
                or tuple(fitted.get('features', model.FEATURES)) != tuple(model.FEATURES)
                or not isinstance(beta, list) or len(beta) != 2
                or any(type(b) not in (int, float) or not -2 <= b <= 2 for b in beta)):
            raise ValueError('invalid R23 recipe, through-extension date or R7 baseline hash: ' + code)
        bindings = dict(panel_sha256=league.provenance['panel_sha256'], r7_recipe_sha256=league.baseline_sha256,
                        r7_fit_sha256=league.baseline['fit_sha256'], r22_fit_sha256=locked.identity['r22_recipe_sha256'][code])
        training_inputs = inputs.get('competitions', {}).get(code, {})
        if (any(declared.get(key, value) != value for declared in (artifacts, training_inputs)
                for key, value in bindings.items())
                or any(training_inputs.get(key, value) != value for key, value in artifacts.items())):
            raise ValueError('R23 recipe inputs differ from the locked study: ' + code)
        if 'fit_sha256' in recipe:
            fit, fit_hash = source._document(directory / 'fit.json')
            if (fit_hash != recipe['fit_sha256'] or fit.get('dose', dose) != dose
                    or fit.get('inputs', artifacts) != artifacts):
                raise ValueError('R23 recipe differs from its validation fit: ' + code)
        if 'recipe_sha256' in inputs and inputs['recipe_sha256'][code] != recipe_hash:
            raise ValueError('R23 recipe differs from the training input manifest: ' + code)
        recipes[code], hashes[code] = recipe, recipe_hash
        design = model.Design(locked.events, code)
        design.walk(league.calendar)
        designs[code] = design
    return recipes, designs, dict(study_inputs_sha256=inputs_hash, recipe_sha256=hashes)


def _coverage(locked, cutoff):
    fixtures = {}
    for mid, fixture in locked.fixtures.items():
        missing = list(locked.missing.get(mid, ()))
        detail = dict(fixture=fixture, missing_inputs=missing)
        if mid in locked.parents:
            _, raw, original, previous, old, gaps = locked.parents[mid]
            detail['parent_digests'] = {f'{key}_first_issue': r['record_sha256'] for key, r in (
                ('odds_source', raw), ('r7', original), ('r10', previous), ('r22', old))}
            detail['market_gaps'] = gaps
            detail['original_line'] = raw['payload']['line']
            if aware(old['recorded_at']) > cutoff:
                missing.append('r22_first_issue_after_cutoff')
        league = locked.leagues[fixture['cohort']]
        if date.fromisoformat(league.calendar[-1]['date']) >= cutoff.date():
            missing.append('through_extension_reaches_cutoff_date')
        for key in ('extension_acquired_at', 'events_fetched_through'):
            if league.provenance[key] and aware(league.provenance[key]) > cutoff:
                missing.append(key + '_after_cutoff')
        detail['disposition'] = ('already_started' if aware(fixture['kickoff_at']) <= cutoff else
                                 'unavailable_inputs' if missing else 'eligible')
        fixtures[mid] = detail
    return fixtures


def _context(design, game, cutoff):
    day = cutoff.isoformat()
    feature, exposure = design.context(game, day)
    feature = np.asarray(feature, dtype=float)
    if (feature.shape != (4,) or not np.isfinite(feature).all() or (np.abs(feature) > .75).any()
            or exposure['cutoff'] != day or exposure.get('date', game['date']) != game['date']
            or exposure.get('competition', game['competition']) != game['competition']):
        raise ValueError('R23 forecast feature or as-of cutoff differs: ' + game['id'])
    mean = exposure['league_mean']
    if mean is not None and (not math.isfinite(mean) or mean < 0):
        raise ValueError('invalid R23 as-of league mean: ' + game['id'])
    for side, columns in (('home', (0, 3)), ('away', (2, 1))):
        detail = exposure[side]
        n, games = detail['n'], detail['games']
        if (type(n) is not int or not 0 <= n <= len(games) <= 12
                or any(date.fromisoformat(g['date']) >= cutoff for g in games)
                or n != sum(g['valid'] for g in games)
                or (n and (not detail.get('source_team_id') or mean is None))
                or (not n and any(feature[i] != 0. for i in columns))):
            raise ValueError('invalid R23 as-of club history: ' + game['id'] + '/' + side)
    return feature, exposure


def _forecasts(population, locked, recipes, designs, study_identity, cutoff):
    day = cutoff.date().isoformat()
    payloads = {}
    for mid, fixture in population.items():
        code = fixture['cohort']
        game, raw, original, previous, old, gaps = locked.parents[mid]
        odds = raw['payload']
        control = original['payload']['forecasts']['candidate']
        recipe = recipes[code]
        league = locked.leagues[code]
        feature, exposure = _context(designs[code], game, cutoff.date())
        prior = source.r7.matrix(game, league.baseline)
        full = model.matrix(prior, feature, recipe['fitted'])
        line = odds['line']
        picks = dict(r7=control, r22=old['payload']['forecasts']['candidate'], market=odds['forecasts']['market'])
        quality = {}
        for arm, dose in (('candidate', recipe['dose']), ('full_dose', 1.)):
            pick, grid = model.choices(prior, full, dose, control, line)
            _decisions(pick, None, line)
            if (any(pick[key] != control[key] for key in DIRECTIONS)
                    or pick['primary'] != pick['top3'][0]
                    or (np.array_equal(grid, prior) and pick != control)
                    or (dose == 0 and not np.array_equal(grid, prior))):
                raise ValueError('R23 score choices or inherited R7 directions differ: ' + mid)
            cells = [tuple(map(int, score.split('-'))) for score in pick['top3']]
            values = [float(grid[cell]) for cell in cells]
            if (not np.isfinite(grid).all() or (grid < 0).any()
                    or not math.isclose(float(grid.sum()), 1., rel_tol=1e-9)):
                raise ValueError('R23 did not produce a normalized score grid: ' + mid)
            picks[arm] = pick
            quality[arm] = dict(p_first=values[0], p_three=sum(values), p_ordered=values)
        payloads[mid] = dict(
            version=model.VERSION, formal_kpi_eligible=False, home_name=game['home'], away_name=game['away'],
            forecasts=picks, line=line, prices=odds['prices'], market_evidence=odds['market_evidence'],
            market_gaps=gaps, market_basis='original_r5_first_issue', fixture_sources=odds.get('fixture_sources'),
            quality=quality['candidate'], full_dose_quality=quality['full_dose'],
            features=feature.tolist(), process=exposure, score_dose=recipe['dose'], beta=recipe['fitted']['beta'],
            training_through=recipe['through'], history_cutoff=day, baseline_sha256=league.baseline_sha256,
            recipe_sha256=study_identity['recipe_sha256'][code], protocol_sha256=locked.identity['protocol_sha256'],
            events_sha256=locked.identity['events_sha256'], r22_recipe_sha256=old['payload']['recipe_sha256'],
            odds_source_first_issue=raw['record_sha256'], odds_cutoff_at=raw['fixture']['cutoff_at'],
            r7_first_issue=original['record_sha256'], r10_first_issue=previous['record_sha256'],
            r22_first_issue=old['record_sha256'], r5_recorded_at=raw['recorded_at'],
            r7_recorded_at=original['recorded_at'], r22_recorded_at=old['recorded_at'], **league.provenance)
    return payloads


def _prepare(locked, cutoff, trained=None):
    coverage = _coverage(locked, cutoff)
    population = {mid: row['fixture'] for mid, row in coverage.items() if row['disposition'] == 'eligible'}
    population = dict(sorted(population.items(), key=lambda item: (aware(item[1]['kickoff_at']), item[0])))
    payloads = _forecasts(population, locked, *trained, cutoff) if trained is not None else {}
    return coverage, population, payloads


def _report(coverage, payloads, cutoff):
    competitions = {}
    for code in source.CODES:
        rows = [r for r in coverage.values() if r['fixture']['cohort'] == code]
        payload = [payloads[r['fixture']['match_id']] for r in rows if r['fixture']['match_id'] in payloads]
        totals = {key: sum(r['disposition'] == key for r in rows)
                  for key in ('eligible', 'already_started', 'unavailable_inputs')}
        both = sum(all(p['process'][s]['n'] > 0 for s in ('home', 'away')) for p in payload)
        competitions[code] = dict(original=len(rows), **totals, both_clubs=both if payloads else None,
                                  neutral_fallback=len(payload) - both if payloads else None,
                                  original_actual_lines=sum(r['disposition'] == 'eligible'
                                      and r['original_line'] is not None for r in rows))
    return dict(cutoff_at=cutoff, original_population=len(coverage), fixtures=coverage, competitions=competitions)


def check(data_root, events, training=None):
    """Validate the locked archives and optional completed training without issuing anything."""
    with TemporaryDirectory() as temp:
        locked = _locked(data_root, events, Path(temp))
        trained = _recipes(training, locked) if training is not None else None
        clock = aware(ledger._now())
        coverage, population, payloads = _prepare(locked, clock.astimezone(timezone.utc), trained)
    status = ('READY' if trained is not None and population else
              'LOCKED_INPUTS_VERIFIED' if trained is None else 'NO_ELIGIBLE_FIXTURES')
    return {'status': status, **locked.identity, **(trained[2] if trained is not None else {}),
            **_report(coverage, payloads, clock.astimezone(BEIJING).isoformat())}


def _save_once(path, value):
    deliver(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode())


def _check_firsts(locked, population, old, freeze_root, now):
    for mid in locked.fixtures:
        scope = dict(track=TRACK, stage='prematch', match_id=mid)
        path = freeze_root / digest(scope) / '000001.json'
        if path.exists():
            first = verify(source.read(path))
            if (old is None or mid not in population or first.get('idempotency_key') != old['record_sha256']
                    or first.get('evaluation_slot') != 'FIRST_PREMATCH'):
                raise ValueError('R23 already has a different first issue: ' + mid)
        elif mid in population and aware(population[mid]['kickoff_at']) <= now:
            raise ValueError('fixture started before its R23 first issue could be saved: ' + mid)


def _issue(locked, trained, out, ranks, freeze_root):
    old = source._existing(out, TRACK)
    now = aware(ledger._now()).astimezone(timezone.utc)
    cutoff = aware(old['recorded_at']).astimezone(timezone.utc) if old else now
    coverage, population, payloads = _prepare(locked, cutoff, trained)
    identity = dict(locked.identity, **trained[2], original_population=len(coverage), coverage=coverage,
                    already_started=[mid for mid, row in coverage.items() if row['disposition'] == 'already_started'])
    if old and (old['track'] != TRACK + '-registration'
                or old['payload'] != dict(track=TRACK, identity=identity, fixtures=population)):
        raise ValueError('inputs or population changed after R23 registration')
    if (ranks / 'plan.json').exists():
        rank_record = ledger._read(ranks, source.read(ranks / 'plan.json'))
        if (old is None or rank_record['payload']['identity']['source_registration'] != old['record_sha256']):
            raise ValueError('ranking output belongs to a different registration')
    _check_firsts(locked, population, old, freeze_root, now)
    if not population:
        return dict(status='NO_ELIGIBLE_FIXTURES', **_report(coverage, payloads, cutoff.astimezone(BEIJING).isoformat()))

    plan = ledger.register(list(population.values()), identity, TRACK, out, freeze_root=freeze_root)
    issued = aware(plan['recorded_at']).astimezone(timezone.utc)
    if issued != cutoff:
        if _coverage(locked, issued) != coverage:
            raise ValueError('eligibility changed before the actual R23 registration cutoff')
        if issued.date() != cutoff.date():
            payloads = _forecasts(population, locked, *trained, issued)
    receipts = {mid: ledger.issue(plan, dict(fixture, cutoff_at=plan['recorded_at']), payloads[mid],
                                  TRACK, out, freeze_root=freeze_root) for mid, fixture in population.items()}
    _save_once(out / 'receipts.json', dict(registration=plan, matches=receipts))
    _save_once(out / 'pending.json', ledger.review(out, [], out, by_competition=True))
    _save_once(out / 'coverage.json', dict(registration=plan, **_report(coverage, payloads, plan['recorded_at'])))
    seats = ranked.lock(out, r22.ROOT / 'configs/analysis-scope.json', r22.ROOT / 'project-goals.json',
                        ranks, freeze_root=freeze_root)
    return dict(status='ISSUED', registration=plan, ranking=seats,
                original_population=len(coverage), first_issues=len(receipts))


def run(data_root, events, training, out, ranks, *, freeze_root=DEFAULT_ROOT):
    freeze_root = Path(freeze_root)
    protected = (data_root.resolve(), training.resolve())
    for path in (out, ranks, freeze_root):
        if any(path.resolve().is_relative_to(p) for p in protected):
            raise ValueError('R23 output must be outside the locked data and training directories')
    if out.resolve() == ranks.resolve():
        raise ValueError('R23 first issues and ranked seats need distinct directories')
    with TemporaryDirectory() as temp:
        locked = _locked(data_root, events, Path(temp))
        trained = _recipes(training, locked)
    out.mkdir(parents=True, exist_ok=True)
    freeze_root.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(freeze_root / ('.' + TRACK + '.lock')), exclusive_lock(out / '.batch.lock'):
        return _issue(locked, trained, out, ranks, freeze_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True, help='locked R22 data export')
    parser.add_argument('--events', type=Path, help='original archived Understat events.jsonl')
    parser.add_argument('--study', type=Path, help='completed R23 run with inputs.json and main/<code>/forward.json')
    parser.add_argument('--out', type=Path, help='new R23 first-issue directory')
    parser.add_argument('--ranks', type=Path, help='new ranked-seat directory')
    parser.add_argument('--freeze-root', type=Path, default=Path(DEFAULT_ROOT), help='canonical append-only freeze store')
    parser.add_argument('--check', action='store_true', help='read-only validation with the actual current clock')
    args = parser.parse_args()
    if args.check:
        result = check(args.data_root, args.events, args.study)
    else:
        if not (args.study and args.out and args.ranks):
            parser.error('issuance requires --study, --out and --ranks')
        result = run(args.data_root, args.events, args.study, args.out, args.ranks, freeze_root=args.freeze_root)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
