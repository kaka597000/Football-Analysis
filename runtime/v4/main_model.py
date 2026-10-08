"""Independent WCPP main-model implementation; legacy Champion remains intact."""
import sys, json, math, hashlib
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse
import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'v2'))
sys.path.insert(0, str(ROOT / 'v3'))
from model import (aware, digest, validate_history, fit_strength, fit_nuisance,
                   fit_baseline, rates, matrix, probabilities, total_settlement, partitions)
from context import assess

VERSION = 'WCPP-MAIN-1.0'
RECIPES = ('baseline', 'strength365', 'strength180', 'dc365', 'bivariate365', 'form180', 'blend180')
FEATURES = ('recent_attack', 'opponent_recent_concede', 'recent_points',
            'opponent_recent_points', 'same_cohort_gap', 'opponent_same_cohort_gap')
CONFIG = {'recent_games': 6, 'recent_prior_games': 4, 'form_ridge': 20.,
          'max_log_rate_adjustment': .35, 'temperature': 1., 'selection_min_matches': 40,
          'selection_loss_tolerance': .05}


def code_hash():
    paths = [ROOT/'v2/model.py', ROOT/'v2/evidence.py', ROOT/'v3/context.py', Path(__file__), Path(__file__).with_name('context_learning.py')]
    return digest({str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


def history(rows):
    rows = validate_history(rows)
    for r in rows:
        for k in ('home', 'away'):
            if not isinstance(r[k], str) or not r[k]: raise ValueError('invalid team name')
        if r['home'] == r['away']: raise ValueError('same team fixture')
        if len(r['date']) != 10: raise ValueError('history requires ISO calendar dates')
        if aware(r['observed_at']) > datetime.now(timezone.utc): raise ValueError('future-observed history')
    return rows


def recent_features(rows, fixture):
    """Same calendar date is excluded: no inferred intraday result ordering."""
    date = fixture.get('date') or aware(fixture['kickoff_at']).date().isoformat()
    past = [r for r in rows if r['date'] < date]
    if not past: raise ValueError('no strictly prior feature history')
    avg = max(.1, sum(r['home_goals'] + r['away_goals'] for r in past)/(2*len(past)))
    summaries = {}
    for team in (fixture['home'], fixture['away']):
        games = [r for r in past if team in (r['home'], r['away'])][-CONFIG['recent_games']:]
        if not games: raise ValueError('recent form unavailable for team: ' + team)
        gf = ga = pts = 0
        for r in games:
            h = r['home'] == team
            scored, conceded = (r['home_goals'], r['away_goals']) if h else (r['away_goals'], r['home_goals'])
            gf += scored; ga += conceded; pts += 3 if scored > conceded else 1 if scored == conceded else 0
        n = len(games); prior = CONFIG['recent_prior_games']
        gap = min(14, (datetime.fromisoformat(date)-datetime.fromisoformat(games[-1]['date'])).days)
        summaries[team] = (math.log((gf+prior*avg)/((n+prior)*avg)),
                           math.log((ga+prior*avg)/((n+prior)*avg)),
                           (pts+prior*1.3)/(n+prior), gap/14.)
    h, a = summaries[fixture['home']], summaries[fixture['away']]
    return np.array([[h[0], a[1], h[2], a[2], h[3], a[3]],
                     [a[0], h[1], a[2], h[2], a[3], h[3]]], dtype=float)


def form_training(rows):
    """Five expanding folds. Rate offsets and features precede each target date."""
    dates = sorted({r['date'] for r in rows})
    if len(dates) < 30: raise ValueError('insufficient form training dates')
    edges = sorted(set([dates[int(len(dates)*x)] for x in (.5,.6,.7,.8,.9)]))
    x, offset, y, ledger = [], [], [], []
    for i, start in enumerate(edges):
        end = edges[i+1] if i+1 < len(edges) else '9999-12-31'
        prior = [r for r in rows if r['date'] < start]
        targets = [r for r in rows if start <= r['date'] < end]
        if len(prior) < 60: continue
        base = fit_strength(prior, half_life=180.)
        count = 0
        for r in targets:
            lh, la, missing = rates(base, r)
            if missing: continue
            try: f = recent_features(rows, r)
            except ValueError: continue
            x.extend(f); offset.extend([lh, la]); y.extend([r['home_goals'], r['away_goals']]); count += 1
        ledger.append({'fit_end':prior[-1]['date'], 'targets_start':start, 'targets_end':end,
                       'usable_matches':count})
    if len(y) < 100: raise ValueError('insufficient real out-of-fold form targets')
    x, offset, y = np.array(x), np.array(offset), np.array(y)
    mean, scale = x.mean(axis=0), np.maximum(x.std(axis=0), .05)
    z = (x-mean)/scale; ridge = CONFIG['form_ridge']; cap = CONFIG['max_log_rate_adjustment']
    def objective(b):
        raw = z@b; bounded = np.clip(raw, -cap, cap); mu = offset*np.exp(bounded)
        loss = np.sum(mu-y*np.log(mu)) + ridge*(b@b)
        active = (raw > -cap) & (raw < cap)
        grad = z.T@((mu-y)*active)+2*ridge*b
        return loss, grad
    fit = minimize(objective, np.zeros(len(FEATURES)), jac=True, method='L-BFGS-B', bounds=[(-.5,.5)]*len(FEATURES))
    if not fit.success: raise ValueError('form fit failed: ' + fit.message)
    return {'features':list(FEATURES), 'mean':mean.tolist(), 'scale':scale.tolist(),
            'coefficients':fit.x.tolist(), 'target_count':len(y), 'folds':ledger,
            'max_log_rate_adjustment':cap, 'train_end':rows[-1]['date'],
            'scope':'same-cohort history only; gap is not all-competition rest; no motivation/injury coefficients'}


def fit_core(rows):
    rows = history(rows)
    long = fit_nuisance(fit_strength(rows), rows)
    short = fit_strength(rows, half_life=180.)
    return {'baseline':fit_baseline(rows), 'long':long, 'short':short}


def distribution(core, form, rows, fixture, recipe):
    if recipe not in RECIPES: raise ValueError('unknown recipe')
    base = core['baseline'] if recipe == 'baseline' else core['short'] if recipe in ('strength180','form180','blend180') else core['long']
    lh, la, missing = rates(base, fixture)
    correction = None
    if recipe == 'form180':
        if missing: raise ValueError('cannot apply learned form to unseen teams')
        f = recent_features(rows, fixture)
        z = (f-np.array(form['mean']))/np.array(form['scale'])
        correction = np.clip(z@np.array(form['coefficients']), -form['max_log_rate_adjustment'], form['max_log_rate_adjustment'])
        lh *= math.exp(float(correction[0])); la *= math.exp(float(correction[1]))
    family = 'dixon_coles' if recipe == 'dc365' else 'bivariate' if recipe == 'bivariate365' else 'poisson'
    nuisance = base.get('nuisance',{}).get(family,0.)
    m = matrix(lh, la, family, nuisance)
    if recipe == 'blend180':
        bh, ba, _ = rates(core['baseline'],fixture)
        m = .5*m + .5*matrix(bh,ba)
    # Mixture moments, rather than one component's rates, describe the output matrix.
    means = [float(np.arange(len(m))@m.sum(axis=1)), float(np.arange(len(m))@m.sum(axis=0))]
    return m, {'unseen_teams':missing, 'means':means,
               'form_log_adjustment':None if correction is None else correction.tolist()}


def score_matrices(items):
    n = len(items); hits = wh = oh = unseen = 0; loss = wb = ob = 0.; records = []
    if not n: raise ValueError('empty evaluation')
    for r,m,meta in items:
        h,a = r['home_goals'],r['away_goals']; primary = tuple(map(int,np.unravel_index(np.argmax(m),m.shape)))
        wd = probabilities(m); actual = 0 if h>a else 1 if h==a else 2
        over = total_settlement(m,2.5,'over')['win']
        hit = primary == (h,a); hits += hit; wh += int(np.argmax(wd)) == actual; oh += (over>.5) == (h+a>2.5)
        loss -= math.log(max(float(m[h,a]),1e-300)); wb += float(np.sum((wd-np.eye(3)[actual])**2)); ob += (over-int(h+a>2.5))**2
        unseen += bool(meta['unseen_teams'])
        records.append({'match_id':r['match_id'],'date':r['date'],'primary':f'{primary[0]}-{primary[1]}', 'hit':bool(hit)})
    return {'n':n,'primary_hits':int(hits),'top1':hits/n,'score_log_loss':loss/n,
            'wdl_hits':int(wh),'wdl_accuracy':wh/n,'wdl_brier':wb/n,
            'diagnostic_fixed_2_5_hits':int(oh),'diagnostic_fixed_2_5_accuracy':oh/n,'diagnostic_fixed_2_5_brier':ob/n,
            'unseen_team_matches':unseen,'records':records}


def rolling(rows, targets, form, recipes=RECIPES):
    output = {r:[] for r in recipes}; folds = []
    for month in sorted({r['date'][:7] for r in targets}):
        block = [r for r in targets if r['date'][:7] == month]; first = block[0]['date']
        prior = [r for r in rows if r['date'] < first]; core = fit_core(prior)
        folds.append({'month':month,'fit_end':prior[-1]['date'],'first_target':first,'n':len(block)})
        for row in block:
            for recipe in recipes:
                try: m,meta = distribution(core,form,rows,row,recipe)
                except ValueError:
                    if recipe != 'form180': raise
                    # Preserve full denominator: cold-start matches use short-strength prior, not silently dropped.
                    m,meta = distribution(core,form,rows,row,'strength180')
                    meta['form_fallback'] = True
                output[recipe].append((row,m,meta))
    return {k:score_matrices(v) for k,v in output.items()}, folds


def choose(selection):
    base = selection['baseline']
    usable = [k for k,v in selection.items() if v['n'] >= CONFIG['selection_min_matches']
              and v['primary_hits'] > base['primary_hits']
              and v['score_log_loss'] <= base['score_log_loss'] + CONFIG['selection_loss_tolerance']]
    return max(usable,key=lambda k:(selection[k]['top1'],-selection[k]['score_log_loss'])) if usable else 'baseline'


def comparison(candidate, baseline):
    """Paired date-cluster bootstrap. Acceptance only, never candidate reselection."""
    if [r['match_id'] for r in candidate['records']] != [r['match_id'] for r in baseline['records']]:
        raise ValueError('unpaired evaluation')
    dates = sorted({r['date'] for r in candidate['records']})
    by_date = {d:[] for d in dates}
    for c,b in zip(candidate['records'],baseline['records']):
        by_date[c['date']].append(int(c['hit'])-int(b['hit']))
    sums = np.array([sum(by_date[d]) for d in dates]); counts = np.array([len(by_date[d]) for d in dates])
    sampled = np.random.default_rng(20261008).integers(0,len(dates),(2000,len(dates)))
    values = sums[sampled].sum(axis=1)/counts[sampled].sum(axis=1)
    bounds = list(map(float,np.quantile(values,[.025,.975])))
    passed = bounds[0] > 0 and candidate['score_log_loss'] <= baseline['score_log_loss'] and candidate['wdl_brier'] <= baseline['wdl_brier']
    return {'top1_difference':candidate['top1']-baseline['top1'], 'date_cluster_bootstrap95':bounds,
            'acceptance_gate_passed':bool(passed), 'draws':2000,
            'rule':'positive lower Top1 interval, nonworse score log loss and WDL Brier; no test-based reselection',
            'formal_promotion':False}


def train(rows):
    rows = history(rows)
    first, second, select, test = partitions(rows); initial = first+second
    form = form_training(initial)
    selection, sfolds = rolling(rows,select,form)
    recipe = choose(selection)
    tested, tfolds = rolling(rows,test,form,('baseline',recipe) if recipe != 'baseline' else ('baseline',))
    acceptance = comparison(tested[recipe],tested['baseline'])
    execution = recipe if acceptance['acceptance_gate_passed'] else 'baseline'
    report = {'version':VERSION,'cohort':rows[0]['cohort'],'mode':'RETROSPECTIVE_DATE_ORDER_DIAGNOSTIC',
              'formal_validation':False,'previously_inspected_history':True,
              'selection_rule':'rolling selection Top1 improvement and score-log-loss guard versus baseline; test never selects',
              'config':CONFIG,'selected_recipe':recipe,'execution_recipe':execution,'acceptance':acceptance,'dataset_hash':digest(rows),'code_hash':code_hash(),
              'form_training':form,'selection':selection,'selection_folds':sfolds,'test':tested,'test_folds':tfolds,
              'split':{k:{'n':len(v),'start':v[0]['date'],'end':v[-1]['date']} for k,v in [('initial',initial),('selection',select),('test',test)]},
              'limitations':['historical first-known times unavailable; not prospective validation',
                             'same-cohort spacing cannot establish full rest or congestion',
                             'no trained motivation, lineup, injuries or tactical coefficients',
                             'OU2.5 is a fixed-line diagnostic, not actual market accuracy',
                             'a development comparison cannot prove target attainment'],
              'legacy_champion_unchanged':'V21.5','target_attainment_proven':False,'formal_kpi_added':0}
    artifact = {'version':VERSION,'role':'NEW_MAIN_RESEARCH','cohort':rows[0]['cohort'],
                'selected_recipe':recipe,'execution_recipe':execution,'acceptance':acceptance,'core':fit_core(rows),'form':form_training(rows),
                'history':rows,'train_end':rows[-1]['date'],
                'observed_at':max(r['observed_at'] for r in rows),'built_at':datetime.now(timezone.utc).isoformat(),
                'config':CONFIG,'code_hash':code_hash(),'selection_report_hash':digest(report),
                'legacy_champion_unchanged':'V21.5','permanent_lambda_modified':False,
                'context_coefficients':None,'market_numeric_adjustment':False,
                'formal_validation':False,'formal_freeze_eligible':False}
    return artifact, report


def url(value):
    p = urlparse(value or '')
    return p.scheme == 'https' and bool(p.netloc)


def quote_check(quote, fixture):
    """Require the actual line, bookmaker, fixture mapping and observable times."""
    if not quote: return None
    if quote.get('verified') is not True or quote.get('match_id') != fixture['match_id']:
        raise ValueError('total quote unverified or fixture mismatch')
    if not url(quote.get('source_url')) or not quote.get('bookmaker'):
        raise ValueError('bookmaker/source required')
    cutoff = aware(fixture['cutoff_at']); query = aware(quote['query_time'])
    if query > cutoff: raise ValueError('later market query')
    quoted = quote.get('quoted_at')
    if quoted and aware(quoted) > query: raise ValueError('quote published after query')
    line = quote['line']
    if isinstance(line,bool) or not isinstance(line,(int,float)) or not math.isfinite(line) or line < 0 or abs(line*4-round(line*4)) > 1e-8:
        raise ValueError('invalid actual total line')
    if quote.get('market') not in ('asian_total','standard_total'):
        raise ValueError('market type must be explicit')
    return dict(quote,quoted_at_status='KNOWN' if quoted else 'UNKNOWN',asian_main_line_verified=quote.get('asian_main_line_verified') is True)


def predict(artifact, fixture):
    if artifact.get('version') != VERSION or artifact['code_hash'] != code_hash(): raise ValueError('artifact/code mismatch')
    if artifact.get('config') != CONFIG: raise ValueError('artifact/config mismatch')
    stored = history(artifact['history'])
    if stored[-1]['date'] != artifact['train_end'] or stored[0]['cohort'] != artifact['cohort']:
        raise ValueError('artifact/history mismatch')
    if max(aware(r['observed_at']) for r in stored) > aware(artifact['observed_at']):
        raise ValueError('history observed after artifact data cutoff')
    now = datetime.now(timezone.utc); cutoff = aware(fixture['cutoff_at']); kickoff = aware(fixture['kickoff_at'])
    if cutoff > now or cutoff >= kickoff or kickoff <= now: raise ValueError('genuinely future PreMatch required')
    if fixture.get('phase') != 'PreMatch' or fixture['cohort'] != artifact['cohort']: raise ValueError('phase/cohort mismatch')
    if aware(artifact['observed_at']) > cutoff or aware(artifact['built_at']) > cutoff: raise ValueError('later artifact')
    if artifact['train_end'] >= kickoff.date().isoformat(): raise ValueError('history must precede kickoff calendar date')
    for key in ('context_features','total_line','market_quote','date'):
        if key in fixture: raise ValueError('unsupported manual field: '+key)
    if not fixture.get('sources'): raise ValueError('fixture evidence required')
    for e in fixture['sources']:
        if not url(e.get('source_url')) or aware(e['query_time']) > cutoff: raise ValueError('invalid fixture evidence')
        for k in ('source_published_at','known_at'):
            if e.get(k) and aware(e[k]) > aware(e['query_time']): raise ValueError('later evidence publication/knowledge')
    review = assess(fixture)
    m, meta = distribution(artifact['core'],artifact['form'],artifact['history'],fixture,artifact['execution_recipe'])
    if meta['unseen_teams']: raise ValueError('unseen teams unsupported for main-model prediction')
    from context_learning import adjusted_rates, baseline_contract
    lh,la,context_applied,context_reason = adjusted_rates(artifact,review,*meta['means'],fixture['cutoff_at'])
    if context_applied:
        m = matrix(lh,la)
        meta['means'] = [lh,la]
    meta['context_reason'] = context_reason
    primary = tuple(map(int,np.unravel_index(np.argmax(m),m.shape)))
    wd = dict(zip(('主胜','平局','客胜'),map(float,probabilities(m))))
    ranked = np.argsort(-m.ravel(),kind='stable')[:3]
    quote = quote_check(fixture.get('total_quote'),fixture)
    total = None
    if quote:
        over,under = (total_settlement(m,quote['line'],side) for side in ('over','under'))
        side = max(('over','under'),key=lambda s: (over if s=='over' else under)['win']+.5*(over if s=='over' else under)['half_win']-.5*(over if s=='over' else under)['half_loss']-(over if s=='over' else under)['loss'])
        total = {'quote':quote,'over':over,'under':under,'reference_direction':side,
                 'direction_basis':'unit-odds settlement balance; not betting expected value'}
    reasons = list(review['reasons']) + ['Prospective accuracy not established']
    if not context_applied: reasons.append(context_reason)
    if fixture.get('market_conflict'): reasons.append('Unresolved market conflict')
    return {'version':VERSION,'role':'NEW_MAIN_RESEARCH','generated_at':now.isoformat(),'input':fixture,
            'model_hash':digest(artifact),'model_built_at':artifact['built_at'],'model_observed_at':artifact['observed_at'],
            'baseline_contract':baseline_contract(artifact),'code_hash':code_hash(),'matrix_hash':digest(m.tolist()),'matrix':m.tolist(),
            'recipe':artifact['execution_recipe'],'selected_candidate':artifact['selected_recipe'],'rates':meta,'primary':f'{primary[0]}-{primary[1]}',
            'primary_probability':float(m[primary]),'top3':[{'score':f'{i//len(m)}-{i%len(m)}','probability':float(m.ravel()[i])} for i in ranked],
            'wdl':wd,'wdl_reference':max(wd,key=wd.get),'total':total,'context_assessment':review,
            'context_numeric_adjustment_applied':context_applied,'market_numeric_adjustment_applied':False,
            'recommendation_status':'OBSERVE','reasons':reasons,'probability_calibration_status':'NOT_PROSPECTIVELY_VALIDATED',
            'freeze_status':'DRAFT_NOT_FROZEN','formal_kpi_eligible':False,
            'legacy_champion_unchanged':'V21.5','permanent_lambda_modified':False,'target_attainment_proven':False}


def freeze(artifact, draft, directory):
    if draft.get('freeze_status') != 'DRAFT_NOT_FROZEN': raise ValueError('requires draft')
    if draft['input'].get('identity_verified') is not True: raise ValueError('verified fixture identity required')
    fresh = predict(artifact,draft['input'])
    # Compare all substantive fields, including recommendations and market provenance.
    ignore = {'generated_at'}
    if {k:v for k,v in draft.items() if k not in ignore} != {k:v for k,v in fresh.items() if k not in ignore}:
        raise ValueError('draft does not reproduce')
    frozen = dict(draft,freeze_status='NEW_MAIN_RESEARCH_FREEZE',frozen_at=datetime.now(timezone.utc).isoformat())
    frozen['record_hash'] = digest(frozen)
    directory = Path(directory); directory.mkdir(parents=True,exist_ok=True)
    path = directory/(hashlib.sha256(draft['input']['match_id'].encode()).hexdigest()+'.json')
    with path.open('x') as f: json.dump(frozen,f,ensure_ascii=False,indent=2,allow_nan=False)
    return str(path)
