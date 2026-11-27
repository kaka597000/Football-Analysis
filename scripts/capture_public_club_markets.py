"""Capture official schedule identities and keyless InferSports prices for a fixture list."""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'runtime/v5'), str(ROOT)]
from automation.infer_market_snapshot import BASE, fetch as provider_fetch
from automation.market_snapshot import fetch as board_fetch
from research.analysis_freeze import aware
from research_program.club_market_capture import CHECKPOINTS, LEAGUES, attach
from research_program.future_validation import read, write_new
from research_program.market_baseline import MAXIMUM_QUOTE_AGE


def collect(fixtures, teams, out, *, archive_root=None, checkpoint=None):
    offset = timedelta(minutes=CHECKPOINTS[checkpoint]) if checkpoint else None
    out = Path(out).resolve()
    root = out if archive_root is None else Path(archive_root).resolve()
    folder = out.relative_to(root)
    out.mkdir(parents=True, exist_ok=False)
    captures, outputs, failures = {}, [], {}
    next_request = 0.
    provider_block = None

    def get(url, filename, is_board=False):
        nonlocal next_request, provider_block
        if url not in captures:
            if is_board:
                record = board_fetch(url, url, out/filename)
            else:
                if provider_block:
                    raise RuntimeError(provider_block)
                sleep(max(0., next_request-monotonic()))
                next_request = monotonic()+1.
                try:
                    record = provider_fetch(url, out/filename)
                except HTTPError as error:
                    if error.code in (401,403,429):
                        provider_block = f'provider HTTP {error.code}'
                    raise
            captures[url] = dict(record, file=(folder / record['file']).as_posix())
        return captures[url]

    for original in fixtures:
        f = {k:v for k,v in original.items()
             if k not in ('total_quote','market_snapshots','market_evidence','market_team_ids','market_checkpoint')}
        mid, league = f['match_id'], f['cohort']
        f['market_availability'] = 'NO_FRESH_VERIFIED_TOTAL'
        try:
            if league not in LEAGUES or f['stage'] != 'regular' or f['score_basis'] != 'regulation_90':
                raise ValueError('outside the supported regular-time leagues')
            kickoff = aware(f['kickoff_at']).astimezone(timezone.utc)
            now = datetime.now(timezone.utc)
            if kickoff <= now:
                raise ValueError('kickoff has passed')
            deadline = kickoff - offset if offset is not None else None
            if deadline is not None:
                f['market_checkpoint'] = dict(name=checkpoint, deadline=deadline.isoformat(),
                                              checked_at=now.isoformat())
                if now > deadline:
                    raise ValueError('checkpoint already elapsed')
                if now < deadline - MAXIMUM_QUOTE_AGE:
                    raise ValueError('earliest clock-eligible collection time is still ahead')
            home_id, away_id = [teams[league][f[side]]['provider_id'] for side in ('home','away')]
            day = kickoff.date().isoformat()
            url = f'https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={kickoff:%Y%m%d}'
            board = get(url,f'espn-{league}-{day}.json',True)
            params = {'sport':'football','league':LEAGUES[league][0],'date':day,'timezone':'UTC',
                      'status':'scheduled','limit':200}
            page = get(BASE+'/v1/events?'+urlencode(params),f'events-{league}-{day}.json')
            events = read(root/page['file'])['data']
            matches = [e['id'] for e in events if aware(e['scheduled_at'])==kickoff
                       and (e['home_team']['id'],e['away_team']['id'])==(home_id,away_id)]
            if len(matches) != 1:
                raise ValueError('unique scheduled provider event not found')
            event, = matches
            query = urlencode({'markets':'1x2,totals','format':'decimal','period':'full_time'})
            odds = get(BASE+f'/v1/events/{event}/odds?'+query,event+'-odds.json')
            info = {'event_id':event,'espn':board,'events':page,'odds':odds}
            collected = datetime.now(timezone.utc)
            if deadline is not None and collected > deadline:
                raise ValueError('market capture did not finish before the checkpoint')
            f = attach(f,root,info,teams,(deadline or collected).isoformat())
            if deadline is not None:
                f['market_checkpoint']['verified_at'] = datetime.now(timezone.utc).isoformat()
        except (HTTPError, URLError, TimeoutError, KeyError, RuntimeError, ValueError) as error:
            failures[mid] = str(error)
        outputs.append(f)
    write_new(out/'prepared-fixtures.json',outputs)
    write_new(out/'receipts.json',{'captures':list(captures.values()),'failures':failures,
                                   'fixtures':len(fixtures),'fresh_total':sum('total_quote' in f for f in outputs)})
    return outputs


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('fixtures','team-ids','out'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--archive-root', type=Path, help='root used by the eventual review to resolve raw files')
    parser.add_argument('--checkpoint', choices=CHECKPOINTS, help='validate against each fixture’s own pregame deadline')
    args = parser.parse_args()
    collect(read(args.fixtures),read(args.team_ids),args.out,archive_root=args.archive_root,checkpoint=args.checkpoint)
