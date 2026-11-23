"""Interpret the explicit provenance contracts of the retained same-book market collectors."""
from urllib.parse import urlsplit


def paired_market(row):
    flag=row.get('paired_market')
    if flag is True:
        return True
    if flag is False or not row.get('h2h') or not row.get('total'):
        return False
    source=row.get('source')
    bet365=(flag=='paired Bet 365 HDA and O/U from same ESPN event summary response'
            or source=='ESPN core prematch')
    asian=(source=='AsianOdds event snapshot' and row.get('bookmaker')=='BetInAsia via AsianOdds')
    for evidence in row.get('sources',[]):
        url=urlsplit(evidence.get('url') or '')
        if not evidence.get('sha256'):
            continue
        if (bet365 and row.get('bookmaker')=='Bet 365' and url.hostname=='site.api.espn.com'
                and url.path.endswith('/summary') and 'event=' in url.query):
            return True
        if asian and url.hostname=='asianodds.org' and evidence.get('event_id') and evidence.get('captured_at'):
            return True
    return False
