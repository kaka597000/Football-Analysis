"""Attach retained, identity-bound market quotes to new prematch inputs only.

This reader makes no network request and writes no freeze. It verifies retained
snapshot and source bytes again; provider adapters remain responsible for the
truthful interpretation of their source identity and quote timestamp fields.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

try:
    from runtime.v2_test.data_contract import aware, digest, public_url, snapshot
    from runtime.research_program.future_validation import quote_audit
except ModuleNotFoundError:
    from v2_test.data_contract import aware, digest, public_url, snapshot
    from research_program.future_validation import quote_audit


def _source(root, metadata, cutoff):
    relative = Path(metadata['file'])
    path = (root / relative).resolve()
    if (relative.is_absolute() or not path.is_relative_to(root.resolve())
            or hashlib.sha256(path.read_bytes()).hexdigest() != metadata['sha256']):
        raise ValueError('retained identity source checksum or path mismatch')
    public_url(metadata['url'])
    if aware(metadata['captured_at']) > cutoff:
        raise ValueError('identity evidence unavailable at cutoff')


def _matches(target, fixture):
    keys = {'match_id': 'match_id', 'cohort': 'cohort', 'stage': 'stage',
            'home_id': 'home', 'away_id': 'away'}
    if any(not target.get(a) or target[a] != fixture.get(b) for a, b in keys.items()):
        return False
    if fixture.get('competition') and target.get('competition') != fixture['competition']:
        return False
    return aware(target['kickoff_at']) == aware(fixture['kickoff_at'])


def attach_total_quote(fixture, market_dir, *, provider_order=(), bookmaker_order=(), now=None):
    """Return a copy plus an audit; missing markets do not remove the fixture.

    Preference lists are operational, fixed before inference. Unlisted providers
    and books follow lexically. For each provider/book use its latest source
    quote, rejecting ambiguous same-time prices/lines rather than choosing a line
    based on a forecast. Existing input quotes are not rewritten when no new
    retained quote qualifies; the caller's ordinary quote audit still applies.
    """
    now = now or datetime.now(timezone.utc)
    cutoff, kickoff = aware(fixture['cutoff_at']), aware(fixture['kickoff_at'])
    if not cutoff <= now < kickoff:
        raise ValueError('market attachment requires an actual prematch cutoff')
    target = dict(fixture, home_id=fixture['home'], away_id=fixture['away'])
    result = deepcopy(fixture)
    excluded, candidates = Counter(), []
    files = sorted(Path(market_dir).rglob('market-channel.json'))
    for path in files:
        try:
            channel = json.loads(path.read_text())
            retained = channel.get('snapshots', [])
            if (not isinstance(retained, list)
                    or retained and channel.get('schema') != 'WCPP-MARKET-CHANNEL-1'):
                raise ValueError('market snapshots must be a list')
        except (OSError, ValueError, TypeError):
            excluded['INVALID_MARKET_CHANNEL'] += 1
            continue
        for saved in retained:
            try:
                if (saved.get('schema') != 'WCPP-V2-FEATURE-SNAPSHOT-1'
                        or saved.get('snapshot_sha256') != digest({k: v for k, v in saved.items()
                                                                   if k != 'snapshot_sha256'})):
                    raise ValueError('retained snapshot checksum mismatch')
                if not _matches(saved['target'], fixture):
                    excluded['FIXTURE_IDENTITY_OR_STAGE_MISMATCH'] += 1
                    continue
                if not aware(saved['target']['cutoff_at']) <= aware(saved['created_at']) <= cutoff:
                    raise ValueError('snapshot unavailable at prediction cutoff')
                age_limit = saved['policy']['market_max_age_seconds']
                if type(age_limit) not in (int, float) or not 0 <= age_limit <= 3600:
                    raise ValueError('source freshness policy exceeds locked one-hour limit')
                rows = saved['observations']
                for row in rows:
                    if row['field'] == 'market_quote':
                        _source(path.parent, row['identity_evidence'], cutoff)
                        # Infer odds bind only an event ID. Its retained events
                        # response supplies the provider teams/grade/kickoff link.
                        if row['provider'] == 'infersports' or 'provider_event_evidence' in row:
                            _source(path.parent, row['provider_event_evidence'], cutoff)
                checked = snapshot(rows, target, path.parent, provider=saved['provider'],
                                   market_max_age_seconds=age_limit, now=now)
                excluded.update(checked['excluded'])
                for row in checked['observations']:
                    if row['field'] != 'market_quote' or row['value']['market'] != 'totals':
                        continue
                    value, evidence = row['value'], row['evidence']
                    quote = {'verified': True, 'match_id': fixture['match_id'],
                             'bookmaker': value['bookmaker'], 'market': 'standard_total',
                             'line': value['line'], 'over_odds': value['odds']['over'],
                             'under_odds': value['odds']['under'], 'quoted_at': value['quoted_at'],
                             'query_time': row['observed_at'], 'source_url': evidence['url'],
                             'source_sha256': evidence['sha256'], 'provider': row['provider']}
                    if not quote_audit(dict(fixture, total_quote=quote))['eligible']:
                        excluded['INELIGIBLE_LOCKED_TOTAL_QUOTE'] += 1
                        continue
                    candidates.append({'quote': quote, 'channel_path': str(path.resolve()),
                                       'snapshot_sha256': saved['snapshot_sha256'],
                                       'observation_sha256': digest(row),
                                       'evidence_file': evidence['file']})
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                excluded['INVALID_SNAPSHOT_OR_SOURCE_EVIDENCE'] += 1
    groups = defaultdict(list)
    for item in candidates:
        q = item['quote']; groups[(q['provider'], q['bookmaker'])].append(item)
    eligible = []
    for values in groups.values():
        latest = max(aware(v['quote']['quoted_at']) for v in values)
        recent = [v for v in values if aware(v['quote']['quoted_at']) == latest]
        signatures = {(v['quote']['line'], v['quote']['over_odds'], v['quote']['under_odds']) for v in recent}
        if len(signatures) != 1:
            excluded['AMBIGUOUS_LATEST_BOOK_LINE_OR_PRICE'] += 1
            continue
        eligible.append(max(recent, key=lambda v: (aware(v['quote']['query_time']), v['snapshot_sha256'])))
    def priority(value, order):
        return (order.index(value) if value in order else len(order), value)
    selected = min(eligible, key=lambda v: (priority(v['quote']['provider'], provider_order),
                   priority(v['quote']['bookmaker'], bookmaker_order))) if eligible else None
    audit = {'schema': 'WCPP-MARKET-ATTACHMENT-1',
             'status': 'ATTACHED' if selected else 'NO_ELIGIBLE_RETAINED_TOTAL_QUOTE',
             'market_channels_examined': len(files), 'eligible_provider_books': len(eligible),
             'excluded': dict(excluded), 'provider_order': list(provider_order),
             'bookmaker_order': list(bookmaker_order), 'source_time_inferred': False,
             'first_freeze_backfilled': False, 'numeric_weights_changed': False}
    if selected:
        result['total_quote'] = selected['quote']
        audit['selected'] = {k: v for k, v in selected.items() if k != 'quote'}
        audit['selected'].update(provider=selected['quote']['provider'],
                                 bookmaker=selected['quote']['bookmaker'])
    result['market_attachment'] = audit
    return result, audit
