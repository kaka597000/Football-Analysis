"""Export public aggregate metrics and denominators without publishing individual prices or predictions."""
import argparse
import csv
import io
from pathlib import Path
import json

from competition_scope import policy
from study import ARMS, METRICS, indexed, save, write


def publish(inputs, native_path, proxy_path, out):
    native, proxy = (json.loads(p.read_bytes()) for p in (native_path, proxy_path))
    original = indexed(inputs)
    groups = {s['code'] for s in policy()['competitions']}
    columns = ['panel','competition','listed','market_pair','same_line_market','scored','score_log_loss_baseline',
               'score_log_loss_candidate']
    for m in METRICS:
        columns += [f'{m}_{name}' for name in ('n', *ARMS, 'gained', 'lost')]
    buf = io.StringIO(newline='')
    table = csv.DictWriter(buf, fieldnames=columns, lineterminator='\n')
    table.writeheader()
    for report, path in ((native,native_path),(proxy,proxy_path)):
        paired = indexed(path.parent/'forecasts.jsonl')
        for league in sorted(groups | set(report['by_league'])):
            stats = report['by_league'].get(league)
            predicted = [r for r in paired.values() if r['competition'] == league]
            market = sum('forecasts' in r for r in predicted)
            same = sum(r['status'] == 'same_line' for r in predicted)
            row = dict(panel=report['panel'], competition=league, listed=stats['listed'] if stats else 0,
                       market_pair=market, same_line_market=same, scored=stats['paired'] if stats else 0)
            if stats and stats['paired']:
                for arm in ARMS:
                    row['score_log_loss_'+arm] = stats['log_loss'][arm]
                for m in METRICS:
                    for name in ('n', *ARMS, 'gained', 'lost'):
                        row[f'{m}_{name}'] = stats[m][name]
            table.writerow(row)
    write(out/'competition-coverage.csv',buf.getvalue().encode())
    for report in (native,proxy):
        fields = ('panel','fit_panel','beta','lock_sha256','labels_sha256','listed','gaps','all_paired',
                  'same_line','eligible_for_future_proposal','actual_display_pairs')
        data = {k:report[k] for k in fields}
        if report['panel'] == 'native':
            data['by_beijing_kickoff_day'] = report['by_day']
            data['deduplicated_original_fixtures'] = len(original)
            data['sensitivity_excluding_previously_reviewed_slate'] = report['sensitivity_excluding_previously_reviewed_slate']
        save(out/(report['panel']+'-metrics.json'), data)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','native','proxy','out'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    publish(a.inputs, a.native, a.proxy, a.out)
