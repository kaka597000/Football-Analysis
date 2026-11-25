from copy import deepcopy
import gzip
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from research_program import lineup_study as s


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def lines(rows):
    return b''.join(encoded(row) + b'\n' for row in rows)


def pack(path, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, 'w:gz' if path.suffix == '.gz' else 'w') as bundle:
        for name, body in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            bundle.addfile(member, BytesIO(body))


def roster(g):
    row = dict(source_match_id=g['id'].split(':')[1], competition=s.CODE, date=g['date']+'T12:00Z',
               event_status='STATUS_FULL_TIME', completed=True, regulation_periods=2,
               source_sha256=sha256(g['id'].encode()).hexdigest())
    for side in s.SIDES:
        team = g[side].split()[-1]
        row[side+'_id'] = team
        row[side+'_goals'] = str(g[side+'_goals'])
        row[side+'_starters'] = [dict(player_id=f'{team}0{i}', starter=True, position_abbreviation=role)
                                 for i,role in enumerate('GDDDDMMMFFF')]
    return row


def original(g, arm):
    line = s.base.actual_line(g)
    picks = dict(primary='4-4', top3=['4-4','1-1','0-1'], total_side='under' if line else None,
                 total_line=line, wdl={'r5':'draw','r7':'home','r10':'away'}[arm])
    row = dict(id=g['id'], cohort=s.CODE, date=g['date'], stage=g['stage'], home=g['home'], away=g['away'],
               source_sha256=g['source_sha256'], actual=[g['home_goals'],g['away_goals']], line=line,
               forecast=dict(candidate=picks, market={'wdl':'draw'}))
    if arm == 'r10':
        row['forecast']['r7'] = dict(picks, wdl='home')
    if arm != 'r5':
        row['window'] = 'evaluation' if g['date'] < s.BOUNDS[2] else 'recent'
    return row


def fixtures():
    games = []
    for day, pairs in [
        ('2023-12-02', [(1,2)]), ('2023-12-12', [(2,1)]), ('2023-12-20', [(1,2)]),
        ('2024-01-04', [(2,1)]), ('2024-12-15', [(1,2)]), ('2024-12-23', [(2,1)]),
        ('2025-01-05', [(1,2),(3,4),(5,6),(7,8)]), ('2025-01-12', [(2,1),(4,3)]),
        ('2026-01-03', [(1,2)])]:
        for home,away in pairs:
            idx = len(games) + 1
            games.append(dict(id=f'espn:{idx}', competition=s.CODE, season=day[:4], stage='regular',
                         score_basis='regulation_90', date=day, home=f'Club {home}', away=f'Club {away}',
                         home_goals=idx%3, away_goals=(idx+1)%2, neutral=False, h2h=None,
                         total=dict(line=2.25,over=2.,under=2.) if day[:4]=='2026' else None,
                         paired_market=False, sources=[]))
    return games


def archives(root, games):
    events = []
    for g in games:
        teams = [dict(homeAway=side, team=dict(id=g[side].split()[-1],displayName=g[side]), score=str(g[side+'_goals']))
                 for side in s.SIDES]
        mid = g['id'].split(':')[1]
        events.append(dict(id=mid, competitions=[dict(id=mid, date=g['date']+'T12:00Z', competitors=teams,
                          status=dict(type=dict(completed=True,name='STATUS_FULL_TIME')))]))
    board = encoded(dict(leagues=[{'slug':s.CODE}],events=events))
    key = sha256(board).hexdigest()
    for g in games:
        g['source_sha256'] = key
    r5, r7, r10 = [root/k for k in ('r5','r7','r10')]
    file5 = {'panel.jsonl':lines(games)}
    held = [g for g in games if g['date']>=s.BOUNDS[1]]
    for name, rows in [('evaluation',[g for g in held if g['date']<s.BOUNDS[2]]),
                       ('recent',[g for g in held if g['date']>=s.BOUNDS[2]])]:
        file5['study/'+name+'.jsonl'] = lines(original(g,'r5') for g in rows)
    model = 'models/jpn.1.tar.gz'
    pack(r5/model,file5)
    pack(r5/'evidence/boards.tar',{key+'.gz':gzip.compress(board)})
    (r5/'evidence/source-index.json').write_bytes(encoded({key:dict(archive='boards.tar',member=key+'.gz')}))
    panel_sha = sha256(file5['panel.jsonl']).hexdigest()
    settings = encoded(dict(competition=s.CODE, version=s.r7.VERSION, source_panel_sha256=panel_sha, bounds=s.BOUNDS,
                            training_games=3, selection=dict(mix=0)))
    baseline = encoded(dict(version=s.r7.VERSION, competition=s.CODE, through=games[-1]['date'],
                            state=s.r7.dump(s.r7.blank()), policy=dict(mix=0), fit_sha256=sha256(settings).hexdigest()))
    pack(r7/model,{'main/fit.json':settings, 'main/forward.json':baseline,
         'main/forecasts.jsonl.gz':gzip.compress(lines(original(g,'r7') for g in held))})
    shot = encoded(dict(version=s.decision.VERSION, competition=s.CODE, bounds=s.BOUNDS, panel_sha256=[panel_sha],
                        train_games=3, validation_games=3))
    pack(r10/'replay/main.tar.gz',{'main/jpn.1/fit.json':shot, 'main/jpn.1/forward.json':encoded(dict(
         version=s.decision.VERSION, competition=s.CODE, through=games[-1]['date'], fit_sha256=sha256(shot).hexdigest(),
         r7_forward_sha256=sha256(baseline).hexdigest())),
         'main/jpn.1/forecasts.jsonl.gz':gzip.compress(lines(original(g,'r10') for g in reversed(held)))})
    for directory in (r5,r7,r10):
        hashes = {p.relative_to(directory).as_posix():s.sha(p) for p in directory.rglob('*') if p.is_file()}
        (directory/'manifest.json').write_bytes(encoded(dict(files=hashes)))
    return r5,r7,r10


class LineupStudyTests(unittest.TestCase):
    def test_cli_retains_partial_population_real_lines_controls_and_forward_cutoff(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            games = fixtures()
            paths = archives(root, games)
            source = root/'rosters.jsonl'
            source.write_bytes(lines(roster(g) for g in games if g['date'] < '2025' and g != games[1]))
            manifest = root/'snapshot-manifest.json'
            snapshot = dict(schema='j1-lineups-history-intake-snapshot-v1', events_jsonl_sha256=s.sha(source),
                            events_jsonl_rows=5, snapshot_as_of_utc='2026-11-25T00:00:00Z',
                            r5_panel_member_sha256=sha256(lines(games)).hexdigest(),
                            r5_panel_package_sha256=s.sha(paths[0]/'models/jpn.1.tar.gz'))
            manifest.write_bytes(encoded(snapshot))
            out = root/'output'
            flags = dict(zip(('r5','r7','r10'),paths), rosters=source, out=out)
            args = [item for k,p in flags.items() for item in ('--'+k,str(p))]
            cmd = subprocess.run([sys.executable,'-m','research_program.lineup_study',*args],
                                 cwd=Path(s.__file__).resolve().parents[1], capture_output=True, text=True)
            self.assertEqual(cmd.returncode,0,cmd.stderr)
            self.assertEqual(json.loads(cmd.stdout)['evaluation']['population'],6)
            coverage = s.control.read(out/'coverage.json')
            self.assertEqual(coverage['overall']['status'],'PARTIAL')
            self.assertEqual(coverage['folds']['evaluation']['both_complete_rosters'],0)
            self.assertEqual(coverage['folds']['evaluation']['both_prior_rosters'],1)
            records = s.archive.read_gzip(out/'forecasts.jsonl.gz')
            ordered = sorted(games[6:], key=lambda g: (g['date'], g['id']))
            self.assertEqual([r['id'] for r in records],[g['id'] for g in ordered])
            for row in records:
                tips = row['forecast']
                self.assertEqual((tips['r5']['wdl'], tips['r7']['wdl'], tips['r10']['wdl']),('draw','home','away'))
                self.assertEqual(tips['market'],{'wdl':'draw'})
                for arm in ('candidate','form_only','form_matched_dose','fixed_form','fixed_full'):
                    self.assertEqual(tips[arm]['total_line'],row['line'])
                    self.assertEqual(tips[arm]['total_side'] is None,row['line'] is None)
            metrics = s.control.read(out/'metrics.json')
            old, new = metrics['evaluation'], metrics['recent']
            self.assertEqual((old['population'],old['measured_lines'],new['population'],new['measured_lines']),(6,0,1,1))
            self.assertEqual(old['scores']['candidate']['metrics']['actual_ou']['eligible'],0)
            self.assertEqual(new['scores']['candidate']['metrics']['actual_ou']['eligible'],1)
            self.assertEqual(old['ranking']['all']['short_days'],1)
            pair = old['paired']['form_only']['metrics']['score_top1']
            self.assertEqual(pair['gained']-pair['lost'],pair['net_hits'])
            self.assertEqual(len(pair['date_cluster_ci']),2)
            fit = s.control.read(out/'fit.json')
            recipe = s.control.read(out/'forward.json')
            self.assertEqual(fit['fitted']['form']['features'],list(s.FEATURES[:2]))
            self.assertNotIn('xg_only',fit['fitted']['form'])
            self.assertEqual((fit['training_games'],fit['validation_games']), (3,3))
            self.assertEqual(recipe['doses'],fit['doses'])
            self.assertEqual(recipe['fit_sha256'],s.sha(out/'fit.json'))
            self.assertEqual(recipe['study_inputs_sha256'],s.sha(out/'inputs.json'))
            self.assertEqual(recipe['through'],games[-1]['date'])
            self.assertEqual(recipe['fitted']['full']['training_games'],len(games))
            self.assertEqual(recipe['rosters_sha256'],s.sha(source))
            self.assertEqual(recipe['roster_manifest_sha256'],s.sha(manifest))
            self.assertEqual(len(s.archive.read_gzip(out/'roster-coverage.jsonl.gz')),len(games))
            source.write_text('')
            empty = root/'empty'
            with self.assertRaisesRegex(ValueError, 'snapshot manifest'):
                s.run(*paths, source, empty)
            self.assertFalse(empty.exists())
            manifest.write_bytes(encoded(dict(snapshot, events_jsonl_rows=0, events_jsonl_sha256=s.sha(source))))
            s.run(*paths, source, empty)
            self.assertEqual(len(s.archive.read_gzip(empty/'forecasts.jsonl.gz')),len(records))
            self.assertEqual(s.control.read(empty/'coverage.json')['overall']['status'],'NO_VERIFIED_ROSTERS')

    def test_roster_identity_unknown_positions_and_incomplete_sides_keep_calendar_slots(self):
        games = fixtures()[:3]
        for g in games:
            for side in s.SIDES:
                g[side+'_id'] = g[side].split()[-1]
        a,b,c = [roster(g) for g in games]
        a['away_starters'][0]['position_abbreviation'] = 'unknown'
        b['home_id'],b['away_id'] = b['away_id'],b['home_id']
        c['home_starters'][0]['starter'] = False
        checked,audit,outside = s.roster_inputs(games,[a,b,c,dict(c,source_match_id='99999')])
        self.assertEqual(outside,1)
        self.assertEqual(len(audit),len(games))
        self.assertEqual(audit[games[0]['id']]['away']['status'],'unknown_or_ambiguous_position')
        self.assertIsNone(checked[games[0]['id']]['away_starters'])
        self.assertEqual(len(checked[games[0]['id']]['home_starters']),11)
        self.assertNotIn(games[1]['id'],checked)
        self.assertEqual(audit[games[2]['id']]['home']['explicit_starters'],10)
        self.assertIsNone(checked[games[2]['id']]['home_starters'])
        for marker in ('extra_time_indicated','shootout_indicated'):
            checked, _, _ = s.roster_inputs(games, [dict(a, **{marker:True})])
            self.assertFalse(checked)
        sided = dict(roster(games[0]), duplicate_or_missing_sides=True, roster_side_counts={'home':2,'away':1})
        checked, _, _ = s.roster_inputs(games,[sided])
        self.assertIsNone(checked[games[0]['id']]['home_starters'])
        self.assertEqual(len(checked[games[0]['id']]['away_starters']),11)

    def test_ranking_uses_selected_cell_and_three_first_hits_only(self):
        prior = np.full(s.base.H.shape, .001)
        prior[0,0] = 1.
        prior /= prior.sum()
        g = fixtures()[-1]
        g['source_sha256'] = 'a'*64
        ref = original(g,'r7')
        published = dict(r7=ref['forecast']['candidate'])
        fitted = {arm:s.fit([],[],[],form_only=arm=='form') for arm in ('form','full')}
        doses = {arm:dict(score=0,wdl=0,total=0) for arm in fitted}
        r = s._forecast((ref,published),prior,np.zeros((2,6)),{},fitted,doses)
        self.assertAlmostEqual(r['quality']['candidate']['p_first'],prior[4,4])
        self.assertLess(r['quality']['candidate']['p_first'],prior.max())
        fixtures_by_day = [dict(r,id=mid,date='2025-01-01',actual=[4,4] if mid!='b' else [1,1])
                           for mid in ('d','b','a','c')]
        fixtures_by_day += [dict(r,id='e',date='2025-01-02',actual=[4,4])]
        days = s.selected_days(fixtures_by_day)
        self.assertEqual([z['id'] for z in days[0]['selected']],['a','b','c'])
        self.assertIs(days[0]['three_first_sweep'],False)
        self.assertIsNone(days[1]['three_first_sweep'])
        summary = s._rank_summary(days)
        self.assertEqual((summary['full_days'],summary['short_days'],summary['three_first_sweeps']),(1,1,0))
        self.assertEqual((summary['individual']['first_hits'],summary['individual']['ordered_three_hits']),(3,4))
        self.assertEqual(summary['per_seat']['1']['selected'],2)

    def test_independent_and_full_matched_form_doses_preserve_published_zero_heads(self):
        prior = np.full(s.base.H.shape, 1. / s.base.H.size)
        g = fixtures()[-1]
        g['source_sha256'] = 'f'*64
        ref = original(g,'r7')
        old = ref['forecast']['candidate']
        fitted = {arm:s.fit([],[],[],form_only=arm=='form') for arm in ('form','full')}
        fitted['form']['scoring_beta'][0] = 1.
        fitted['full']['scoring_beta'][2] = 1.
        x = np.zeros((2,6))
        x[0,[0,2]] = .5
        doses = dict(full=dict(score=0,wdl=1,total=0),form=dict(score=1,wdl=0,total=1))
        r = s._forecast((ref,dict(r7=old)),prior,x,{},fitted,doses)['forecast']
        self.assertEqual(r['form_only']['top3'],r['fixed_form']['top3'])
        self.assertNotEqual(r['form_only']['top3'],old['top3'])
        self.assertEqual(r['form_matched_dose']['top3'],old['top3'])
        self.assertEqual(r['candidate']['wdl'],r['fixed_full']['wdl'])
        self.assertEqual(r['form_only']['wdl'],old['wdl'])

    def test_controls_reject_missing_or_spliced_published_rows(self):
        g = fixtures()[-1]
        g['source_sha256'] = 'e'*64
        a,b,c = [original(g,arm) for arm in ('r5','r7','r10')]
        a['window'] = b['window']
        with self.assertRaisesRegex(ValueError,'population'):
            s._controls([g],[a],[b],[])
        wrong = deepcopy(c)
        wrong['line'] = 2.5
        with self.assertRaisesRegex(ValueError,'line differs'):
            s._controls([g],[a],[b],[wrong])
        wrong = deepcopy(c)
        wrong['forecast']['market']['wdl'] = 'home'
        with self.assertRaisesRegex(ValueError,'controls disagree'):
            s._controls([g],[a],[b],[wrong])

    def test_changing_a_holdout_starting_eleven_reaches_only_later_dates(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            games = fixtures()
            paths = archives(root, games)
            source = root / 'rosters.jsonl'
            rows = [roster(g) for g in games]
            target = next(g for g in games if g['date'] == '2025-01-05' and g['home'] == 'Club 1')
            results = []
            for label in ('original', 'revised'):
                if label == 'revised':
                    xi = next(r for r in rows if 'espn:'+r['source_match_id'] == target['id'])
                    for player in xi['home_starters']:
                        player['player_id'] = 'replacement-' + player['player_id']
                source.write_bytes(lines(rows))
                out = root / label
                s.run(*paths, source, out)
                results.append({r['id']: r for r in s.archive.read_gzip(out/'forecasts.jsonl.gz')})
            self.assertEqual((root/'original/fit.json').read_bytes(), (root/'revised/fit.json').read_bytes())
            before, after = results
            for g in games:
                if g['date'] == target['date']:
                    for key in ('features', 'forecast', 'quality'):
                        self.assertEqual(before[g['id']][key], after[g['id']][key])
            following = next(g['id'] for g in games if g['date'] == '2025-01-12' and g['away'] == 'Club 1')
            self.assertNotEqual(before[following]['features'], after[following]['features'])
