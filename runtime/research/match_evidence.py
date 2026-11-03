"""Validate supplementary evidence identity, provenance and cutoff times."""
import copy
from datetime import datetime
from urllib.parse import urlsplit, parse_qsl


def aware(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('timezone required')
    return d


def evidence_block(block, fixture):
    from .analysis_freeze import reject_credentials
    reject_credentials(block)
    if block.get('status') not in ('available','partial','scenario','unknown','conflict'):raise ValueError('status')
    if not isinstance(block.get('summary'),str) or not block['summary'].strip():raise ValueError('summary')
    if not isinstance(block.get('facts',{}),dict):raise ValueError('facts')
    if block.get('match_id')!=fixture['match_id']:raise ValueError('match identity')
    cutoff=aware(fixture['cutoff_at'])
    if aware(block['as_of'])>cutoff:raise ValueError('late module')
    if block['status'] in ('available','partial','scenario'):
        if not block.get('sources') or not block.get('facts'):raise ValueError('source-backed facts required')
    for e in block.get('sources',[]):
        u=urlsplit(e['source_url'])
        if (u.scheme!='https' or not u.hostname or u.username or u.password
                or any(k.lower() in {'key','apikey','api_key','token','access_token'} for k,_ in parse_qsl(u.query))):raise ValueError('source')
        if not aware(e['known_at'])<=aware(e['query_time'])<=aware(block['as_of'])<=cutoff:raise ValueError('source time')
    if block['status']=='scenario' and (not block.get('scenarios') or not block.get('uncertainty')):raise ValueError('scenario uncertainty')
    return {k:copy.deepcopy(block[k]) for k in ('status','summary','facts','sources','missing','scenarios','uncertainty') if k in block}
