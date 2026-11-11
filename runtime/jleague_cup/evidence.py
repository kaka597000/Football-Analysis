"""Reconstruct Japanese League Cup phases and regulation scores from retained HTML."""
from datetime import datetime,timezone
import hashlib,html,json,re
from pathlib import Path
from urllib.parse import urlparse,urljoin

CODE='jpn.league_cup'

def clean(value):return html.unescape(re.sub('<[^>]+>','',value)).strip()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def aware(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('source timezone required')
    return result.astimezone(timezone.utc)

def source(source):
    p=Path(source['raw_path']);body=p.read_bytes()
    if not source.get('raw_sha256') or hashlib.sha256(body).hexdigest()!=source['raw_sha256']:raise ValueError('raw hash mismatch')
    url=urlparse(source['source_url'])
    if url.scheme!='https' or url.hostname!='www.betexplorer.com' or url.port not in (None,443):raise ValueError('supported source host required')
    match=re.fullmatch(r'/football/japan/ybc-levain-cup-(\d{4})/(?:results/|[^/]+/[^/]+/)',url.path)
    if not match:raise ValueError('exact Japanese League Cup source required')
    if source.get('query_time') and aware(source['query_time'])>datetime.now(timezone.utc):raise ValueError('future source capture')
    return body.decode('utf-8'),int(match.group(1))

def phase(label):
    text=re.sub(r'1X2$','',label).strip()
    lower=text.lower();leg=2 if 'second leg' in lower else 1 if 'first leg' in lower else None
    if re.search(r'\bgroup [a-z]\b',lower):stage='group-stage'
    elif 'quarter-final' in lower:stage='quarterfinals'
    elif 'semi-final' in lower:stage='semifinals'
    elif re.search(r'^final\b',lower):stage='final'
    else:
        r=re.search(r'1/(\d+)-finals',lower);stage='round-of-'+str(2*int(r.group(1))) if r else None
    form='group-home-away' if stage=='group-stage' else 'two-leg' if leg else 'single' if stage else None
    return {'stage':stage,'leg':leg,'tie_format':form,'stage_label':text}

def parse_list(s,url):
    parsed=urlparse(url);match=re.fullmatch(r'/football/japan/ybc-levain-cup-(\d{4})/results/',parsed.path)
    if not match or parsed.hostname!='www.betexplorer.com':raise ValueError('exact results listing required')
    season=match.group(1);label=None;output=[]
    for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>',s,re.S):
        if '<th' in tr:label=clean(tr)
        cells=re.findall(r'<td\b[^>]*>(.*?)</td>',tr,re.S)
        if len(cells)<3 or 'in-match' not in cells[0]:continue
        names=clean(cells[0]).split(' - ')
        link=re.search(r'href="([^"]+)"',cells[0])
        if len(names)!=2 or not link:continue
        event_url=urljoin(url,html.unescape(link.group(1)));event_path=urlparse(event_url).path
        if not re.fullmatch('/football/japan/ybc-levain-cup-'+season+r'/[^/]+/[^/]+/',event_path):continue
        try:date=datetime.strptime(clean(cells[-1]),'%d.%m.%Y').date().isoformat()
        except ValueError:continue
        display=clean(cells[1]);score=re.fullmatch(r'(\d+):(\d+)',display)
        h,a=map(int,score.groups()) if score else (None,None)
        if any(x is not None and not 0<=x<=40 for x in (h,a)):raise ValueError('score outside support')
        info=phase(label or '')
        output.append({'event_url':event_url,'provider_event_id':event_path.rstrip('/').rsplit('/',1)[-1],
                       'season':season,'date':date,'home_name':names[0],'away_name':names[1],
                       'home_90':h,'away_90':a,'display_score':display,'raw_row':tr,
                       **info})
    return output

def periods(s,url,second_leg=False):
    canonical=re.search(r'<link[^>]*rel="canonical"[^>]*href="([^"]+)"',s)
    if not canonical or html.unescape(canonical.group(1)).rstrip('/')!=url.rstrip('/'):raise ValueError('detail canonical identity mismatch')
    finished=re.search(r'<input[^>]*id="isFinished"[^>]*>',s)
    if not finished or 'value="1"' not in finished.group(0):raise ValueError('explicit finished mark required')
    def field(name):
        m=re.search(r'id="'+name+r'"[^>]*>(.*?)</',s,re.S)
        if not m:raise ValueError('detail period field missing:'+name)
        return clean(m.group(1))
    status,partial,display=field('js-eventstage'),field('js-partial'),field('js-score')
    if not re.fullmatch(r'\([0-9:,\s]+\)',partial):raise ValueError('unrecognized period increments')
    values=[tuple(map(int,x)) for x in re.findall(r'(\d+):(\d+)',partial)]
    shown=tuple(map(int,display.split(':')))
    if status=='After Extra Time' and len(values) in (3,4):
        if tuple(map(sum,zip(*values)))!=shown:raise ValueError('ET total mismatch')
        et=tuple(map(sum,zip(*values[2:])));pen=(None,None)
    elif status=='After Penalties' and len(values) in (3,4,5):
        before=tuple(map(sum,zip(*values[:-1])));pen=values[-1]
        if (before[0]!=before[1] and not second_leg) or pen[0]==pen[1] or (shown[0]>shown[1])!=(pen[0]>pen[1]):raise ValueError('shootout identity/score conflict')
        et=tuple(map(sum,zip(*values[2:-1]))) if len(values)>3 else (0,0)
    else:raise ValueError('unverified extra-time/penalty basis')
    return {'home_90':values[0][0]+values[1][0],'away_90':values[0][1]+values[1][1],
            'home_et':et[0],'away_et':et[1],'home_pen':pen[0],'away_pen':pen[1],
            'periods':values,'status':status}

def verify_row(row):
    if row.get('competition')!=CODE:raise ValueError('competition mismatch')
    evidence=row['evidence'];text,season=source(evidence)
    matches=[x for x in parse_list(text,evidence['source_url']) if x['event_url'].rstrip('/')==row['event_url'].rstrip('/')]
    if len(matches)!=1:raise ValueError('source match absent or duplicated')
    fact=matches[0]
    if row['date']!=fact['date'] or str(row['season'])!=str(season):raise ValueError('source date/edition mismatch')
    for side in ('home','away'):
        if row[side+'_name']!=fact[side+'_name']:raise ValueError('source team orientation mismatch')
    if not fact['stage'] or not fact['tie_format']:raise ValueError('unknown phase is inventory only')
    if row.get('stage') not in (None,fact['stage']):raise ValueError('supplied phase conflicts with raw')
    if row.get('leg') not in (None,fact['leg']):raise ValueError('supplied leg conflicts with raw')
    if fact['home_90'] is None:
        detail=row.get('period_evidence')
        if not detail:raise ValueError('explicit regulation periods required')
        raw,detail_year=source(detail)
        if detail_year!=season:raise ValueError('period edition mismatch')
        fact.update(periods(raw,fact['event_url'],fact['leg']==2))
    for side in ('home','away'):
        supplied=row.get(side+'_goals',row.get(side+'_90'))
        if supplied is not None and supplied!=fact[side+'_90']:raise ValueError('source regulation score conflict')
    if fact['date']>=datetime.now(timezone.utc).date().isoformat():raise ValueError('future/today label rejected')
    return fact

def classify(row):
    try:
        f=verify_row(row)
        # Edition is part of the identity until independent format equivalence
        # is certified. No inferred 2024/2026 compatibility or J1 fallback.
        identity={'competition':CODE,'gender':'men','age':'senior','entity':'club','kind':'domestic_cup',
                  'era':'edition-'+f['season'],'stage':f['stage'],'tie_format':f['tie_format'],'leg':f['leg']}
        if row.get('gender') not in ('men','male') or row.get('age')!='senior' or row.get('score_basis')!='regulation_90':raise ValueError('explicit senior-men regulation identity required')
        return {'complete':True,'classification':identity,'route_key':'|'.join(map(str,identity.values())),
                'active_route_gap':True,'status':'RESEARCH_ONLY_NOT_ACTIVITY_ROUTE','reasons':[]}
    except (ValueError,TypeError,KeyError,OSError,AttributeError) as e:
        return {'complete':False,'classification':None,'route_key':None,'reasons':[str(e)],'active_route_gap':True}
