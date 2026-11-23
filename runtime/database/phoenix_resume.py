"""Resume missing authorized Phoenix match details from a stored queue after a quota reset."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.request

from phoenix import ORIGIN, SCOPES, received, trial_market


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def official_request(path, credential):
    url = 'https://' + ORIGIN + path
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer '+credential,
                                 'Accept': 'application/json', 'User-Agent': 'WCPP-Phoenix-Research/1.0'})
    try:
        with urllib.request.build_opener(NoRedirect).open(req,timeout=45) as response:
            return response.status, response.read(), response.headers.get('X-Quota-Remaining')
    except urllib.error.HTTPError as error:
        return error.code, b'', error.headers.get('X-Quota-Remaining')


def atomic_write(path, data):
    partial=path.with_suffix(path.suffix+'.tmp')
    partial.write_bytes(data)
    partial.replace(path)


def resume(root, db, limit, fetch, pause=time.sleep):
    pending=[]
    seen={r[0] for r in db.execute("SELECT fixture_id FROM phoenix_market_evidence WHERE feed='trial' AND endpoint='all'")}
    for line in (root/'trial/remaining-market-fixture-queue.jsonl').read_text().splitlines():
        row=json.loads(line)
        mid=str(row['fixture_id'])
        if row['status']=='finished' and mid not in seen:
            pending.append(row)
            seen.add(mid)
    result={'pending_finished_at_start':len(pending),'added':0}
    if not pending or not limit:
        return result
    status, body, remaining=fetch('/api/me')
    if status!=200:
        return dict(result,http_status=status,stopped_at='account')
    account=json.loads(body)['data']
    if remaining is not None and int(remaining)==0:
        return dict(result,quota_remaining=0)
    allowed=set(account['leagues']) & set(SCOPES)
    folder=root/'trial/resumed'
    indices=folder/'receipts'
    indices.mkdir(parents=True,exist_ok=True)
    for item in pending:
        if item['provider_league_code'] not in allowed:
            continue
        mid=str(int(item['fixture_id']))
        index=indices/(mid+'.json')
        if index.exists():
            receipt=json.loads(index.read_bytes())
        else:
            pause(1/max(1,min(5,int(account.get('rps',1)))))
            endpoint='/api/matches/'+mid+'/all?tick_limit=100'
            status,body,remaining=fetch(endpoint)
            if status!=200:
                return dict(result,http_status=status,stopped_at=mid)
            response=json.loads(body)
            if not response.get('ok') or str(response['data'].get('fixture_id'))!=mid:
                raise ValueError('provider returned a different match')
            raw=folder/(mid+'.json.gz')
            atomic_write(raw,gzip.compress(body,mtime=0))
            receipt=dict(kind='trial-market',endpoint='all',fixture_id=mid,url='https://'+ORIGIN+endpoint,
                         file=raw.relative_to(root).as_posix(),observed_at=datetime.now(timezone.utc).isoformat(),
                         sha256=hashlib.sha256(body).hexdigest())
            atomic_write(index,(json.dumps(receipt)+'\n').encode())
        with db:
            source,data=received(db,root,receipt)
            trial_market(db,mid,'all',data,source)
        result['added']+=1
        if remaining is not None:
            result['quota_remaining']=int(remaining)
        if result['added']>=limit or remaining is not None and int(remaining)==0:
            break
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--db',type=Path,required=True)
    ap.add_argument('--max-matches',type=int,default=500,help='0 reports the stored backlog without a network request')
    ap.add_argument('--key-env',default='fenghuangtest')
    args=ap.parse_args()
    if args.max_matches<0 or not args.db.is_file():
        ap.error('select an existing Phoenix database and a nonnegative match limit')
    key=os.environ.get(args.key_env,'').strip()
    if args.max_matches and not key:
        ap.error('provider credential is unavailable')
    folder=args.root/'trial/resumed'
    folder.mkdir(parents=True,exist_ok=True)
    # SQLite releases this local writer lock even if the process is interrupted.
    lock=sqlite3.connect(folder/'worker.sqlite',timeout=1)
    db=sqlite3.connect(args.db,timeout=45)
    try:
        lock.execute('BEGIN IMMEDIATE')
        db.execute('PRAGMA foreign_keys=ON')
        print(json.dumps(resume(args.root,db,args.max_matches,lambda path:official_request(path,key))))
    finally:
        db.close()
        lock.close()


if __name__=='__main__':
    main()
