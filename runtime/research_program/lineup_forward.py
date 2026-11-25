"""Preview or explicitly issue R15 research firsts for the unstarted original R10 J1 slate."""

import argparse
from datetime import timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from publication import atomic_json
from research.analysis_freeze import DEFAULT_ROOT, aware, digest, verify
from storage_lock import exclusive_lock
from . import all_scope_forward as r5_model
from . import continuation_forward as r7
from . import display_windows
from . import goal_state_forward as ledger
from . import lineup_study as study
from .future_identity import fixture_identity
from .goal_state_study import read_lines, sha
from .paired_kpi_inference import _decisions
from .r8_score_study import read
from .r9_score_study import verified_archive
from .territory_forward import FirstIssues, _document

TRACK = 'research-r15-j1-prior-starters-20261125'
CODE = study.CODE
PAIRS = {f'vs_{arm}': [arm, 'candidate'] for arm in ('r5', 'r7', 'r10', 'market', 'form_only', 'form_matched_dose')}
PAIRS.update(fixed_increment=['fixed_form', 'fixed_full'], same_runtime=['local_r7', 'local_candidate'],
             first_only=['r7_first_only', 'first_only'])
MODELS = dict(r5='models/jpn.1.tar.gz', r7='models/jpn.1.tar.gz', r10='replay/main.tar.gz')
FIRSTS = dict(r5='forward-first-issues.tar.gz', r7='first-issues.tar.gz', r10='forward/first-issues.tar.gz')


def _sources(root, folders, manifests):
    batches = {}
    for key, name in FIRSTS.items():
        dest = root / 'issues' / key
        verified_archive(folders[key], manifests[key], name, dest)
        batches[key] = FirstIssues.load(dest / 'issued' if key == 'r5' else dest)
    plans = {k: v.registration['payload']['identity'] for k, v in batches.items()}
    if (plans['r5']['model'], plans['r7']['version'], plans['r10']['version']) != (
            study.base.VERSION, r7.model.VERSION, study.decision.VERSION):
        raise ValueError('incorrect original R5/R7/R10 registration')
    dest = root / 'observations'
    verified_archive(folders['r5'], manifests['r5'], 'forward-observations.tar.gz', dest)
    observations = dest / 'forward'
    indices = []
    for name, key in (('fixtures.jsonl', 'schedule_sha256'), ('market-latest-status.jsonl', 'market_status_sha256')):
        path = observations / name
        if sha(path) != plans['r5'][key]:
            raise ValueError('R5 observation differs from its registration: ' + name)
        rows = [r for r in read_lines(path) if r['competition'] == CODE]
        index = {r['match_id']: r for r in rows}
        if len(index) != len(rows):
            raise ValueError('duplicate J1 observation: ' + name)
        indices.append(index)
    return batches, observations, *indices


def _model_inputs(root, folders, manifests, folder, rosters, protocol):
    inputs, input_hash = _document(folder / 'inputs.json')
    fit, fit_hash = _document(folder / 'fit.json')
    recipe, recipe_hash = _document(folder / 'forward.json')
    registered, protocol_hash = _document(protocol)
    if (registered['version'] != study.VERSION or not inputs['protocol_sha256'] == protocol_hash == study.PROTOCOL_SHA256
            or (inputs['version'], inputs['competition'], inputs['mode'], inputs['formal_kpi_eligible']) != (
                study.VERSION, CODE, study.MODE, False)
            or any(recipe.get(k) != v for k, v in inputs.items())
            or (recipe['fit_sha256'], recipe['study_inputs_sha256'], recipe['features']) != (fit_hash, input_hash, list(study.FEATURES))
            or (fit['version'], fit['competition'], fit['features'], tuple(fit['bounds'])) != (
                study.VERSION, CODE, list(study.FEATURES), study.BOUNDS)
            or recipe['doses'] != fit['doses'] or set(recipe['doses']) != {'full', 'form'}):
        raise ValueError('R15 recipe, inputs, protocol or validation fit differs')
    for arm, doses in recipe['doses'].items():
        fitted = recipe['fitted'][arm]
        if (fitted['form_only'] is not (arm == 'form') or fitted['training_games'] != recipe['games']
                or set(doses) != {'score', 'wdl', 'total'}
                or any(type(d) not in (float, int) or d not in study.decision.DOSES for d in doses.values())):
            raise ValueError('invalid R15 forward weights or head doses')
    for key, name in MODELS.items():
        if inputs['archive_sha256'][key] != manifests[key]['files'][name]:
            raise ValueError('R15 fitted archive differs: ' + key)
        verified_archive(folders[key], manifests[key], name, root / key)
    panel, seventh, tenth = root / 'r5/panel.jsonl', root / 'r7/main', root / 'r10/main/jpn.1'
    baseline, baseline_hash = _document(seventh / 'forward.json')
    shot, shot_hash = _document(tenth / 'forward.json')
    panel_hash = sha(panel)
    if ((inputs['panel_sha256'], inputs['r7_fit_sha256'], inputs['r10_fit_sha256'], inputs['baseline_sha256'],
         inputs['schedule_index_sha256']) != (panel_hash, sha(seventh/'fit.json'), sha(tenth/'fit.json'), baseline_hash,
                                             manifests['r5']['files']['evidence/source-index.json'])
            or (baseline['version'], baseline['competition'], baseline['fit_sha256'], baseline['through']) != (
                r7.model.VERSION, CODE, inputs['r7_fit_sha256'], recipe['through'])
            or read(seventh / 'fit.json')['source_panel_sha256'] != panel_hash
            or (shot['version'], shot['competition'], shot['through'], shot['r7_forward_sha256'], shot['fit_sha256']) != (
                study.decision.VERSION, CODE, recipe['through'], baseline_hash, inputs['r10_fit_sha256'])):
        raise ValueError('R5 calendar or R7/R10 forward weights differ from R15 inputs')
    games = study.check_panel(panel, CODE, last=recipe['through'])
    if (len(games), games[-1]['date']) != (recipe['games'], recipe['through']):
        raise ValueError('R15 training does not end at the last completed R5 slot')
    study._schedule(games, folders['r5'], manifests['r5'])
    rows, roster_hash = study.read_rosters(rosters)
    stamp = study.snapshot_identity(rosters.parent / 'snapshot-manifest.json', rows, roster_hash, panel_hash,
                                    inputs['archive_sha256']['r5'])
    if roster_hash != inputs['rosters_sha256'] or any(inputs.get(k) != v for k, v in stamp.items()):
        raise ValueError('R15 normalized roster snapshot differs from the fitted inputs')
    snapshot_at = aware(stamp['roster_snapshot_at'])
    for row in rows:
        if any(aware(row[k]) > snapshot_at for k in ('source_query_time_utc', 'identity_source_query_time_utc') if row.get(k)):
            raise ValueError('roster source acquired after its input snapshot')
    checked, _, _ = study.roster_inputs(games, rows)
    history = study.LineupHistory(checked)
    anchors, _, _ = r7.model.inputs(games, CODE)
    history.walk(games, anchors)
    identity = dict(version=study.VERSION, competitions=[CODE], pairs=PAIRS, formal_kpi_eligible=False,
                    protocol_sha256=protocol_hash, study_inputs_sha256=input_hash, fit_sha256=fit_hash,
                    recipe_sha256={CODE: recipe_hash}, r7_forward_sha256=baseline_hash, r10_recipe_sha256=shot_hash,
                    panel_sha256=panel_hash, rosters_sha256=roster_hash, roster_coverage=inputs['roster_coverage'], **stamp)
    return recipe, baseline, history, identity


def _fixture_source(f, raw, scheduled, market, observations):
    mid = f['match_id']
    for row in (scheduled, market):
        origin = dict(match_id=row['match_id'], cohort=row['competition'], stage=row['stage'], kickoff_at=row['kickoff_at'],
                      home='espn:' + str(row['home_id']), away='espn:' + str(row['away_id']))
        if (fixture_identity(origin) != fixture_identity(f) or 'espn:' + str(row['event_id']) != mid
                or row['identity_verified'] is not True or aware(row['observed_at']) > aware(raw['fixture']['cutoff_at'])):
            raise ValueError('R5 source fixture identity or observation clock differs: ' + mid)
    p = raw['payload']
    if (scheduled['status'] != 'STATUS_SCHEDULED' or scheduled['score_basis'] != 'regulation_90'
            or tuple(scheduled[s] for s in study.SIDES) != (p['home_name'], p['away_name'])
            or p['fixture_sources'] != scheduled['sources'] or not scheduled['sources']):
        raise ValueError('R5 scheduled source differs: ' + mid)
    for source in scheduled['sources']:
        board, checksum = _document(observations / source['raw_path'])
        if checksum != source['sha256'] or [league['slug'] for league in board['leagues']] != [CODE]:
            raise ValueError('R5 original schedule bytes differ: ' + mid)
        events = [e for e in board['events'] if 'espn:' + str(e['id']) == mid]
        if len(events) != 1 or len(events[0]['competitions']) != 1:
            raise ValueError('missing or ambiguous event in R5 original schedule: ' + mid)
        fixture = events[0]['competitions'][0]
        sides = {p['homeAway']: str(p['team']['id']) for p in fixture['competitors']}
        if ('espn:' + str(fixture['id']) != mid or aware(fixture['date']) != aware(f['kickoff_at'])
                or fixture['status']['type']['name'] != 'STATUS_SCHEDULED' or len(fixture['competitors']) != 2
                or sides != {s: str(scheduled[s + '_id']) for s in study.SIDES}):
            raise ValueError('R5 raw scheduled fixture identity or status differs: ' + mid)
    hda, total, paired, label = r5_model.prices(market)
    if (p['prices'] != dict(h2h=hda, total=total) or (p['market_mode'] == 'paired') != paired or p['displayed_label'] != label):
        raise ValueError('R5 source prices differ from the original first: ' + mid)
    evidence = p['market_evidence']
    if not hda and not total and (evidence or p['forecasts']['market']):
        raise ValueError('R5 market control has no original prices: ' + mid)
    for key, price in (('h2h', hda), ('total', total)):
        if price is None:
            continue
        quote = evidence if 'source_sha256' in evidence else evidence.get(key, {})
        book = market.get(key) or {}
        obs = book.get(label) or book.get('close') or book.get('open') or market.get('observed_line_only') or {}
        source = obs.get('source') or {}
        if (not source.get('sha256') or source['sha256'] != quote.get('source_sha256')
                or (source.get('file') and sha(observations / source['file']) != source['sha256'])
                or not quote.get('observed_at') or obs.get('observed_at') != quote['observed_at']
                or obs.get('quoted_at') != quote.get('quoted_at') or obs.get('bookmaker') != quote.get('bookmaker')
                or (quote.get('quoted_at') and aware(quote['quoted_at']) > aware(quote['observed_at']))
                or any(aware(quote[k]) > aware(raw['fixture']['cutoff_at']) for k in ('observed_at', 'quoted_at') if quote.get(k))):
            raise ValueError('R5 quote source or clock differs: ' + mid)


def _forecast(f, sources, recipe, baseline, history, identity, cutoff, day):
    batches, observations, schedule, markets = sources
    mid = f['match_id']
    raw, published, shot = (batches[k].match(f, cutoff) for k in ('r5', 'r7', 'r10'))
    _fixture_source(f, raw, schedule[mid], markets[mid], observations)
    odds, seventh, tenth = (rec['payload'] for rec in (raw, published, shot))
    game = r7.frozen_game(f, odds)
    game.update(date=aware(f['kickoff_at']).astimezone(timezone.utc).date().isoformat(),
                home_id=str(schedule[mid]['home_id']), away_id=str(schedule[mid]['away_id']))
    line = study.base.actual_line(game)
    if (aware(raw['recorded_at']) > aware(published['fixture']['cutoff_at'])
            or aware(published['recorded_at']) > aware(shot['fixture']['cutoff_at'])
            or aware(tenth['archived_cutoff_at']) != aware(raw['fixture']['cutoff_at'])
            or not raw['record_sha256'] == seventh['input_sha256'] == tenth['odds_source_first_issue']
            or not identity['r7_forward_sha256'] == seventh['model_sha256'] == tenth['r7_forward_sha256']
            or identity['r10_recipe_sha256'] != tenth['recipe_sha256']
            or not odds['line'] == seventh['line'] == tenth['line'] == line
            or seventh['forecasts']['candidate'] != tenth['forecasts']['r7']
            or not odds['forecasts']['market'] == seventh['forecasts']['market'] == tenth['forecasts']['market']):
        raise ValueError('original prices or issued R7/R10 controls disagree: ' + mid)
    for key, row, weight in (('r7', published, 'model_sha256'), ('r10', shot, 'recipe_sha256')):
        if (batches[key].registration['payload']['identity']['recipe_sha256'][CODE] != row['payload'][weight]
                or row['payload']['training_through'] != recipe['through']
                or recipe['through'] >= aware(row['recorded_at']).astimezone(timezone.utc).date().isoformat()):
            raise ValueError('issued reference has a different recipe or training date: ' + mid)
    x, exposure = history.context(game, cutoff=day)
    for side in study.SIDES:
        if (exposure['cutoff'] != day or exposure[side]['team_id'] != game[side + '_id']
                or any(slot and (slot['date'] >= day or slot['date'] > recipe['through'])
                       for slot in (exposure[side]['previous'], exposure[side]['penultimate']))):
            raise ValueError('J1 history or team identity reaches outside cutoff: ' + mid)
    originals = dict(r5=odds['forecasts']['candidate'], market=odds['forecasts']['market'],
                     r7=seventh['forecasts']['candidate'], r10=tenth['forecasts']['candidate'])
    prior = r7.matrix(game, baseline)
    tips, quality, heads = study.predictions(prior, x, line, originals, recipe['fitted'], recipe['doses'])
    tips.update(first_only=dict(primary=tips['candidate']['primary']), r7_first_only=dict(primary=tips['r7']['primary']))
    for pick in tips.values():
        _decisions(pick, None, line)
    return dict(version=study.VERSION, formal_kpi_eligible=False, home_name=game['home'], away_name=game['away'],
                home_id=game['home_id'], away_id=game['away_id'], forecasts=tips, quality=quality, line=line,
                prices=odds['prices'], market_evidence=odds['market_evidence'], market_mode=odds['market_mode'],
                source_market_status=markets[mid]['market_status'], fixture_sources=odds['fixture_sources'],
                reference='original_prospective_R7_first', r7_track=published['track'],
                probability_basis='score-head matrix at the actually selected cells',
                head_sources={k: 'issued_R7' if m is prior else 'R15' for k, m in heads.items()},
                odds_source_first_issue=raw['record_sha256'], r7_first_issue=published['record_sha256'], r10_first_issue=shot['record_sha256'],
                source_slots={k: dict(slot=v['evaluation_slot'], cutoff_at=v['fixture']['cutoff_at'], recorded_at=v['recorded_at'])
                              for k, v in zip(('r5', 'r7', 'r10'), (raw, published, shot))},
                features=x.tolist(), process=exposure, inputs_cutoff_at=cutoff.isoformat(),
                history_cutoff=day, training_through=recipe['through'],
                doses=recipe['doses'], recipe_sha256=identity['recipe_sha256'][CODE],
                **{k: identity[k] for k in ('protocol_sha256', 'study_inputs_sha256', 'fit_sha256', 'rosters_sha256',
                       'roster_manifest_sha256', 'roster_snapshot_at', 'roster_coverage', 'panel_sha256', 'r7_forward_sha256', 'r10_recipe_sha256')})


def _seats(payloads):
    ranked = sorted(payloads, key=lambda mid: (-payloads[mid]['quality']['p_first'], mid))
    return [dict(seat=i, match_id=mid, primary=payloads[mid]['forecasts']['candidate']['primary'],
                 top3=payloads[mid]['forecasts']['candidate']['top3'], p_first=payloads[mid]['quality']['p_first'])
            for i, mid in enumerate(ranked[:3], 1)]


def _display_slots(folder, parent, cutoff):
    """Reference earlier Current/Test and public seats on their original separate track."""
    registration = folder / 'registration'
    plan = ledger._read(registration, read(registration / 'plan.json'))
    spec = plan['payload']['identity']
    original = {k: f for k, f in parent['payload']['fixtures'].items() if f['cohort'] == CODE}
    if (plan['track'] != display_windows.TRACK + '-registration' or spec['original_registration'] != parent['record_sha256']
            or plan['payload']['fixtures'].keys() != original.keys()
            or any(fixture_identity(f) != fixture_identity(original[k]) for k, f in plan['payload']['fixtures'].items())
            or sorted(mid for mids in spec['slates'].values() for mid in mids) != sorted(original)
            or aware(plan['recorded_at']) > cutoff):
        raise ValueError('display windows differ from the original R10 J1 slots or issue clock')
    slots = {}
    for window, mids in spec['slates'].items():
        out = folder / window
        slots.update({mid: dict(window=window, status='no_verified_publication_by_cutoff') for mid in mids})
        if not (out / 'publication.json').is_file():
            continue
        publication = ledger._read(out, read(out / 'publication.json'))
        if aware(publication['recorded_at']) > cutoff:
            continue
        _, fixtures, issued = display_windows.prepared_batch(
            registration, window, read(out / 'entries.json'), (out / 'message.txt').read_text(encoding='utf-8'), out)
        if (publication['track'] != display_windows.TRACK + '-' + window + '-registration'
                or publication['payload']['identity'] != issued
                or publication['payload']['fixtures'] != {f['match_id']: f for f in fixtures}
                or aware(publication['recorded_at']) >= min(aware(f['kickoff_at']) for f in fixtures)):
            raise ValueError('earlier first display or selection differs: ' + window)
        seats = {mid: i for i, mid in enumerate(issued['selected'], 1)}
        for mid, row in issued['rows'].items():
            slots[mid].update(status='authored_first' if row['authored'] else 'unissued',
                              publication_sha256=publication['record_sha256'], published_seat=seats.get(mid),
                              authored_sha256=(row['authored'] or {}).get('sha256'),
                              current_test_pair_sha256=(row['pair'] or {}).get('sha256'))
    return dict(registration_sha256=plan['record_sha256'], current_sha256=spec['current_sha256'],
                test_sha256=spec['test_sha256'], slots=slots)


def _existing(out):
    if (out / 'receipts.json').is_file():
        return ledger._read(out, read(out / 'receipts.json')['registration'])
    plans = [verify(r) for p in out.glob('*-v000001.json') if (r := read(p))['track'] == TRACK + '-registration']
    if len(plans) > 1:
        raise ValueError('multiple R15 registrations in one output')
    return plans[0] if plans else None


def _canonical_plan(store):
    binding = store / ('.' + TRACK + '-registration.json')
    if binding.is_file():
        scope = read(binding)
        if scope['track'] != TRACK + '-registration' or scope['stage'] != 'prematch':
            raise ValueError('invalid R15 canonical registration binding')
        first = store / digest(scope) / '000001.json'
        if first.is_file():
            plan = verify(read(first))
            if any(plan[k] != v for k, v in scope.items()) or plan['revision'] != 1:
                raise ValueError('invalid R15 canonical registration first')
            return plan
    return None


def _prepare(folders, folder, rosters, protocol, display_folder, old, root):
    manifest_docs = {k: _document(p / 'manifest.json') for k, p in folders.items()}
    manifests = {k: v[0] for k, v in manifest_docs.items()}
    sources = _sources(root, folders, manifests)
    recipe, baseline, history, identity = _model_inputs(root, folders, manifests, folder, rosters, protocol)
    if old and old['track'] != TRACK + '-registration':
        raise ValueError('output belongs to a different first registration')
    cutoff = aware(old['payload']['identity']['inputs_cutoff_at'] if old else ledger._now()).astimezone(timezone.utc)
    if old and cutoff > aware(old['recorded_at']):
        raise ValueError('inputs postdate the original registration')
    if recipe['through'] >= cutoff.date().isoformat() or aware(identity['roster_snapshot_at']) > cutoff:
        raise ValueError('training or source snapshot reaches beyond the issue cutoff')
    batches = sources[0]
    parent = batches['r10'].registration
    population = parent['payload']['fixtures']
    display = _display_slots(display_folder, parent, cutoff) if display_folder else None
    fixtures, started = {}, []
    for mid, f in sorted(population.items()):
        if f['cohort'] != CODE:
            continue
        if f['match_id'] != mid:
            raise ValueError('registered R10 match ID differs: ' + mid)
        if aware(f['kickoff_at']) <= cutoff:
            started.append(mid)
        else:
            fixtures[mid] = f
    day = cutoff.date().isoformat()
    payloads = {mid: _forecast(f, sources, recipe, baseline, history, identity, cutoff, day) for mid, f in fixtures.items()}
    if display:
        for mid, payload in payloads.items():
            payload['prior_display'] = display['slots'][mid]
    identity.update(archive_manifest_sha256={k: v[1] for k, v in manifest_docs.items()},
                    source_registrations={k: b.registration['record_sha256'] for k, b in batches.items()},
                    already_started=started, inputs_cutoff_at=cutoff.isoformat(), history_cutoff=day, slots_per_competition=3,
                    selected={CODE: _seats(payloads)}, selection_scope='whole unstarted R10 J1 slate', prior_display=display,
                    payload_sha256={mid: digest(payload) for mid, payload in payloads.items()})
    locked_files = {rosters: identity['rosters_sha256'], rosters.parent/'snapshot-manifest.json': identity['roster_manifest_sha256'],
                    folder/'forward.json': identity['recipe_sha256'][CODE], folder/'fit.json': identity['fit_sha256'],
                    folder/'inputs.json': identity['study_inputs_sha256'], protocol: identity['protocol_sha256']}
    if any(sha(path) != expected for path, expected in locked_files.items()):
        raise ValueError('recipe or source inputs changed during preparation')
    return fixtures, payloads, identity


def _save(fixtures, payloads, identity, out, store, old):
    if not fixtures:
        raise ValueError('no unstarted registered R10 J1 fixtures')
    body = dict(track=TRACK, fixtures=fixtures, identity=identity)
    if old and (old['track'] != TRACK + '-registration' or old['payload'] != body):
        raise ValueError('R15 first registration differs from the current inputs or seats')
    scope = dict(track=TRACK + '-registration', stage='prematch', match_id=TRACK + '-plan:' + digest(body))
    original = store / digest(scope) / '000001.json'
    registered = verify(read(original)) if original.exists() else None
    for mid in fixtures:
        first = store / digest(dict(track=TRACK, stage='prematch', match_id=mid)) / '000001.json'
        if first.exists() and (not registered or verify(read(first)).get('idempotency_key') != registered['record_sha256']):
            raise ValueError('fixture already has a different R15 first: ' + mid)
    binding = store / ('.' + TRACK + '-registration.json')
    if not binding.is_file() or read(binding) != scope:
        atomic_json(binding, scope)
    plan = ledger.register(list(fixtures.values()), identity, TRACK, out, freeze_root=store)
    committed_at = aware(plan['recorded_at']).astimezone(timezone.utc)
    if (aware(identity['inputs_cutoff_at']) > committed_at or aware(identity['roster_snapshot_at']) > committed_at
            or any(aware(p['source_slots']['r10']['recorded_at']) > committed_at for p in payloads.values())):
        raise ValueError('inputs postdate the original registration')
    matches = {mid: ledger.issue(plan, dict(f, cutoff_at=plan['recorded_at']), payloads[mid], TRACK, out,
                                 freeze_root=store) for mid, f in fixtures.items()}
    index = dict(registration=plan, matches=matches)
    path = out / 'receipts.json'
    if path.exists() and read(path) != index:
        raise ValueError('stored first-issue index differs')
    if not path.exists():
        atomic_json(path, index)
    pending = ledger.review(out, [], out, by_competition=True)
    report = out / 'pending.json'
    if report.exists() and read(report) != pending:
        raise ValueError('stored pending report differs from the original firsts')
    if not report.exists():
        atomic_json(report, pending)
    return plan


def run(r5, r7_dir, r10, study_dir, rosters, out=None, *, protocol=study.PROTOCOL, display_windows_dir=None,
        issue=False, freeze_root=DEFAULT_ROOT):
    folders = dict(r5=r5, r7=r7_dir, r10=r10)
    adjacent = r5.parent / '20261125-j1-stage-windows'
    if display_windows_dir is None and (adjacent / 'registration/plan.json').is_file():
        display_windows_dir = adjacent
    if not issue:
        with TemporaryDirectory() as tmp:
            fixtures, _, identity = _prepare(folders, study_dir, rosters, protocol, display_windows_dir, None, Path(tmp))
        return dict(mode='preview', fixtures=fixtures, identity=identity)
    if out is None:
        raise ValueError('an output directory is required to issue')
    store = Path(freeze_root)
    for path in (out, store):
        path.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(out/'.batch.lock'), exclusive_lock(store/('.' + TRACK + '.lock')), TemporaryDirectory() as tmp:
        local, canonical = _existing(out), _canonical_plan(store)
        if local and canonical and local != canonical:
            raise ValueError('R15 output and canonical first registration differ')
        old = local or canonical
        fixtures, payloads, identity = _prepare(folders, study_dir, rosters, protocol, display_windows_dir, old, Path(tmp))
        return _save(fixtures, payloads, identity, out, store, old)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('r5', 'r7', 'r10', 'study', 'rosters'):
        parser.add_argument('--' + arg, type=Path, required=True)
    parser.add_argument('--protocol', type=Path, default=study.PROTOCOL)
    parser.add_argument('--display-windows', type=Path, help='archived J1 Current/Test windows (defaults to sibling archive when present)')
    parser.add_argument('--out', type=Path, help='research first-issue directory for --issue')
    parser.add_argument('--issue', action='store_true', help='register the selected seats and save each research first')
    parser.add_argument('--freeze-root', type=Path, default=Path(DEFAULT_ROOT))
    a = parser.parse_args()
    print(json.dumps(run(a.r5, a.r7, a.r10, a.study, a.rosters, a.out, protocol=a.protocol, display_windows_dir=a.display_windows,
                         issue=a.issue, freeze_root=a.freeze_root), ensure_ascii=False))
