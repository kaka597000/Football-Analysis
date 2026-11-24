"""Count price changes in original ESPN snapshots, using the first comprehensive freezes."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from research.analysis_freeze import aware, verify


def decimal_price(american):
    price = Decimal(american)
    if not price.is_finite() or abs(price) < 100:
        raise ValueError('expected American odds')
    return 1 + (price / 100 if price > 0 else -100 / price)


def markets(quote, key):
    prices = tuple(decimal_price(quote['moneyline'][leg][key]['odds'])
                   for leg in ('home', 'draw', 'away'))
    lines, totals = [], []
    for side, prefix in (('over', 'o'), ('under', 'u')):
        leg = quote['total'][side][key]
        label = leg['line']
        if not label.startswith(prefix):
            raise ValueError('unexpected total side')
        lines.append(Decimal(label[1:]))
        totals.append(decimal_price(leg['odds']))
    return prices, tuple(lines), tuple(totals)


def archived_quote(report, fixture, event_id):
    sources = []
    for source in fixture['sources']:
        url = urlsplit(source['source_url'])
        if (url.hostname == 'site.api.espn.com' and url.path.endswith('/summary')
                and parse_qs(url.query).get('event') == [event_id]
                and aware(source['query_time']) < aware(fixture['cutoff_at'])):
            sources.append(source)
    if len(sources) != 1:
        return 'missing_bound_source', None
    source = sources[0]
    for path in report.glob(f'*/raw/espn-summary-{event_id}.json'):
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != source['response_sha256']:
            continue
        meta = json.loads(path.with_suffix('.json.meta.json').read_bytes())
        if (meta['sha256'] != source['response_sha256'] or meta['url'] != source['source_url']
                or aware(meta['query_time']) != aware(source['query_time'])):
            raise ValueError('raw capture metadata conflicts with original freeze')
        summary = json.loads(raw)
        if str(summary['header']['id']) != event_id:
            raise ValueError('raw ESPN event conflicts with original freeze')
        quotes = [item for item in summary.get('odds', []) if item['provider']['name'] == 'DraftKings']
        if len(quotes) != 1:
            return 'missing_unique_book', None
        return 'paired', quotes[0]
    return 'missing_bound_body', None


def audit(report, issued):
    counts, seen = Counter(), set()
    for match in json.loads((report / 'analysis-records.json').read_bytes())['matches']:
        ref = match['comprehensive_reference']
        directory, name = Path(ref['path']).parts[-2:]
        if not re.fullmatch('[0-9a-f]{64}', directory) or name != '000001.json':
            raise ValueError('invalid first-freeze reference')
        record = verify(json.loads((issued / directory / name).read_bytes()))
        if (record['record_sha256'] != ref['sha256'] or record['freeze_id'] != ref['freeze_id']
                or record['evaluation_slot'] != 'FIRST_PREMATCH'
                or (record['stage'], record['track']) != ('prematch', 'comprehensive')
                or record['match_id'] in seen):
            raise ValueError('expected a unique original comprehensive freeze')
        seen.add(record['match_id'])
        counts['roster'] += 1
        fixture = record['fixture']
        event_id = str(fixture.get('source_ids', {}).get('espn', ''))
        if not event_id.isdecimal():
            counts['no_espn_identity'] += 1
            continue
        status, quote = archived_quote(report, fixture, event_id)
        counts[status] += 1
        if quote is not None:
            opening, observed = markets(quote, 'open'), markets(quote, 'close')
            for index, key in enumerate(('wdl_price_changed', 'total_line_changed', 'total_price_changed')):
                counts[key] += opening[index] != observed[index]
            counts['all_unchanged'] += opening == observed
    return dict(counts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('issued', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.report, args.issued), indent=2, sort_keys=True))
