"""CLI for future-only candidate prediction and immutable research freezes."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from model import aware,digest,research_predict
from evidence import evidence_before,fit_context,validate_context

def read(path):return json.loads(Path(path).read_text())

def freeze(payload,model,directory):
    if payload.get('freeze_status')!='DRAFT_NOT_FROZEN':raise ValueError('only unmodified candidate drafts')
    fixture=payload['input'];now=datetime.now(timezone.utc)
    if aware(fixture['kickoff_at'])<=now:raise ValueError('cannot retrospectively freeze')
    if fixture.get('identity_verified') is not True or fixture.get('context_audit_complete') is not True:
        raise ValueError('identity/context audit incomplete')
    supported=set()
    for e in fixture.get('audit_evidence',[]):
        evidence_before(e,fixture['cutoff_at'],require_official='lineup' in e.get('supports_field',[]))
        supported.update(e.get('supports_field',[]))
    if not {'fixture','lineup','availability','motivation','integrity'}<=supported:
        raise ValueError('missing auditable context evidence fields')
    if digest(model)!=payload['model_hash']:raise ValueError('model hash mismatch')
    current=research_predict(model,fixture,payload['family'],payload['temperature'])
    for k in ['input_hash','matrix_hash','primary','primary_probability']:
        if current[k]!=payload[k]:raise ValueError('draft does not reproduce')
    saved=dict(payload,freeze_status='CHALLENGER_RESEARCH_FREEZE',frozen_at=now.isoformat(),formal_kpi_eligible=False)
    saved['record_hash']=digest(saved)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    path=directory/(hashlib.sha256(fixture['match_id'].encode()).hexdigest()+'.json')
    with path.open('x') as f:json.dump(saved,f,ensure_ascii=False,indent=2)
    return str(path)

def main():
    parser=argparse.ArgumentParser();commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('predict');p.add_argument('artifact');p.add_argument('fixture');p.add_argument('--out',required=True)
    p=commands.add_parser('freeze');p.add_argument('artifact');p.add_argument('draft');p.add_argument('--directory',required=True)
    p=commands.add_parser('fit-context');p.add_argument('samples');p.add_argument('--features',nargs='+',required=True);p.add_argument('--out',required=True)
    p=commands.add_parser('validate-context');p.add_argument('samples');p.add_argument('--features',nargs='+',required=True);p.add_argument('--out',required=True)
    args=parser.parse_args()
    if args.command=='predict':
        model=read(args.artifact);result=research_predict(model,read(args.fixture),model['selected_family'],model['temperature'])
    elif args.command=='freeze':print(freeze(read(args.draft),read(args.artifact),args.directory));return
    elif args.command=='fit-context':result=fit_context(read(args.samples),args.features)
    else:result=validate_context(read(args.samples),args.features)
    Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(args.out)

if __name__=='__main__':main()
