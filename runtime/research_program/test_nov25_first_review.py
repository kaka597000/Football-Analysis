from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research_program import nov25_first_review as review


class IncrementalFirstReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.mid = 'espn:99'
        self.fixtures = {self.mid:dict(match_id=self.mid,cohort='afc.champions:ELITE:league_phase',stage='league_phase',
                                       home='espn:1',away='espn:2',kickoff_at='2026-11-25T10:00Z')}
        self.rows = []
        self.now = review.aware('2026-11-25T14:00+00:00')

    def capture(self, *, kind='summary', status='STATUS_FULL_TIME', observed='12:00', score=(2,1),
                start='10:00:31', edit=None, parsed=None):
        sides = [dict(homeAway=s, team=dict(id=str(i)), score=str(g),
                      linescores=[dict(displayValue=str(g)),dict(displayValue='0')])
                 for i,(s,g) in enumerate(zip(('home','away'),score),1)]
        state = dict(name=status,state='post' if status=='STATUS_FULL_TIME' else 'in',completed=status=='STATUS_FULL_TIME')
        f = dict(id='99',date=self.fixtures[self.mid]['kickoff_at'],status=dict(type=state,period=2),competitors=sides)
        if kind == 'summary':
            f['groups'] = dict(id='1', name='Group A')
        else:
            f['group'] = dict(groupId='1', name='East Region')
        season = dict(year=2026,type=14524,slug='league-stage',name='2026-27 AFC Champions League Elite, League Stage')
        event = dict(id='99',season=season,league=dict(slug='afc.champions'),competitions=[f])
        keys = [] if start is None else [dict(type=dict(type='kickoff',id='80'),period=dict(number=1),
                                                clock=dict(value=0),wallclock='2026-11-25T'+start+'Z')]
        doc = dict(header=event,format=dict(regulation=dict(periods=2)),keyEvents=keys) if kind=='summary' else dict(
            leagues=[dict(slug='afc.champions')],events=[event])
        if edit:
            edit(doc)
        body = json.dumps(doc).encode()
        path = str(len(self.rows))+'.json'
        (self.root/path).write_bytes(body)
        when = '2026-11-25T'+observed+'Z'
        ref = dict(kind=kind,league_slug='afc.champions',event_id='99' if kind=='summary' else None,
                   url='https://site.api.espn.com/apis/site/v2/sports/soccer/afc.champions/'+kind+
                       ('?event=99' if kind=='summary' else '?dates=20261125'),
                   method='GET',http_status=200,requested_at_utc=when,captured_at_utc=when,path=path,
                   bytes=len(body),sha256=sha256(body).hexdigest())
        if parsed:
            ref['status'] = parsed
        self.rows.append((len(self.rows)+1,ref))
        return ref

    def observe(self):
        return review.inspect(self.root,self.fixtures,self.rows,self.now)[:2]

    def test_kickoff_is_typed_and_half_time_or_live_never_settles(self):
        self.capture(status='STATUS_HALFTIME',observed='12:00',parsed='STATUS_FULL_TIME')
        self.capture(status='STATUS_SECOND_HALF',observed='12:15',score=(4,3))
        self.capture(kind='scoreboard',observed='12:16')
        with patch.object(review,'_certify',side_effect=AssertionError('live row reached certification')):
            status, labels = self.observe()
        self.assertEqual(labels,{})
        self.assertEqual(status[self.mid]['kickoff']['at'],'2026-11-25T10:00:31+00:00')
        self.assertEqual(status[self.mid]['latest_by_kind']['summary']['observed_score'],[4,3])
        self.assertEqual(status[self.mid]['latest']['source']['kind'],'scoreboard')

    def test_valid_summary_passes_both_verifiers_and_same_final_is_counted_once(self):
        first = self.capture(observed='12:01')
        self.capture(observed='12:02')
        order = []
        certify, actual = review._certify, review._actual

        def step(name, fn):
            def call(*args):
                order.append(name)
                return fn(*args)
            return call
        with patch.object(review,'_certify',side_effect=step('certify',certify)), patch.object(review,'_actual',side_effect=step('actual',actual)):
            _,labels = self.observe()
        self.assertEqual(order,['certify','actual','certify','actual'])
        self.assertEqual(labels[self.mid]['evidence']['sha256'],first['sha256'])
        self.assertEqual(labels[self.mid]['home_goals'],2)
        self.assertEqual(labels[self.mid]['cohort'],'afc.champions:ELITE:league_phase')

    def test_conflicting_finals_withdrawals_and_nonregulation_remain_pending(self):
        self.capture()
        self.capture(observed='12:10',score=(1,1))
        status,labels = self.observe()
        self.assertEqual(labels,{})
        self.assertIn('conflicting certified FT90 scores',status[self.mid]['issues'])
        self.rows.pop()
        self.capture(status='STATUS_SECOND_HALF',observed='12:10')
        self.assertFalse(self.observe()[1])
        self.rows.pop()
        self.capture(observed='12:10', start='10:04:00')
        status, labels = self.observe()
        self.assertFalse(labels)
        self.assertIn('conflicting first-period kickoff wallclocks',status[self.mid]['issues'])
        for field, alter in [
            ('shootout', lambda d:d['keyEvents'].append(dict(shootout=True))),
            ('extra time', lambda d:d['header']['competitions'][0]['status'].update(period=3)),
            ('halves', lambda d:d['header']['competitions'][0]['competitors'][0]['linescores'].pop())]:
            with self.subTest(field=field):
                self.rows.clear()
                self.capture(edit=alter)
                self.assertFalse(self.observe()[1])
                self.assertTrue(self.observe()[0][self.mid]['issues'])

    def test_no_inferred_actual_kickoff_and_no_premature_final(self):
        self.capture(status='STATUS_FIRST_HALF',start=None,
                     edit=lambda d:d.update(firstPlayWallClock='2026-11-25T10:00:31Z'))
        self.assertIsNone(self.observe()[0][self.mid]['kickoff'])
        self.rows.clear()
        self.capture(observed='11:30',start='10:04:00')
        self.assertFalse(self.observe()[1])
        self.rows.clear()
        self.capture(start=None)
        self.assertIn(self.mid,self.observe()[1])

    def test_original_event_stage_league_teams_clock_and_bytes_are_bound(self):
        for alter in [lambda d:d['header'].update(id='100'),
                      lambda d:d['header']['league'].update(slug='afc.champions.2'),
                      lambda d:d['header']['season'].update(type=99),
                      lambda d:d['header']['competitions'][0].pop('groups'),
                      lambda d:d['header']['competitions'][0]['groups'].update(id='2'),
                      lambda d:d['header']['competitions'][0].update(date='2026-11-25T09:00Z'),
                      lambda d:d['header']['competitions'][0]['competitors'][0]['team'].update(id='2')]:
            self.rows.clear()
            self.capture(edit=alter)
            with self.assertRaises(ValueError):
                self.observe()
        self.rows.clear()
        r = self.capture()
        (self.root/r['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'SHA-256'):
            self.observe()
        r['path']='../outside'
        with self.assertRaisesRegex(ValueError,'outside'):
            self.observe()

    def test_cutoff_keeps_exact_acquisition_bytes_and_skips_concurrent_partial_tail(self):
        early = self.capture(status='STATUS_HALFTIME',observed='10:50')
        late = self.capture(observed='12:00')
        prefix = json.dumps(early,sort_keys=True).encode()+b'\n'
        (self.root/'refreshes.jsonl').write_bytes(prefix+json.dumps(late).encode()+b'\n'+b'{"incomplete":')
        captures,body = review.refresh_log(self.root,review.aware('2026-11-25T11:00Z'),with_bytes=True)
        self.assertEqual(body,prefix)
        self.assertEqual([r for _,r in captures],[early])
        self.assertFalse(review.inspect(self.root,self.fixtures,captures,self.now)[1])

    def test_short_and_pending_seats_and_backup_hits_do_not_become_primary_sweeps(self):
        picks = {k:dict(primary='1-0',top3=['1-0','1-1','2-0']) for k in 'abc'}
        hits = {k:(1,0) for k in picks}
        self.assertTrue(review.seats(list('abc'),picks,hits)['all_three_first_sweep'])
        self.assertIsNone(review.seats(list('ab'),picks,hits)['all_three_first_sweep'])
        self.assertIsNone(review.seats(list('abc'),picks,{'a':(1,0)})['all_three_first_sweep'])
        r = review.seats(list('abc'),picks,dict(hits,b=(1,1)))
        self.assertFalse(r['all_three_first_sweep'])
        self.assertEqual((r['first_hits'],r['top3_hits']),(2,3))


class OfficialJLeagueTests(unittest.TestCase):
    fixture = dict(match_id='espn:401877530',home='espn:131701',away='espn:3393',stage='Japanese J1 League',
                   cohort='jpn.1', kickoff_at='2026-11-25T10:00Z')
    ref = dict(code='112501',url=review.J1_URL.format('112501'),captured_at_utc='2026-11-25T10:13Z')

    def card(self, *, state='in-game', status='前半 7分', header='2026/11/25 (水) 19:04 KO',
             native='2026-11-25T10:04:00.000Z', score=(2,1), alter=None, captured=None):
        home = dict(teamId='176',code='mito',name='水戸ホーリーホック',nameS='水戸',score=score[0])
        away = dict(teamId='123',code='tokyov',name='東京ヴェルディ',nameS='東京Ｖ',score=score[1])
        g = dict(variant='game-details', tournament='j1',tournamentName='明治安田Ｊ１リーグ',section='第16節',
                 type=state,date='$D'+native,homeTeam=home,awayTeam=away)
        if state=='in-game':
            g['liveMatchInfo'] = dict(periodOfTime='first-half',passedMinutes=7)
        if alter:
            alter(g)
        doc = ('<link rel="canonical" href="{url}"><meta property="og:url" content="{url}">'
               '<meta property="og:title" content="【公式】水戸 vs 東京Ｖの試合結果・データ（明治安田Ｊ１リーグ：2026年11月25日）">'
               '<div class="p-match-details-skeleton"><span>試合終了</span></div>'
               '<div class="o-page-header--game-details o-page-header--game-details--{state}">'
               '<p class="o-page-header__date">{header}</p><p class="o-page-header__section">第16節</p>'
               '<div class="o-page-header__club--home"><p class="o-page-header__club-name">水戸ホーリーホック</p></div>'
               '<div class="o-page-header__club--away"><p class="o-page-header__club-name">東京ヴェルディ</p></div>'
               '<div class="o-page-header__match-info"><b class="o-page-header__match-score">{h}</b>'
               '<b class="o-page-header__match-score">{a}</b><p>{status}</p></div></div>'
               '<script>self.__next_f.push({flight})</script>').format(
                   url=self.ref['url'],state=state,header=header,status=status,h=score[0],a=score[1],
                   flight=json.dumps([1,'2:'+json.dumps(g,ensure_ascii=False)+'\n'],ensure_ascii=False))
        ref = dict(self.ref,captured_at_utc=captured or self.ref['captured_at_utc'])
        return review.official_card(doc.encode(),ref,self.fixture)

    def test_jst_original_label_half_open_and_issue_must_precede_every_possible_instant(self):
        p = self.card()
        iv = p['ko_interval']
        self.assertTrue(p['ko_eligible'])
        self.assertEqual((iv['start_inclusive'],iv['end_exclusive']),('2026-11-25T10:04+00:00','2026-11-25T10:05+00:00'))
        self.assertEqual(iv['start_jst'],'2026-11-25T19:04+09:00')
        for stamp,expect in [('10:03:59.999999','before_entire_interval'),('10:04:00','inside_interval_unproved'),
                             ('10:04:59.999999','inside_interval_unproved'),('10:05:00','at_or_after_interval')]:
            self.assertEqual(review.before_official_minute('2026-11-25T'+stamp+'Z',iv),expect)

    def test_pregame_native_time_alone_within_minute_and_disagreeing_native_are_references(self):
        for p in [self.card(state='pre-game',status='KICK OFF',header='2026/11/25 (水)'),
                  self.card(state='pre-game',status='KICK OFF'),self.card(native='2026-11-25T10:05:00Z'),
                  self.card(captured='2026-11-25T10:04:59Z')]:
            self.assertFalse(p['ko_eligible'])
            self.assertIsNone(review.official_crosscheck([p],{},None)['kickoff_interval'])
        p = self.card(header='2026/11/25 (水) 19:00 KO',native='2026-11-25T10:00:00Z')
        self.assertIsNone(review.official_crosscheck([p],{},None)['kickoff_interval'])
        self.assertIsNotNone(review.official_crosscheck([p],{'kickoff':{'at':'2026-11-25T10:00:30Z'}},None)['kickoff_interval'])

    def test_identity_calendar_round_canonical_and_consistency(self):
        for change in [lambda g:g['homeTeam'].update(teamId='123'),lambda g:g.update(tournament='j2'),
                       lambda g:g.update(section='第17節'),lambda g:g['awayTeam'].update(name='水戸ホーリーホック')]:
            with self.assertRaises(ValueError):
                self.card(alter=change)
        for bad in ('2026/11/25 (木) 19:04 KO','2026/11/26 (木) 19:04 KO'):
            with self.assertRaises(ValueError):
                self.card(header=bad)
        with patch.dict(self.ref,{'url':'https://www.jleague.jp.invalid/match/j1/2026/112501/'}), self.assertRaises(ValueError):
            self.card()

    def test_independent_final_agreement_conflict_stale_live_and_regulation(self):
        p = self.card()
        final = self.card(state='post-game',status='試合終了',captured='2026-11-25T12:00Z')
        label = dict(home_goals=2,away_goals=1,observed_at='2026-11-25T11:58Z')
        check = review.official_crosscheck
        self.assertEqual(check([p],{},label)['verdict'],'espn_ft90_only')
        self.assertEqual(check([p,final],{},label)['verdict'],'official_and_espn_ft_agree')
        self.assertEqual(check([final],{},None)['verdict'],'awaiting_certified_espn_ft90')
        self.assertEqual(check([],{},label)['verdict'],'espn_ft90_only')
        self.assertEqual(check([p,final],{},dict(label,home_goals=3))['verdict'],'pending_conflict')
        self.assertEqual(check([p],{},dict(label,observed_at='2026-11-25T11:34:59.999999Z'))['verdict'],'pending_conflict')
        self.assertEqual(check([p],{},dict(label,observed_at='2026-11-25T11:35:00Z'))['verdict'],'espn_ft90_only')
        late = self.card(status='ハーフタイム',captured='2026-11-25T12:01Z')
        self.assertEqual(check([late],{},label)['verdict'],'pending_conflict')
        self.assertEqual(check([final,late],{},label)['verdict'],'pending_conflict')
        self.assertEqual(check([self.card(state='post-game',status='PK戦')],{},label)['verdict'],'pending_conflict')
        self.assertEqual(check([p],dict(kickoff=dict(at='2026-11-25T10:05Z')),label)['verdict'],'pending_conflict')
        self.assertEqual(check([p,self.card(header='2026/11/25 (水) 19:05 KO', native='2026-11-25T10:05Z')],{},label)['verdict'],'pending_conflict')
