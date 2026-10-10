"""Train, predict, freeze and review the independent main model."""
import argparse, json
from pathlib import Path
from main_model import train, predict, freeze
from review import evaluate
from context_learning import train_context
from competition_router import classify, format_audit


def read(path): return json.loads(Path(path).read_text())


def main():
    p = argparse.ArgumentParser(); commands = p.add_subparsers(dest='command', required=True)
    t = commands.add_parser('train'); t.add_argument('history'); t.add_argument('--directory',required=True)
    t = commands.add_parser('predict'); t.add_argument('artifact'); t.add_argument('fixture'); t.add_argument('--out',required=True)
    t = commands.add_parser('freeze'); t.add_argument('artifact'); t.add_argument('draft'); t.add_argument('--directory',required=True)
    t = commands.add_parser('review'); t.add_argument('freezes'); t.add_argument('results'); t.add_argument('--population',type=int,required=True); t.add_argument('--out',required=True)
    t = commands.add_parser('train-context'); t.add_argument('artifact'); t.add_argument('samples'); t.add_argument('--out',required=True)
    t = commands.add_parser('classify'); t.add_argument('fixture'); t.add_argument('--out',required=True)
    t = commands.add_parser('audit-decision'); t.add_argument('fixture'); t.add_argument('--out',required=True)
    t = commands.add_parser('diagnose'); t.add_argument('forecasts'); t.add_argument('results'); t.add_argument('--population',type=int,required=True); t.add_argument('--out',required=True)
    t = commands.add_parser('fetch-feed'); t.add_argument('--league-id',type=int,required=True); t.add_argument('--start-time',type=int,required=True); t.add_argument('--end-time',type=int,required=True); t.add_argument('--page',type=int,default=1); t.add_argument('--out',required=True)
    t = commands.add_parser('lookup-research'); t.add_argument('--theme',required=True); t.add_argument('--out',required=True)
    a = p.parse_args()
    if a.command == 'train':
        artifact,report = train(read(a.history)); directory = Path(a.directory); directory.mkdir(parents=True,exist_ok=True)
        for name,value in [('artifact',artifact),('report',report)]:
            with (directory/(name+'.json')).open('x') as f: json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
        print(json.dumps({'directory':str(directory),'selected_recipe':artifact['selected_recipe'], 'test':{k:{kk:vv for kk,vv in v.items() if kk!='records'} for k,v in report['test'].items()}},ensure_ascii=False))
    elif a.command == 'freeze': print(freeze(read(a.artifact),read(a.draft),a.directory))
    else:
        if a.command == 'predict': value = predict(read(a.artifact),read(a.fixture))
        elif a.command == 'audit-decision':
            from decision_audit import assess_decision
            value = assess_decision(read(a.fixture))
        elif a.command == 'diagnose':
            from diagnostics import diagnose
            value = diagnose(read(a.forecasts),read(a.results),a.population)
        elif a.command == 'fetch-feed':
            from provider_feed import fetch_fixtures
            value = fetch_fixtures(a.league_id,a.start_time,a.end_time,a.page)
        elif a.command == 'lookup-research':
            index = read(Path(__file__).resolve().parents[2]/'docs/knowledge/wanwei-methods-v1.json')
            themes = [t for t in index['themes'] if t['id'] == a.theme]
            if not themes: raise ValueError('unknown research theme')
            value = {'evidence_level':index['evidence_level'],'usage':index['usage'],'themes':themes}
        elif a.command == 'classify':
            fixture=read(a.fixture); value=classify(fixture)
            value['format_reasons']=format_audit(fixture,value)
        elif a.command == 'train-context': value = train_context(read(a.artifact),read(a.samples))
        else: value = evaluate(read(a.freezes),read(a.results),a.population)
        with Path(a.out).open('x') as f: json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
        print(a.out)

if __name__ == '__main__': main()
