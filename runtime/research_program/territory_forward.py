"""Issue the five-league R14 research slate from the registered R5, R7 and R10 archives."""

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timezone
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from publication import atomic_json
from research.analysis_freeze import DEFAULT_ROOT, aware, digest, verify
from storage_lock import exclusive_lock
from . import all_scope_model as base
from . import continuation_forward as r7
from . import goal_state_forward as ledger
from . import r10_shot_model as decision
from . import territory_hurdle as hurdle
from .all_scope_study import check_panel
from .future_identity import fixture_identity
from .goal_state_study import read_lines, sha
from .paired_kpi_inference import _decisions
from .r8_score_study import read
from .r9_score_study import verified_archive
from .territory_history import LEAGUES, TerritoryHistory
from .territory_study import CODES

TRACK = 'research-r14-territory-press-20261125'
PAIRS = {f'vs_{arm}': [arm, 'candidate'] for arm in ('r7', 'r10', 'market', 'xg', 'xg_matched_dose')}
PAIRS.update(same_runtime=['local_r7', 'local_candidate'], fixed_increment=['fixed_xg', 'fixed_full'])


@dataclass
class FirstIssues:
    folder: Path
    registration: dict
    matches: dict

    @classmethod
    def load(cls, folder):
        index = read(folder / 'receipts.json')
        plan = ledger._read(folder, index['registration'])
        if (plan['track'] != plan['payload']['track'] + '-registration'
                or set(plan['payload']['fixtures']) != set(index['matches'])):
            raise ValueError('incomplete or mismatched first-issue registration: ' + str(folder))
        return cls(folder, plan, index['matches'])

    def match(self, fixture, cutoff):
        mid = fixture['match_id']
        if mid not in self.matches:
            raise ValueError('missing same-fixture first issue: ' + mid)
        old = ledger._read(self.folder, self.matches[mid])
        plan = self.registration
        if (old['track'] != plan['payload']['track'] or old['match_id'] != mid
                or old['payload']['registration_sha256'] != plan['record_sha256']
                or fixture_identity(old['fixture']) != fixture_identity(fixture)
                or fixture_identity(plan['payload']['fixtures'][mid]) != fixture_identity(fixture)
                or not aware(plan['recorded_at']) <= aware(old['fixture']['cutoff_at'])
                <= aware(old['recorded_at']) <= cutoff < aware(fixture['kickoff_at'])):
            raise ValueError('first-issue identity, registration or chronology mismatch: ' + mid)
        return old


def _document(path):
    body = path.read_bytes()
    return json.loads(body), sha256(body).hexdigest()


def _study(study, events, protocol, archives):
    inputs, inputs_hash = _document(study / 'inputs.json')
    registered, protocol_hash = _document(protocol)
    manifests, manifest_hashes = {}, {}
    for key, folder in archives.items():
        manifests[key], manifest_hashes[key] = _document(folder / 'manifest.json')
    events_hash = sha(events)
    if (registered['version'] != hurdle.VERSION or inputs['events_sha256'] != events_hash
            or inputs['extension_manifest_sha256'] != manifest_hashes['r11']):
        raise ValueError('R14 protocol, Understat events or R11 extension differs from the fitted study')
    identity = dict(version=hurdle.VERSION, competitions=list(CODES), pairs=PAIRS, formal_kpi_eligible=False,
                    protocol_sha256=protocol_hash, study_inputs_sha256=inputs_hash,
                    events_sha256=events_hash, extension_manifest_sha256=manifest_hashes['r11'],
                    archive_manifest_sha256=manifest_hashes, recipe_sha256={})
    recipes = {}
    for code in CODES:
        folder = study / 'main' / code
        recipe, recipe_hash = _document(folder / 'forward.json')
        if ((recipe['version'], recipe['competition'], recipe['fit_sha256']) !=
                (hurdle.VERSION, code, sha(folder / 'fit.json'))
                or recipe['fitted']['xg_only'] is not False or recipe['xg_fitted']['xg_only'] is not True):
            raise ValueError('mismatched R14 forward recipe or fit: ' + code)
        for doses in (recipe['doses'], recipe['xg_doses']):
            if (set(doses) != {'score', 'wdl', 'total'}
                    or any(type(d) not in (float, int) or d not in decision.DOSES for d in doses.values())):
                raise ValueError('unregistered R14 decision dose: ' + code)
        recipes[code] = recipe
        identity['recipe_sha256'][code] = recipe_hash
    return recipes, identity, manifests


def _calendar(panel, extra, recipe, baseline):
    code = recipe['competition']
    games = check_panel(panel, code, last=baseline['through'])
    if not games or games[-1]['date'] != baseline['through'] or baseline['through'] > recipe['through']:
        raise ValueError('R5 panel and R7/R14 training dates disagree: ' + code)
    ids = {g['id'] for g in games}
    for g in sorted(extra, key=lambda row: (row['date'], row['id'])):
        if g['date'] > recipe['through']:
            continue
        if (g['id'] in ids or g['date'] <= baseline['through'] or g['score_basis'] != 'regulation_90'
                or any(type(g[k]) is not int or g[k] < 0 for k in ('home_goals', 'away_goals'))):
            raise ValueError('duplicate, overlapping or incomplete R11 fixture: ' + g['id'])
        games.append(g)
        ids.add(g['id'])
    if games[-1]['date'] != recipe['through']:
        raise ValueError('R14 training date differs from the completed calendar: ' + code)
    return games


def _leagues(root, archives, manifests, events, recipes, history_type=TerritoryHistory):
    verified_archive(archives['r11'], manifests['r11'], 'extension.tar.gz', root / 'extension')
    extra = defaultdict(list)
    for g in read_lines(root / 'extension/fixture-panels.jsonl'):
        if g['competition'] in CODES:
            extra[g['competition']].append(g)
    sources = read(root / 'extension/manifest.json')['sources']
    acquired = {s['league']: s['acquired_at_utc'] for s in sources if s['league'] in CODES}
    records = defaultdict(list)
    for row in read_lines(events):
        code = LEAGUES.get(row['competition'], row['competition'])
        if code in CODES:
            records[code].append(row)
    leagues = {}
    for code, recipe in recipes.items():
        name = 'models/' + code + '.tar.gz'
        verified_archive(archives['r7'], manifests['r7'], name, root / code / 'r7')
        verified_archive(archives['r5'], manifests['r5'], name, root / code / 'r5')
        folder = root / code / 'r7/main'
        baseline = read(folder / 'forward.json')
        panel = root / code / 'r5/panel.jsonl'
        panel_hash = sha(panel)
        if (recipe['baseline_sha256'] != sha(folder / 'forward.json') or baseline['competition'] != code
                or baseline['version'] != r7.model.VERSION or baseline['fit_sha256'] != sha(folder / 'fit.json')
                or read(folder / 'fit.json')['source_panel_sha256'] != panel_hash):
            raise ValueError('R7 anchor or R5 calendar differs from fitted inputs: ' + code)
        observed = records[code]
        clocks = (r.get('source_fetched_at_utc') or r.get('source_fetched_at') for r in observed)
        last_clock = max((aware(stamp) for stamp in clocks if stamp), default=None)
        history = history_type(observed, code)
        history.walk(_calendar(panel, extra[code], recipe, baseline))
        provenance = dict(panel_sha256=panel_hash, r7_training_through=baseline['through'],
                          events_fetched_through=last_clock.isoformat() if last_clock else None,
                          extension_acquired_at=acquired[code] if extra[code] else None)
        leagues[code] = baseline, history, provenance
    return leagues


def verified_parents(fixture, sources, baseline, recipe, cutoff):
    raw, published, previous = (batch.match(fixture, cutoff) for batch in sources)
    odds, r7_issue, r10_issue = (r['payload'] for r in (raw, published, previous))
    mid, code = fixture['match_id'], fixture['cohort']
    game = r7.frozen_game(fixture, odds)
    game['date'] = aware(fixture['kickoff_at']).astimezone(timezone.utc).date().isoformat()
    line = base.actual_line(game)
    control, market = r7_issue['forecasts']['candidate'], odds['forecasts']['market']
    if (aware(raw['recorded_at']) > aware(published['fixture']['cutoff_at'])
            or aware(published['recorded_at']) > aware(previous['fixture']['cutoff_at'])
            or not raw['record_sha256'] == r7_issue['input_sha256'] == r10_issue['odds_source_first_issue']
            or not recipe['baseline_sha256'] == r7_issue['model_sha256'] == r10_issue['r7_forward_sha256']
            or r7_issue['training_through'] != baseline['through']
            or not line == odds['line'] == r7_issue['line'] == r10_issue['line']
            or control != r10_issue['forecasts']['r7']
            or not market == r7_issue['forecasts']['market'] == r10_issue['forecasts']['market']):
        raise ValueError('R5 prices, published controls or their source bindings differ: ' + mid)
    for batch, record, key in ((sources[1], published, 'model_sha256'), (sources[2], previous, 'recipe_sha256')):
        weights = batch.registration['payload']['identity']['recipe_sha256'][code]
        if record['payload'][key] != weights:
            raise ValueError('published weights differ from their registration: ' + mid)
        issued_day = aware(record['recorded_at']).astimezone(timezone.utc).date()
        if date.fromisoformat(record['payload']['training_through']) >= issued_day:
            raise ValueError('published training reaches its own issue date: ' + mid)
    evidence = odds['market_evidence']
    quotes = [evidence] if 'source_sha256' in evidence else evidence.values()
    odds_cutoff = aware(raw['fixture']['cutoff_at'])
    if any(aware(q[key]) > odds_cutoff for q in quotes for key in ('observed_at', 'quoted_at') if q.get(key)):
        raise ValueError('price observation after R5 cutoff: ' + mid)
    return game, raw, published, previous


def _forecast(fixture, sources, league, recipe, identity, cutoff):
    baseline, history, provenance = league
    game, raw, published, previous = verified_parents(fixture, sources, baseline, recipe, cutoff)
    odds, r7_issue, r10_issue = (r['payload'] for r in (raw, published, previous))
    mid, code = fixture['match_id'], fixture['cohort']
    line = base.actual_line(game)
    control, market = r7_issue['forecasts']['candidate'], odds['forecasts']['market']
    day = cutoff.date().isoformat()
    x, exposure = history.context(game, cutoff=day)
    for side in ('home', 'away'):
        dates = exposure[side + '_history_dates']
        name_date = exposure[side + '_source_name_date']
        if exposure['cutoff'] != day or any(d >= day for d in dates) or (name_date and name_date >= day):
            raise ValueError('team history reaches the issue date: ' + mid)
    prior = r7.matrix(game, baseline)
    local = base.pick(prior, line)
    full = hurdle.apply(prior, x, recipe['fitted'])
    xg = hurdle.apply(prior, x, recipe['xg_fitted'])
    doses = recipe['doses']
    candidate, heads = decision.decisions(prior, full, doses, line, control)
    tips = dict(candidate=candidate, r7=control, local_r7=local, r10=r10_issue['forecasts']['candidate'],
                market=market, fixed_full=control if full is prior else base.pick(full, line),
                fixed_xg=control if xg is prior else base.pick(xg, line))
    tips['xg'], _ = decision.decisions(prior, xg, recipe['xg_doses'], line, control)
    tips['xg_matched_dose'], _ = decision.decisions(prior, xg, doses, line, control)
    tips['local_candidate'], _ = decision.decisions(prior, full, doses, line, local)
    for tip in tips.values():
        _decisions(tip, None, line)  # Validate the same score order and common line the ledger will settle.
    score = heads['score']
    cells = [tuple(map(int, s.split('-'))) for s in candidate['top3']]
    return dict(version=hurdle.VERSION, formal_kpi_eligible=False, home_name=game['home'], away_name=game['away'],
                forecasts=tips, line=line, prices=odds['prices'], market_evidence=odds['market_evidence'],
                features=x.tolist(), process=exposure, doses=doses, xg_doses=recipe['xg_doses'],
                quality=dict(p_first=float(score[cells[0]]), p_three=float(sum(score[c] for c in cells))),
                head_sources={k: 'r7' if m is prior else 'candidate' for k, m in heads.items()},
                training_through=recipe['through'], history_cutoff=day, protocol_sha256=identity['protocol_sha256'],
                events_sha256=identity['events_sha256'], extension_manifest_sha256=identity['extension_manifest_sha256'],
                fit_sha256=recipe['fit_sha256'], baseline_sha256=recipe['baseline_sha256'],
                recipe_sha256=identity['recipe_sha256'][code], odds_source_first_issue=raw['record_sha256'],
                r7_first_issue=published['record_sha256'], r10_first_issue=previous['record_sha256'],
                r10_recipe_sha256=r10_issue['recipe_sha256'],
                odds_cutoff_at=raw['fixture']['cutoff_at'], r5_recorded_at=raw['recorded_at'],
                r7_recorded_at=published['recorded_at'], r10_recorded_at=previous['recorded_at'], **provenance)


def _existing(out, track=TRACK):
    if (out / 'receipts.json').exists():
        return ledger._read(out, read(out / 'receipts.json')['registration'])
    plans = [r for p in out.glob('*-v000001.json') if (r := read(p))['track'] == track + '-registration']
    if len(plans) > 1:
        raise ValueError('multiple R14 registrations in output')
    return verify(plans[0]) if plans else None


def _issue(archives, study, events, protocol, out, freeze_root, root, track, configure, forecast,
           history_type, population_index):
    recipes, identity, manifests = configure(study, events, protocol, archives)
    label = identity['version'].split('-')[0]
    batches = []
    for name, archive, prefix in (('r5', 'forward-first-issues.tar.gz', 'issued'),
                                  ('r7', 'first-issues.tar.gz', ''), ('r10', 'forward/first-issues.tar.gz', '')):
        verified_archive(archives[name], manifests[name], archive, root / name)
        batch = FirstIssues.load(root / name / prefix)
        identity[name + '_registration'] = batch.registration['record_sha256']
        batches.append(batch)
    r5_plan, r7_plan, r10_plan = (b.registration['payload']['identity'] for b in batches)
    if (r5_plan['model'], r7_plan['version'], r10_plan['version']) != (base.VERSION, r7.model.VERSION, decision.VERSION):
        raise ValueError('wrong R5, R7 or R10 research registration')
    old = _existing(out, track)
    cutoff = aware(old['recorded_at'] if old else ledger._now()).astimezone(timezone.utc)
    fixtures, expired = [], []
    for mid, f in batches[population_index].registration['payload']['fixtures'].items():
        if f['cohort'] not in CODES:
            continue
        if f['match_id'] != mid:
            raise ValueError('R10 registration fixture key differs: ' + mid)
        if aware(f['kickoff_at']) <= cutoff:
            expired.append(mid)
        else:
            fixtures.append(f)
    if not fixtures:
        raise ValueError('no unstarted in-scope R10 fixtures at issue')
    identity['already_started'] = sorted(expired)
    population = {f['match_id']: f for f in fixtures}
    if old and (old['track'] != track + '-registration' or old['payload'] !=
                dict(track=track, identity=identity, fixtures=population)):
        raise ValueError('inputs or population changed after ' + label + ' registration')
    index = out / 'receipts.json'
    if index.exists():
        # Revalidate the requested archives without rebuilding completed forecasts.
        models = [f'models/{code}.tar.gz' for code in CODES]
        for key, names in (('r5', models), ('r7', models), ('r11', ['extension.tar.gz'])):
            for name in names:
                if sha(archives[key] / name) != manifests[key]['files'][name]:
                    raise ValueError('archive checksum mismatch: ' + name)
        return read(index)['registration']
    leagues = _leagues(root, archives, manifests, events, recipes, history_type)

    def forecasts(at):
        for code, (_, _, provenance) in leagues.items():
            if date.fromisoformat(recipes[code]['through']) >= at.date():
                raise ValueError('training reaches the UTC issue date: ' + code)
            for key in ('events_fetched_through', 'extension_acquired_at'):
                if provenance[key] and aware(provenance[key]) > at:
                    raise ValueError('source acquired after issue cutoff: ' + code)
        return [forecast(f, batches, leagues[f['cohort']], recipes[f['cohort']], identity, at) for f in fixtures]

    payloads = forecasts(cutoff)
    if sha(events) != identity['events_sha256']:
        raise ValueError('Understat source changed while preparing the issue')
    plan = ledger.register(fixtures, identity, track, out, freeze_root=freeze_root)
    issued = aware(plan['recorded_at']).astimezone(timezone.utc)
    if issued.date() != cutoff.date() or issued < cutoff:
        # A registration recovered from the canonical store may predate this run.
        payloads = forecasts(issued)
    for f in fixtures:
        mid = f['match_id']
        first = Path(freeze_root) / digest(dict(track=track, stage='prematch', match_id=mid)) / '000001.json'
        if first.exists() and verify(read(first))['idempotency_key'] != plan['sha256']:
            raise ValueError('fixture already has a different R14 first issue: ' + mid)
    receipts = {}
    for f, payload in zip(fixtures, payloads, strict=True):
        receipts[f['match_id']] = ledger.issue(plan, dict(f, cutoff_at=plan['recorded_at']), payload, track,
                                               out, freeze_root=freeze_root)
    atomic_json(index, dict(registration=plan, matches=receipts))
    return plan


def run(r5, r7_dir, r10, r11, study, events, protocol, out, *, freeze_root=DEFAULT_ROOT,
        track=TRACK, configure=_study, forecast=_forecast, history_type=TerritoryHistory, population_index=2):
    archives = dict(r5=r5, r7=r7_dir, r10=r10, r11=r11)
    out.mkdir(parents=True, exist_ok=True)
    freeze_root = Path(freeze_root)
    freeze_root.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(out / '.batch.lock'), exclusive_lock(freeze_root / ('.' + track + '.lock')):
        with TemporaryDirectory() as tmp:
            plan = _issue(archives, study, events, protocol, out, freeze_root, Path(tmp), track, configure,
                          forecast, history_type, population_index)
        pending = ledger.review(out, [], out, by_competition=True)
        path = out / 'pending.json'
        if not path.exists():
            atomic_json(path, pending)
        elif read(path) != pending:
            raise ValueError('stored pending report differs from the registered first issues')
        return plan


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('r5', 'r7', 'r10', 'r11'):
        parser.add_argument('--' + key, type=Path, required=True, help=key.upper() + ' archive containing manifest.json')
    parser.add_argument('--study', type=Path, required=True, help='R14 root with inputs.json and main/<competition>/forward.json')
    parser.add_argument('--events', type=Path, required=True, help='Understat events.jsonl used by the R14 study')
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True, help='R14 research registration, first issues and pending report')
    parser.add_argument('--freeze-root', type=Path, default=Path(DEFAULT_ROOT), help='canonical first-issue store')
    a = parser.parse_args()
    receipt = run(a.r5, a.r7, a.r10, a.r11, a.study, a.events, a.protocol, a.out, freeze_root=a.freeze_root)
    print(json.dumps(receipt, ensure_ascii=False))
