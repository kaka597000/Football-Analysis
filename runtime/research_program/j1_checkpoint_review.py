"""Verify separately frozen J1 market benchmarks and score them on retained finals."""

from collections import Counter, defaultdict
from datetime import timedelta
from hashlib import sha256
import json
from math import isclose

from research.analysis_freeze import aware, digest, verify
from . import club_market_capture as club_market
from . import goal_state_forward as ledger
from . import market_baseline as market
from .nov25_first_review import same_fixture
from .paired_kpi_inference import METRICS, OUTCOMES, _decisions


def read_wave(directory, plan, fixtures):
    """Bind an immutable first forecast to the original registration and captured prices."""
    plan = verify(plan)
    prepared = json.loads((directory / 'prepared-fixtures.json').read_bytes())
    entries = {row['match_id']: row for row in prepared}
    names = {row['market_checkpoint']['name'] for row in prepared}
    if len(entries) != len(prepared) or entries.keys() != fixtures.keys() or len(names) != 1:
        raise ValueError('market capture must keep one checkpoint and the full registered slate')
    checkpoint, = names
    offset = timedelta(minutes=club_market.CHECKPOINTS[checkpoint])
    for mid, row in entries.items():
        same_fixture(fixtures[mid], row)
        if aware(row['market_checkpoint']['deadline']) != aware(fixtures[mid]['kickoff_at']) - offset:
            raise ValueError('market capture deadline differs from the registered schedule: ' + mid)
    manifest = (directory / 'receipts.json').read_bytes()
    manifest_sha = sha256(manifest).hexdigest()
    receipts = json.loads(manifest)['captures']
    folder = directory / 'benchmark'
    refs = json.loads((folder / 'index.json').read_bytes())
    track = 'j1-' + checkpoint.lower().replace('-', '').removesuffix('h') + '-market-baseline'
    records = {}
    for ref in refs:
        mid = ref['match_id']
        scope = dict(match_id=mid, stage='prematch', track=track)
        expected = digest(scope)[:16] + '-v000001'
        if (mid not in entries or mid in records or entries[mid]['market_availability'] != 'FRESH_SAME_BOOK_PAIR'
                or ref['freeze_id'] != expected or ref['file'] != expected + '.json'):
            raise ValueError('unexpected checkpoint freeze reference: ' + mid)
        record = ledger._read(folder, dict(ref, id=expected))
        source = entries[mid]
        fixture, payload = record['fixture'], record['payload']
        timing = source['market_checkpoint']
        cutoff, issued_at = aware(fixture['cutoff_at']), aware(record['recorded_at'])
        deadline, checked = aware(timing['deadline']), aware(timing['checked_at'])
        captured = aware(fixture['market_evidence']['odds']['captured_at'])
        if (any(record[key] != value for key, value in scope.items())
                or record['recorded_at'] != ref['recorded_at']
                or fixture != dict(source, cutoff_at=fixture['cutoff_at'])
                or any(meta not in receipts for key, meta in fixture['market_evidence'].items() if key != 'event_id')
                or payload['registration_id'] != plan['freeze_id']
                or payload['registration_sha256'] != plan['record_sha256']
                or payload['capture_manifest_sha256'] != manifest_sha
                or payload['checkpoint'] != checkpoint or payload['checkpoint_deadline'] != timing['deadline']
                or aware(source['cutoff_at']) != deadline or checked < deadline - market.MAXIMUM_QUOTE_AGE
                or not (aware(plan['recorded_at']) <= checked <= captured <= aware(timing['verified_at'])
                        <= cutoff <= issued_at <= deadline < aware(fixture['kickoff_at']))):
            raise ValueError('checkpoint fixture, registration or issue time differs: ' + mid)
        _verify_forecast(source, fixture, payload['forecast'], directory)
        records[mid] = record
    return checkpoint, records


def _verify_forecast(source, fixture, forecast, directory):
    replay = market.predict(fixture)
    stable_keys = ('version', 'method', 'match_id', 'formal_kpi_eligible', 'active_model_changed',
                   'status', 'quote_snapshot', 'quote_snapshot_sha256', 'primary', 'top3',
                   'poisson_top3', 'lowest_price_1x2', 'lowest_price_total')
    if replay['status'] != 'AVAILABLE' or any(forecast[key] != replay[key] for key in stable_keys):
        raise ValueError('frozen market forecast differs from the original evidence')
    total, replay_total = forecast['full_win_total'], replay['full_win_total']
    wdl, replay_wdl = forecast['wdl_probabilities'], replay['wdl_probabilities']
    numbers = [(forecast['primary_probability'], replay['primary_probability'])]
    numbers.extend((wdl[key], replay_wdl[key]) for key in replay_wdl)
    numbers.extend((total['settlements'][side][key], value)
                   for side, states in replay_total['settlements'].items() for key, value in states.items())
    # Optimizer rounding can vary across platforms. Preserve identical decisions and numerical probabilities.
    if (wdl.keys() != replay_wdl.keys() or max(wdl, key=wdl.get) != max(replay_wdl, key=replay_wdl.get)
            or any(total[key] != replay_total[key] for key in ('line', 'direction', 'objective'))
            or any(not isclose(a, b, rel_tol=0, abs_tol=1e-10) for a, b in numbers)):
        raise ValueError('frozen market probabilities or decisions changed')
    quote = forecast['quote_snapshot']
    for inputs in (source, fixture):
        ok, reason, quote_clock = club_market.verify_j1_quote(
            inputs, inputs['total_quote'], directory, quote['h2h']['prices'])
        if not ok or quote_clock != quote['total']['quoted_at']:
            raise ValueError(f'checkpoint raw markets do not reproduce: {fixture["match_id"]} {reason}')


def review(folders, plan, fixtures, results, starts, at):
    """Score each checkpoint on the same caller-verified regulation results and actual starts."""
    groups = defaultdict(dict)
    for folder in folders:
        checkpoint, records = read_wave(folder, plan, fixtures)
        duplicate = groups[checkpoint].keys() & records.keys()
        if duplicate:
            raise ValueError('duplicate frozen benchmark in one checkpoint: ' + ', '.join(sorted(duplicate)))
        groups[checkpoint].update(records)
    labels = {row['match_id']: row for row in results}
    return {name: _score(records, fixtures, labels, starts, at) for name, records in groups.items()}


def _score(records, fixtures, results, starts, at):
    rows, values, totals = {}, defaultdict(list), Counter()
    for mid in fixtures:
        record = records.get(mid)
        if record is None or aware(record['recorded_at']) > at:
            rows[mid] = dict(status='NO_BENCHMARK_AT_REVIEW_CUTOFF')
            continue
        forecast = record['payload']['forecast']
        total = forecast['full_win_total']
        wdl = forecast['wdl_probabilities']
        pick = dict(primary=forecast['primary'], top3=forecast['top3'], wdl=max(wdl, key=wdl.get),
                    total_side=total['direction'])
        status, actual = 'AWAITING_VERIFIED_REGULATION', None
        if mid in results and mid in starts:
            earliest, latest = (aware(starts[mid][key]) for key in ('earliest', 'latest'))
            outcome = results[mid]
            if (aware(record['recorded_at']) < earliest <= latest
                    and latest + timedelta(minutes=90) <= aware(outcome['observed_at']) <= at):
                status = 'SETTLED'
                actual = (outcome['home_goals'], outcome['away_goals'])
            else:
                status = 'UNVERIFIED_CHRONOLOGY'
        _, hits, settlement = _decisions(pick, actual, total['line'])
        for metric in METRICS:
            if hits[metric] is not None:
                values[metric].append(hits[metric])
        if settlement is not None:
            totals[settlement] += 1
        rows[mid] = dict(status=status, freeze_id=record['freeze_id'], forecast=pick,
                         total_line=total['line'], actual=actual, hits=hits, total_settlement=settlement)
    issued = sum('freeze_id' in row for row in rows.values())
    metrics = {name: dict(hits=sum(values[name]), eligible=len(values[name]),
                          accuracy=sum(values[name])/len(values[name]) if values[name] else None) for name in METRICS}
    return dict(registered=len(fixtures), issued=issued, settled=sum(row['status'] == 'SETTLED' for row in rows.values()),
                metrics=metrics,
                total_settlements={outcome: totals[outcome] for outcome in OUTCOMES}, per_match=rows)
