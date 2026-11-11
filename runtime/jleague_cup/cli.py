"""Japanese League Cup standalone database and research model CLI."""
import argparse,json
from pathlib import Path
from .database import build,stats
from .model import train,predict

def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='command',required=True)
    b=s.add_parser('build');b.add_argument('--manifest',required=True);b.add_argument('--db',required=True)
    t=s.add_parser('train');t.add_argument('--db',required=True);t.add_argument('--out',required=True)
    v=s.add_parser('stats');v.add_argument('--db',required=True)
    f=s.add_parser('predict');f.add_argument('--artifact',required=True);f.add_argument('--fixture',required=True);f.add_argument('--out',required=True)
    a=p.parse_args()
    if a.command=='build':result=build(json.loads(Path(a.manifest).read_text()),a.db)
    elif a.command=='train':
        index=train(a.db,a.out);result={'trained_pairs':len(index['routes']),'untrained_groups':index['untrained'],'active_registry_modified':False}
    elif a.command=='stats':result=stats(a.db)
    else:
        result=predict(a.artifact,json.loads(Path(a.fixture).read_text()))
        with Path(a.out).open('x') as out:json.dump(result,out,ensure_ascii=False,allow_nan=False)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
