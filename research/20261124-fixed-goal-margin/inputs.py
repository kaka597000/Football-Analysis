"""The date-only January roster and its labels use separate projections of a pinned archive."""
import argparse
from datetime import date, timedelta
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import subprocess
import tarfile


HERE = Path(__file__).resolve().parent
PROTOCOL = json.loads((HERE / 'protocol.json').read_bytes())
TRAIN = PROTOCOL['train_bytes_sha256']
TEST = ('2223', '2324', '2425')


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False)


def digest(obj):
    return sha256(canonical(obj).encode()).hexdigest()


def file_hash(path):
    with open(path, 'rb') as stream:
        return sha256(stream.read()).hexdigest()


def load_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def store_rows(path, rows):
    Path(path).write_text(''.join(canonical(r)+'\n' for r in sorted(rows,key=lambda r:r['id'])))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)+'\n')


def reader(root):
    subtree = Path('research/20261124-prior-shot-drought')
    if subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip() != PROTOCOL['reader_commit']:
        raise ValueError('use the documented prior archive reader commit')
    subprocess.run(['git','-C',str(root),'diff','--quiet','HEAD','--',str(subtree/'history.py'),
                    str(subtree/'protocol.json')],check=True)
    path = root / subtree / 'history.py'
    spec=spec_from_file_location('pinned_football_archive_history',path)
    module=module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def historical(path, audit, labels=False):
    if file_hash(path) != PROTOCOL['season_archive_sha256']:
        raise ValueError('mismatched Football-Data archive')
    output=[]
    with tarfile.open(path) as archive:
        for season in TEST:
            origin = date(2000+int(season[2:]),1,1)
            for division,league in PROTOCOL['scope'].items():
                raw=archive.extractfile(f'raw/close/{season}_{division}.csv').read()
                games=audit.unpack_season(raw,division,season)
                if not labels:
                    previous=[dict(date=day,home=h,away=a,goals=audit.score(fields),sot=None)
                              for day,h,a,fields in games if day < origin-timedelta(days=1)]
                    state=audit.make_state(previous,origin)
                for day,h,a,fields in games:
                    if day.year != origin.year or day.month != 1:
                        continue
                    mid=digest([division,day.isoformat(),h,a])
                    if labels:
                        output.append(dict(id=mid,score=audit.score(fields),observed_line=audit.historical_line(fields)))
                    else:
                        output.append(dict(id=mid,league=league,date=day.isoformat(),origin=origin.isoformat(),
                                           home=h,away=a,rates=state.inputs(h,a)['rates'],line=2.5))
    if len(output)!=612 or len({r['id'] for r in output})!=len(output):
        raise ValueError('registered January roster changed')
    return output


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('mode',choices=['inputs','labels'])
    for arg in ('reader','archive','r7','out'):
        p.add_argument('--'+arg,type=Path,required=True)
    p.add_argument('--sealed',type=Path)
    p.add_argument('--lock',type=Path)
    args=p.parse_args()
    auditor=reader(args.reader)
    if args.mode=='labels':
        if not args.sealed or not args.lock:
            p.error('pass the published lock and matching forecast file before opening labels')
        from study import read_lock
        read_lock(args.lock,args.sealed,'january')
    rows=historical(args.archive,auditor,labels=args.mode=='labels')
    if args.mode=='inputs':
        auditor.verify_r7(args.r7,rows)
    store_rows(args.out,rows)
    print(args.mode,len(rows),file_hash(args.out))
