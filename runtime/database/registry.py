"""Explicit competition registry; no fuzzy league or team identity merges."""
import re
import unicodedata

LEAGUES = {
    'eng.1': ('英超', 'ENG', 'cross', 'E0'),
    'eng.2': ('英冠', 'ENG', 'cross', 'E1'),
    'ger.1': ('德甲', 'GER', 'cross', 'D1'),
    'ger.2': ('德乙', 'GER', 'cross', 'D2'),
    'esp.1': ('西甲', 'ESP', 'cross', 'SP1'),
    'ita.1': ('意甲', 'ITA', 'cross', 'I1'),
    'fra.1': ('法甲', 'FRA', 'cross', 'F1'),
    'por.1': ('葡超', 'POR', 'cross', 'P1'),
    'ned.1': ('荷甲', 'NED', 'cross', 'N1'),
    'ned.2': ('荷乙', 'NED', 'cross', None),
    'swe.1': ('瑞典超', 'SWE', 'calendar', None),
    'nor.1': ('挪超', 'NOR', 'calendar', None),
    'fin.1': ('芬超', 'FIN', 'calendar', None),
    'bra.1': ('巴甲', 'BRA', 'calendar', None),
    'usa.1': ('美职联', 'USA', 'calendar', None),
    'jpn.1': ('日职联', 'JPN', 'japan_transition', None),
    'jpn.2': ('日职乙', 'JPN', 'japan_transition', None),
    'jpn.centennial.1': ('J1百年构想联赛', 'JPN', 'calendar', None),
    'jpn.centennial.2_3': ('J2/J3百年构想联赛', 'JPN', 'calendar', None),
    'jpn.league_cup': ('日本联赛杯', 'JPN', 'calendar', None),
    'jpn.emperor': ('天皇杯', 'JPN', 'calendar', None),
    'kor.1': ('韩K联', 'KOR', 'calendar', None),
    'kor.2': ('韩K2', 'KOR', 'calendar', None),
    'kor.cup': ('韩国杯', 'KOR', 'calendar', None),
    'aus.1': ('澳超', 'AUS', 'cross', None),
    'uefa.champions': ('欧冠', 'UEFA_CLUB', 'cross', None),
    'afc.champions': ('亚冠', 'AFC_CLUB', 'cross', None),
    'uefa.nations': ('欧国联', 'NATIONAL', 'nations', None),
    'fifa.friendly': ('国家队友谊赛', 'NATIONAL', 'calendar', None),
    'eng.league_cup': ('英联杯', 'ENG', 'cross', None),
    'conmebol.libertadores': ('解放者杯', 'CONMEBOL_CLUB', 'calendar', None),
    'ksa.1': ('沙特联', 'KSA', 'cross', None),
}
ALIASES = {
    'EPL': 'eng.1', '英超': 'eng.1', 'DE': 'ger.1', '德甲': 'ger.1',
    'ES': 'esp.1', '西甲': 'esp.1', 'IT': 'ita.1', '意甲': 'ita.1',
    'FR': 'fra.1', '法甲': 'fra.1', 'PT': 'por.1', '葡超': 'por.1',
    'BR': 'bra.1', '巴西甲': 'bra.1', 'AUS': 'aus.1', 'J1': 'jpn.1',
    'J2': 'jpn.2', '日职': 'jpn.1', '日乙': 'jpn.2', 'JCUP': 'jpn.league_cup',
    'K1': 'kor.1', 'K2': 'kor.2', 'KCUP': 'kor.cup', '韩K联': 'kor.1',
    'K League 1': 'kor.1', 'ACLE': 'afc.champions', 'UCL': 'uefa.champions',
    '欧冠': 'uefa.champions', 'MLS': 'usa.1', '美职': 'usa.1', '美职联': 'usa.1',
    '欧国联': 'uefa.nations', 'UEFA Nations League': 'uefa.nations',
    '国际友谊': 'fifa.friendly', 'International Friendly / CFA China Team Chongqing': 'fifa.friendly',
    'Eerste Divisie': 'ned.2', '荷乙': 'ned.2', '荷甲': 'ned.1', '挪超': 'nor.1',
    '瑞典超': 'swe.1', '芬超': 'fin.1', '英冠': 'eng.2', '英联杯': 'eng.league_cup',
    '沙特联': 'ksa.1', 'SA': 'ksa.1', '解放者杯': 'conmebol.libertadores',
}

def normalize_name(value):
    value = unicodedata.normalize('NFKC', str(value)).casefold().strip()
    return re.sub(r'\s+', ' ', value)

def competition(value):
    if value in LEAGUES:
        return value
    if str(value).startswith('UEFA Nations League'):
        return 'uefa.nations'
    return ALIASES.get(value)

def season_for(code, date, declared=None):
    """Calendar competitions must not inherit erroneous 2026/27 labels."""
    year, month = map(int, date[:7].split('-'))
    kind = LEAGUES[code][2]
    if kind == 'japan_transition':
        if year<=2025:return str(year)
        if year==2026 and month<7:return '2026_TRANSITION_UNCLASSIFIED'
        start=year if month>=7 else year-1
        return f'{start}/{str(start+1)[-2:]}'
    if kind == 'calendar':
        return str(year)
    if kind == 'nations':
        start = year if year % 2 == 0 and month >= 7 else year - 1 if year % 2 else year - 2
        return f'{start}/{str(start+1)[-2:]}'
    if declared:
        s = str(declared).replace('-', '/').replace(' ', '')
        if re.fullmatch(r'\d{4}/\d{2,4}', s):
            start, end = s.split('/')
            return f'{start}/{end[-2:]}'
        if re.fullmatch(r'\d{4}', s) and int(s[2:])==int(s[:2])+1 and int(s[:2]) >= 20 and int(s[:2]) < 30:
            return f'20{s[:2]}/{s[2:]}'
    start = year if month >= 7 else year - 1
    return f'{start}/{str(start+1)[-2:]}'

def expected_fd(code, start):
    return 306 if code in ('ger.1', 'por.1', 'ned.1') or (code == 'fra.1' and start >= 2023) else 552 if code == 'eng.2' else 306 if code == 'ger.2' else 380
