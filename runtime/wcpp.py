"""WCPP cloud Challenger: auditable Poisson baseline, never legacy Champion."""
import argparse
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

VERSION = 'WCPP-CLOUD-CHALLENGER-0.1'
UTC8 = timezone(timedelta(hours=8))
COHORTS = {'MEN_TOP5_LEAGUE', 'MEN_OTHER_TOP_FLIGHT', 'MEN_LOWER_DIVISION',
           'MEN_DOMESTIC_CUP', 'MEN_CONTINENTAL_CLUB', 'MEN_NATIONAL_COMPETITIVE',
           'MEN_NATIONAL_FRIENDLY', 'MEN_ASIAN_GAMES', 'WOMEN_ASIAN_GAMES',
           'MEN_YOUTH_U23', 'MEN_YOUTH_U20_U17', 'WOMEN_NATIONAL_COMPETITIVE',
           'WOMEN_NATIONAL_FRIENDLY', 'WOMEN_CLUB_LEAGUE', 'WOMEN_YOUTH'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def timestamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('时间必须含时区')
    return dt


def now():
    return datetime.now(UTC8)


def number(value, lower, upper, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + '必须为数值')
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(name + '超出支持范围')
    return float(value)


def pmf(lam, count=81):
    lam = number(lam, 0.01, 12, 'lambda')
    probs = [math.exp(-lam)]
    for k in range(1, count):
        probs.append(probs[-1] * lam / k)
    return probs


def distribution(lh, la, line=2.5):
    """Exact Poisson probabilities; 8+ tails partition mass outside the 64 cells."""
    line = number(line, 0, 15, '大小球盘口')
    if abs(line * 4 - round(line * 4)) > 1e-9:
        raise ValueError('盘口必须为0.25的倍数')
    h, a = pmf(lh), pmf(la)
    grid = [[h[i] * a[j] for j in range(8)] for i in range(8)]
    hm, am = math.fsum(h[:8]), math.fsum(a[:8])
    tails = {'home_8_plus_away_0_to_7': (1 - hm) * am,
             'home_0_to_7_away_8_plus': hm * (1 - am),
             'both_8_plus': (1 - hm) * (1 - am)}
    scores = sorted(((h[i] * a[j], i, j) for i in range(81) for j in range(81)),
                    key=lambda x: (-x[0], x[1], x[2]))
    outcome = {'主胜': 0.0, '平局': 0.0, '客胜': 0.0}
    for i in range(81):
        for j in range(81):
            outcome['主胜' if i > j else '平局' if i == j else '客胜'] += h[i] * a[j]
    # Asian quarter lines split into two half-stakes; pushes are never hidden.
    legs = [line] if round(line * 4) % 2 == 0 else [line - .25, line + .25]
    total_p = pmf(lh + la) if lh + la <= 12 else None
    if total_p is None:
        total_p = [math.fsum(h[i] * a[t-i] for i in range(t+1)) for t in range(81)]
    categories = ('win', 'half_win', 'push', 'half_loss', 'loss')
    markets = {}
    for side in ['over', 'under']:
        buckets = dict.fromkeys(categories, 0.0)
        for goals, prob in enumerate(total_p):
            wins = sum(goals > leg if side == 'over' else goals < leg for leg in legs)
            losses = sum(goals < leg if side == 'over' else goals > leg for leg in legs)
            net = (wins - losses) / len(legs)
            label = {1: 'win', .5: 'half_win', 0: 'push', -.5: 'half_loss', -1: 'loss'}[net]
            buckets[label] += prob
        markets[side] = buckets
    mode, alternative = scores[:2]
    return {'lambda_home': lh, 'lambda_away': la,
            'matrix_0_to_7': grid, 'tail_buckets_8_plus': tails,
            'matrix_hash_sha256': sha({'matrix_0_to_7': grid, 'tail_buckets_8_plus': tails}),
            'raw_mode': f'{mode[1]}-{mode[2]}', 'raw_mode_probability': mode[0],
            'primary': f'{mode[1]}-{mode[2]}', 'primary_probability': mode[0],
            'alternative': f'{alternative[1]}-{alternative[2]}',
            'alternative_probability': alternative[0], 'same_as_mode': True,
            'primary_selection_basis': 'Poisson Raw Mode baseline; no override',
            'override_reason': None, 'one_x_two': outcome,
            'one_x_two_recommendation': max(outcome, key=outcome.get),
            'total_goals': {'line': line, **markets},
            'numerical_remainder_upper_bound': max(0., 1 - math.fsum(h) * math.fsum(a))}


def validate_evidence(evidence, cutoff):
    if not isinstance(evidence, list) or not evidence:
        raise ValueError('缺少来源证据')
    for e in evidence:
        if not e.get('source_url', '').startswith('https://'):
            raise ValueError('来源必须提供HTTPS URL')
        if e.get('source_tier') not in ['L1', 'L2', 'L3'] or not e.get('supports_field'):
            raise ValueError('来源等级和支持字段缺失')
        for field in ['known_at', 'query_time']:
            if timestamp(e[field]) > cutoff:
                raise ValueError('来源时间晚于cutoff: ' + field)
        if e.get('source_published_at') and timestamp(e['source_published_at']) > cutoff:
            raise ValueError('来源发布时间晚于cutoff')


def predict(data):
    if data.get('phase') != 'PreMatch' or data.get('cohort') not in COHORTS:
        raise ValueError('必须明确PreMatch及已识别赛事族')
    for field in ['match_id', 'home', 'away', 'competition', 'identity_scope']:
        if not data.get(field):
            raise ValueError('缺少比赛身份字段: ' + field)
    if data['identity_scope'] not in ['jingcai', 'beidan', 'extended']:
        raise ValueError('身份范围无效')
    cutoff, kickoff = timestamp(data['cutoff_at']), timestamp(data['kickoff_at'])
    if cutoff >= kickoff:
        raise ValueError('赛前cutoff必须早于开赛')
    validate_evidence(data.get('sources'), cutoff)
    if not data.get('lambda_provenance'):
        raise ValueError('必须说明lambda来源；不允许默认填入强度')
    result = distribution(data['lambda_home'], data['lambda_away'], data.get('total_line', 2.5))
    return {'model_version': VERSION, 'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'role': 'Challenger', 'champion_unchanged': 'V21.5',
            'permanent_lambda_modified': False, 'formal_validation': False,
            'freeze_status': 'DRAFT_NOT_FROZEN', 'generated_at': now().isoformat(),
            'input': data, 'input_hash_sha256': sha(data), 'prediction': result,
            'limitations': ['仅Poisson数值基线，不是原V21.5完整实现',
                            'λ由输入或独立候选拟合提供，不代表Champion参数',
                            '未实现未经校准的阵容数值调整或市场翻Primary']}


def freeze(payload, directory, parameters):
    if payload.get('model_version') != VERSION:
        raise ValueError('模型版本不符')
    data = payload['input']
    if data.get('demonstration'):
        raise ValueError('演示数据禁止冻结')
    expected = predict(data)
    if (payload['prediction'] != expected['prediction'] or payload['input_hash_sha256'] != sha(data)
            or payload.get('implementation_sha256') != expected['implementation_sha256']):
        raise ValueError('预测或输入被修改')
    regenerated = apply_parameters(data, parameters)
    if any(regenerated[field] != data.get(field) for field in ['lambda_home', 'lambda_away', 'model_artifact_sha256']):
        raise ValueError('实际参数产物与预测不一致')
    current = now()
    if timestamp(data['cutoff_at']) > current or timestamp(data['kickoff_at']) <= current:
        raise ValueError('只能在当前尚未开赛且cutoff已到的比赛上冻结')
    if data.get('identity_verified') is not True:
        raise ValueError('身份未核实，禁止冻结')
    if data.get('context_audit_complete') is not True:
        raise ValueError('情境审计未完成，禁止冻结')
    if timestamp(payload['generated_at']) > current or timestamp(payload['generated_at']) > timestamp(data['kickoff_at']):
        raise ValueError('生成时间无效')
    config = data.get('model_artifact_sha256')
    if not isinstance(config, str) or len(config) != 64 or any(x not in '0123456789abcdef' for x in config):
        raise ValueError('缺少实际模型参数产物哈希')
    frozen = dict(payload, freeze_status='CHALLENGER_RESEARCH_FREEZE', frozen_at=current.isoformat())
    frozen['record_hash_sha256'] = sha(frozen)
    path = Path(directory) / (frozen['record_hash_sha256'] + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(frozen, f, ensure_ascii=False, indent=2, allow_nan=False)
    return str(path)


def fit(history, cutoff, cohort, half_life=180., prior=8.):
    """New shrinkage estimator, not recovered Champion: match-equivalent prior."""
    cutoff = timestamp(cutoff)
    if cohort not in COHORTS:
        raise ValueError('未知赛事族')
    half_life = number(half_life, 1, 3650, 'half_life')
    prior = number(prior, .01, 1000, 'prior')
    rows = []
    identities = set()
    for row in history:
        if row.get('cohort') != cohort:
            continue
        ko, observed = timestamp(row['kickoff_at']), timestamp(row['result_known_at'])
        if observed < ko:
            raise ValueError('赛果known_at早于开赛')
        if observed >= cutoff or ko >= cutoff:
            continue
        identity = (row['home'], row['away'], ko.isoformat())
        if identity in identities:
            raise ValueError('历史比赛重复，需先核实去重')
        identities.add(identity)
        if row.get('score_basis') != 'regulation_90' or not row.get('source_url', '').startswith('https://'):
            raise ValueError('历史数据必须明确90分钟比分及来源')
        hg, ag = row['home_goals'], row['away_goals']
        if any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 30 for v in [hg, ag]):
            raise ValueError('历史比分无效')
        rows.append((row, math.exp(-math.log(2) * (cutoff - ko).total_seconds() / 86400 / half_life)))
    if len(rows) < 20:
        raise ValueError('同赛事族可用历史不足20场；不复制其他赛事族参数')
    weight = math.fsum(w for _, w in rows)
    home_mean = math.fsum(r['home_goals'] * w for r, w in rows) / weight
    away_mean = math.fsum(r['away_goals'] * w for r, w in rows) / weight
    if min(home_mean, away_mean) <= 0:
        raise ValueError('历史均值无效')
    teams = {}
    for row, w in rows:
        for team, gf, ga, expf, expa in [
                (row['home'], row['home_goals'], row['away_goals'], home_mean, away_mean),
                (row['away'], row['away_goals'], row['home_goals'], away_mean, home_mean)]:
            s = teams.setdefault(team, [0., 0., 0., 0.])
            for i, value in enumerate([gf, ga, expf, expa]):
                s[i] += w * value
    base = (home_mean + away_mean) / 2
    return {'version': VERSION, 'method': 'time_decay_shrinkage_ratios', 'role': 'Challenger',
            'cohort': cohort, 'cutoff_at': cutoff.isoformat(), 'history_rows': len(rows),
            'history_hash_sha256': sha([r for r, _ in rows]),
            'half_life_days': half_life, 'prior_match_equivalents': prior,
            'home_mean': home_mean, 'away_mean': away_mean,
            'teams': {t: {'attack': (s[0] + prior * base) / (s[2] + prior * base),
                          'concede': (s[1] + prior * base) / (s[3] + prior * base)} for t, s in teams.items()}}


def apply_parameters(data, params):
    if params['version'] != VERSION or params['cohort'] != data['cohort']:
        raise ValueError('参数版本或赛事族不匹配')
    if timestamp(params['cutoff_at']) > timestamp(data['cutoff_at']):
        raise ValueError('参数拟合时间晚于预测cutoff')
    try:
        h, a = params['teams'][data['home']], params['teams'][data['away']]
    except KeyError:
        raise ValueError('未知球队，需要别名核实或重新拟合，禁止自动猜测') from None
    return dict(data, lambda_home=params['home_mean'] * h['attack'] * a['concede'],
                lambda_away=params['away_mean'] * a['attack'] * h['concede'],
                lambda_provenance='cloud Challenger fitted artifact', model_artifact_sha256=sha(params))


def evaluate(payload, result):
    if payload.get('freeze_status') != 'CHALLENGER_RESEARCH_FREEZE':
        raise ValueError('仅评价真实Challenger研究冻结记录')
    saved = dict(payload)
    digest = saved.pop('record_hash_sha256')
    if sha(saved) != digest:
        raise ValueError('冻结记录校验失败')
    if result['match_id'] != payload['input']['match_id'] or result.get('score_basis') != 'regulation_90':
        raise ValueError('赛果身份或比分口径错误')
    if timestamp(result['known_at']) < timestamp(payload['input']['kickoff_at']) or timestamp(result['known_at']) > now():
        raise ValueError('赛果known_at不合法')
    if not result.get('source_url', '').startswith('https://'):
        raise ValueError('缺少赛果来源')
    hg, ag = result['home_goals'], result['away_goals']
    if any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 30 for v in [hg, ag]):
        raise ValueError('无效赛果')
    score = f'{hg}-{ag}'
    p = payload['prediction']
    return {'forecast_hash_sha256': digest, 'scope': payload['input']['identity_scope'],
            'sample_phase': 'CHALLENGER_RESEARCH', 'formal_validation': False,
            'actual': score, 'primary_hit': p['primary'] == score,
            'alternative_hit': p['alternative'] == score,
            'result': result, 'evaluated_at': now().isoformat()}


def replay(db):
    path = Path(db).resolve()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    c = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    c.execute('PRAGMA query_only=ON')
    groups = {}
    for raw, in c.execute('SELECT payload_json FROM top1_historical_replay_v1'):
        r = json.loads(raw)
        lh, la = r['lambda']
        h, a = pmf(lh), pmf(la)
        # Ascending goal tie-break matches deterministic lower-score Poisson mode.
        mode = [max(range(len(h)), key=h.__getitem__), max(range(len(a)), key=a.__getitem__)]
        hg, ag = r['actual']
        logloss = lh - hg * math.log(lh) + math.lgamma(hg + 1) + la - ag * math.log(la) + math.lgamma(ag + 1)
        split = 'train' if r['date'] < '2026-02-01' else 'test'
        for key in ['all', split, r['competition']]:
            g = groups.setdefault(key, {'n': 0, 'primary_hits': 0, 'mode_matches_stored': 0, 'logloss_sum': 0.})
            g['n'] += 1
            g['primary_hits'] += mode == r['actual']
            g['mode_matches_stored'] += mode == r['models']['Poisson']['mode']
            g['logloss_sum'] += logloss
    c.close()
    for g in groups.values():
        g['primary_top1'] = g['primary_hits'] / g['n']
        g['mean_score_logloss'] = g.pop('logloss_sum') / g['n']
    return {'role': 'historical_development_replay_not_formal_validation',
            'model': 'Simple Poisson from stored lambda; no refit', 'groups': groups,
            'database_sha256': before,
            'database_unchanged': before == hashlib.sha256(path.read_bytes()).hexdigest()}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)


def main():
    parser = argparse.ArgumentParser(description=VERSION)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('predict'); p.add_argument('input'); p.add_argument('--parameters'); p.add_argument('--out', required=True)
    p = sub.add_parser('freeze'); p.add_argument('payload'); p.add_argument('--parameters', required=True); p.add_argument('--directory', required=True)
    p = sub.add_parser('evaluate'); p.add_argument('payload'); p.add_argument('result'); p.add_argument('--out', required=True)
    p = sub.add_parser('fit'); p.add_argument('history'); p.add_argument('--cutoff', required=True); p.add_argument('--cohort', required=True); p.add_argument('--out', required=True)
    p = sub.add_parser('replay'); p.add_argument('database'); p.add_argument('--out', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'predict':
            data = read(args.input)
            if args.parameters: data = apply_parameters(data, read(args.parameters))
            write(args.out, predict(data))
        elif args.command == 'freeze': print(freeze(read(args.payload), args.directory, read(args.parameters)))
        elif args.command == 'evaluate': write(args.out, evaluate(read(args.payload), read(args.result)))
        elif args.command == 'fit': write(args.out, fit(read(args.history), args.cutoff, args.cohort))
        elif args.command == 'replay': write(args.out, replay(args.database))
        print('HELLO BOSS — 完成：' + args.command)
    except (ValueError, KeyError, OSError, sqlite3.Error) as e:
        parser.exit(1, 'HELLO BOSS — 执行失败：' + str(e) + '\n')


if __name__ == '__main__':
    main()
