"""Evidence-only, same-bookmaker/market/line price movement. No forecast override."""
from datetime import datetime
import math
from urllib.parse import urlsplit,parse_qsl


def aware(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None: raise ValueError('timezone required')
    return t


def analyze(fixture):
    result={'status':'NO_SNAPSHOTS','comparisons':[],'rejected_snapshots':0,
            'prediction_modified':False,'money_flow_inferred':False}
    groups={}
    for row in fixture.get('market_snapshots',[]):
        try:
            source=urlsplit(row['source_url'])
            if (source.scheme!='https' or not source.hostname or source.username or source.password
                    or any(k.lower() in {'apikey','api_key','token','access_token'} for k,_ in parse_qsl(source.query))):raise ValueError('source')
            if row['match_id']!=fixture['match_id']:raise ValueError('identity')
            if not aware(row['quoted_at'])<=aware(row['query_time'])<=aware(fixture['cutoff_at'])<aware(fixture['kickoff_at']):raise ValueError('time')
            market=row['market'];line=row.get('line')
            expected={'1x2':{'home','draw','away'},'totals':{'over','under'},'asian_handicap':{'home','away'}}[market]
            if market!='1x2' and (type(line) not in (int,float) or not math.isfinite(line)):raise ValueError('line')
            if market=='1x2' and line is not None:raise ValueError('line')
            if not isinstance(row['bookmaker'],str) or not row['bookmaker'].strip():raise ValueError('bookmaker')
            odds=row['decimal_odds']
            if set(odds)!=expected or any(type(v) not in (int,float) or not math.isfinite(v) or v<=1 for v in odds.values()):raise ValueError('odds')
            key=(row['bookmaker'],market,line)
            groups.setdefault(key,[]).append(row)
        except (KeyError,ValueError,TypeError,AttributeError):result['rejected_snapshots']+=1
    for (book,market,line),rows in groups.items():
        rows.sort(key=lambda x:aware(x['quoted_at']))
        first,last=rows[0],rows[-1]
        if aware(first['quoted_at'])==aware(last['quoted_at']):continue
        result['comparisons'].append({'bookmaker':book,'market':market,'line':line,
            'first_quoted_at':first['quoted_at'],'last_quoted_at':last['quoted_at'],
            'decimal_odds_change':{k:last['decimal_odds'][k]-first['decimal_odds'][k] for k in first['decimal_odds']},
            'first_decimal_odds':first['decimal_odds'],'last_decimal_odds':last['decimal_odds'],
            'meaning':'Observed price change only; different lines not compared; no betting-flow or predictive-effect claim'})
    result['status']='COMPARABLE_HISTORY' if result['comparisons'] else ('INSUFFICIENT_COMPARABLE_HISTORY' if groups else ('INVALID_OR_UNTIMED_SNAPSHOTS' if result['rejected_snapshots'] else 'NO_SNAPSHOTS'))
    return result
