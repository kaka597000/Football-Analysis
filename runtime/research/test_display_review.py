import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research import display_review as d
from research.analysis_freeze import save, digest
from automation.collector import collect
from publication import publish

TRACK = 'comprehensive'


class FrozenClock(datetime):
    current = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


class OfflinePublisher:
    """Durable stand-in for a channel's own first-delivered message store."""

    def __init__(self, root):
        self.root = root
        self.sends = 0

    def lookup_first(self, slot):
        path = self.root / (slot + '.json')
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    def send_first(self, request):
        self.sends += 1
        now = FrozenClock.now(timezone.utc)
        if now >= datetime.fromisoformat(request['deadline']):
            raise ValueError('synthetic publisher deadline passed')
        self.root.mkdir(exist_ok=True)
        slot = request['slot']
        receipt = {'status': 'DELIVERED', 'message_id': 'offline-' + slot[:12], 'delivered_at': now.isoformat(),
                   'request_sha256': digest(request), 'text': request['text']}
        try:
            with (self.root / (slot + '.json')).open('x', encoding='utf-8') as f:
                json.dump(receipt, f, ensure_ascii=False)
        except FileExistsError:
            pass


class SharedDisplayTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(FrozenClock, 'current', FrozenClock.current))
        self.enterContext(patch('research.analysis_freeze.datetime', FrozenClock))
        self.enterContext(patch('publication.datetime', FrozenClock))

    def fixture(self,league='bra.1',provider='espn'):
        return {'match_id':provider+':101','cohort':league,'stage':'regular','home':provider+':1','away':provider+':2','kickoff_at':'2026-01-02T12:00:00Z','cutoff_at':'2026-01-01T11:00:00Z','provider_competition':'PL','provider_stage':'REGULAR_SEASON'}

    def payload(self, first='1-0', alts=('1-1', '2-0')):
        return d.attach_display({'synthetic_test_only': True, 'primary': first}, first, alts,
                                abstain=first is None)[0]

    def release(self, root, fixture, publisher, *, payload=None, resume=False, name='report'):
        root = Path(root)
        return publish(None if resume else (payload or self.payload()), None if resume else fixture,
                       out=root / (name + '.json'), report=root / (name + '.md'),
                       freeze_root=root / 'freezes', track=TRACK, publisher=publisher, resume=resume)

    def record(self, result):
        return json.loads(Path(result['freeze_receipt']['path']).read_text(encoding='utf-8'))

    def settle(self, record, fixture, publisher=None, provider=None, meta=None, root=None, **kwargs):
        kwargs.setdefault('lookup_first', publisher.lookup_first if publisher is not None else None)
        # This fixture's actual start is fixed by the synthetic test, even when
        # it differs from the frozen schedule in the kickoff boundary tests.
        kwargs.setdefault('lookup_actual_kickoff', lambda f: f['kickoff_at'])
        return d.settle(record, fixture, TRACK, provider, meta, root, **kwargs)

    def raw(self,f,h,a,provider='espn'):
        if provider=='football-data.org':return {'id':101,'homeTeam':{'id':1},'awayTeam':{'id':2},'competition':{'code':'PL'},'stage':'REGULAR_SEASON','utcDate':f['kickoff_at'],'status':'FINISHED','score':{'duration':'REGULAR','fullTime':{'home':h,'away':a}}}
        c={'date':f['kickoff_at'],'status':{'type':{'completed':True,'name':'STATUS_FULL_TIME'},'period':2},'competitors':[{'homeAway':s,'team':{'id':str(i)},'score':str(g),'linescores':[{'value':g},{'value':0}]} for s,i,g in [('home',1,h),('away',2,a)]]}
        return {'header':{'id':'101','league':{'slug':f['cohort']},'season':{'year':2026,'name':'Regular Season'},'competitions':[c]},'format':{'regulation':{'periods':2}}}
    def meta(self,root,f,raw,provider='espn'):
        p=Path(root)/'result.json';p.write_text(json.dumps(raw));return {'path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'captured_at':'2026-01-03T12:00:00Z','source_url':('https://api.football-data.org/v4/matches/101' if provider=='football-data.org' else f"https://site.api.espn.com/apis/site/v2/sports/soccer/{f['cohort']}/summary?event=101")}
    def test_actual_publication_and_settlement_across_leagues_and_cup(self):
        fixtures = [('eng.1', 'regular', 'football-data.org', (1, 0), (True, True)),
                    ('usa.1', 'regular', 'espn', (1, 1), (False, True)),
                    ('uefa.europa', 'league-phase', 'espn', (4, 0), (False, False)),
                    ('fifa.world', 'group-stage', 'espn', (2, 0), (False, True))]
        for league, stage, provider, score, hits in fixtures:
            with self.subTest(league=league), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                f = dict(self.fixture(league, 'fdorg' if provider == 'football-data.org' else 'espn'), stage=stage)
                sender = OfflinePublisher(root / 'channel')
                payload = dict(self.payload(), matrix_top3=['4-0', '7-0', '7-1'])
                issued = self.release(root, f, sender, payload=payload)
                record = self.record(issued)
                receipt = issued['display_delivery']['confirmation']
                self.assertEqual(issued['publication_status'], 'DELIVERED')
                self.assertEqual((root / 'report.md').read_text(encoding='utf-8'), receipt['text'])
                self.assertIn(record['payload']['displayed_scores']['text'], receipt['text'])
                raw = self.raw(f, *score, provider)
                meta = self.meta(root, f, raw, provider)
                if stage in ('league-phase', 'group-stage'):
                    _, meta['scoreboard_reference'] = self.board_meta(root, f, raw, stage)
                review = self.settle(record, f, sender, provider, meta, root)
                self.assertEqual((review['top1_hit'], review['top3_hit']), hits)
                self.assertEqual(review['message_id'], receipt['message_id'])
                summary = d.summarize([review])
                self.assertEqual((summary['top1']['settled_n'], summary['top3']['settled_n']), (1, 1))

    def test_empty_incomplete_duplicate_and_abstention(self):
        for first,alts in [(None,()),('1-0',('1-1',)),('1-0',('1-0','2-0')),('1-0',('x','2-0'))]:
            with self.assertRaises(ValueError):d.attach_display({},first,alts)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture('eng.1')
            sender = OfflinePublisher(root / 'channel')
            released = self.release(root, fixture, sender, payload=self.payload(None, ()))
            record = self.record(released)
            with self.assertRaisesRegex(FileExistsError, 'already confirmed'):
                self.release(root, fixture, sender, name='replacement')
            summary = d.summarize([self.settle(record, fixture, sender)])
            self.assertEqual((summary['registered_n'], summary['abstain_n'], summary['top1']['settled_n']), (1, 1, 0))
            self.assertIsNone(summary['top3']['accuracy'])
            self.assertEqual(sender.sends, 1)
            self.assertEqual(len(list((root / 'freezes').glob('*/*.json'))), 1)

    def test_no_backfill_no_cross_track_no_matrix_borrowing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = self.fixture()
            sender = OfflinePublisher(root / 'channel')
            r = self.record(self.release(root, f, sender))
            with self.assertRaises(ValueError):
                d.settle(r, f, 'current-test-pair', lookup_first=sender.lookup_first)
            for mismatch in (dict(f, match_id='espn:OTHER'), dict(f, provider_competition='OTHER')):
                with self.assertRaises(ValueError):
                    self.settle(r, mismatch, sender)
            for field, value in [('track', 'current-test-pair'), ('revision', 2),
                                 ('recorded_at', '2026-01-03T00:00:00Z')]:
                bad = dict(r, **{field: value})
                bad['record_sha256'] = digest({k: v for k, v in bad.items() if k != 'record_sha256'})
                with self.subTest(field=field), self.assertRaises(ValueError):
                    self.settle(bad, f, sender)
            legacy = {'primary': '1-0', 'compared_scores': ['1-0', '1-1', '2-0'],
                      'matrix_top3': ['1-0', '1-1', '2-0']}
            old = self.record({'freeze_receipt': save(legacy, f, stage='prematch', track=TRACK,
                                                      root=root / 'freezes')})
            self.assertFalse(self.settle(old, f, sender)['top3_issued'])

    def test_hidden_abstain_does_not_take_first_display(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = self.fixture('eng.1')
            hidden = self.release(root, f, None, payload=self.payload(None, ()), name='hidden')
            self.assertEqual(hidden['publication_status'], 'UNCONFIRMED')
            self.assertFalse((root / 'hidden.md').exists())
            old = Path(hidden['freeze_receipt']['path'])
            original = old.read_bytes()
            sender = OfflinePublisher(root / 'channel')
            record = self.record(self.release(root, f, sender))
            self.assertEqual(record['revision'], 2)
            self.assertEqual(record['previous_sha256'], self.record(hidden)['record_sha256'])
            self.assertEqual(old.read_bytes(), original)
            self.assertTrue(self.settle(record, f, sender)['top3_issued'])
            with self.assertRaisesRegex(ValueError, 'does not match the displayed freeze'):
                self.settle(self.record(hidden), f, sender)

    def test_freeze_or_local_output_alone_cannot_settle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture('eng.1')
            sender = OfflinePublisher(root / 'channel')
            issued = self.release(root, fixture, sender)
            record = self.record(issued)
            meta = self.meta(root, fixture, self.raw(fixture, 1, 0))
            self.assertTrue((root / 'report.json.display-receipt.json').exists())
            row = self.settle(record, fixture, None, 'espn', meta, root)
            self.assertTrue(row['verified_completed'])
            self.assertEqual(row['display_issue'], 'UNCONFIRMED_DELIVERY')
            self.assertIsNone(row['top1_hit'])
            self.assertIsNone(row['top3_hit'])
            self.assertEqual(d.summarize([row])['top1']['settled_n'], 0)

    def test_output_text_and_channel_confirmation_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture('eng.1')
            sender = OfflinePublisher(root / 'channel')
            bad = self.payload()
            bad['displayed_scores']['alternatives'].reverse()
            with self.assertRaisesRegex(ValueError, 'text differs'):
                self.release(root, fixture, sender, payload=bad)
            self.assertEqual(sender.sends, 0)
            self.assertFalse((root / 'freezes').exists())

            def changed_message(request):
                sender.root.mkdir()
                # Simulate a channel that confirms the request ID but shows altered text.
                receipt = dict(status='DELIVERED', message_id='changed-body', request_sha256=digest(request),
                               delivered_at=FrozenClock.current.isoformat(), text='备选已重新排序')
                path = sender.root / (request['slot'] + '.json')
                path.write_text(json.dumps(receipt), encoding='utf-8')

            with patch.object(sender, 'send_first', side_effect=changed_message):
                with self.assertRaisesRegex(ValueError, 'does not match the displayed freeze'):
                    self.release(root, fixture, sender)
            self.assertFalse((root / 'report.md').exists())
            self.assertFalse((root / 'report.json.display-receipt.json').exists())
            journal = json.loads((root / 'report.json.publication.json').read_text(encoding='utf-8'))
            self.assertEqual(journal['status'], 'FROZEN')

    def test_confirmation_and_actual_kickoff_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = self.fixture('eng.1')
            sender = OfflinePublisher(root / 'channel')
            published = self.release(root, f, sender)
            record = self.record(published)
            confirmation = published['display_delivery']['confirmation']
            with patch.object(sender, 'lookup_first', return_value=dict(confirmation, status='QUEUED')):
                with self.assertRaisesRegex(ValueError, 'does not match the displayed freeze'):
                    self.settle(record, f, sender)
            actual_before_send = '2026-01-01T11:59:59Z'
            for actual, issue in [(None, 'UNVERIFIED_ACTUAL_KICKOFF'),
                                  (confirmation['delivered_at'], 'DELIVERED_AT_OR_AFTER_KICKOFF'),
                                  (actual_before_send, 'DELIVERED_AT_OR_AFTER_KICKOFF')]:
                with self.subTest(actual=actual):
                    row = self.settle(record, f, sender, lookup_actual_kickoff=lambda _: actual)
                    self.assertEqual(row['display_issue'], issue)
                    self.assertFalse(row['top1_issued'])
            for late in (f['kickoff_at'], '2026-01-02T12:00:01Z'):
                with self.subTest(late=late), patch.object(
                    sender, 'lookup_first', return_value=dict(confirmation, delivered_at=late)
                ), self.assertRaisesRegex(ValueError, 'before the frozen kickoff'):
                    self.settle(record, f, sender)

    def test_no_confirmed_delivery_or_failed_send_after_kickoff_stays_pending(self):
        for failure in (None, OSError('synthetic send failed')):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                f = self.fixture('eng.1')
                sender = OfflinePublisher(root / 'channel')
                FrozenClock.current = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
                with patch.object(sender, 'send_first', side_effect=failure):
                    if failure:
                        with self.assertRaises(OSError):
                            self.release(root, f, sender)
                    else:
                        self.assertEqual(self.release(root, f, sender)['publication_status'], 'UNCONFIRMED')
                saved = next((root / 'freezes').glob('*/*.json'))
                original = saved.read_bytes()
                FrozenClock.current = datetime.fromisoformat(f['kickoff_at'])
                restarted = OfflinePublisher(root / 'channel')
                pending = self.release(root, f, restarted, resume=True)
                self.assertEqual(pending['publication_status'], 'UNCONFIRMED')
                self.assertEqual(restarted.sends, 0)
                self.assertFalse((root / 'report.json').exists())
                self.assertFalse((root / 'report.md').exists())
                self.assertFalse((root / 'report.json.display-receipt.json').exists())
                self.assertEqual(saved.read_bytes(), original)

    def test_restart_after_confirmation_preserves_the_original_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = self.fixture('eng.1')
            sender = OfflinePublisher(root / 'channel')
            original_send = sender.send_first

            def interrupt_after_confirmation(request):
                original_send(request)
                raise OSError('lost the channel reply after delivery')

            with patch.object(sender, 'send_first', side_effect=interrupt_after_confirmation):
                with self.assertRaises(OSError):
                    self.release(root, f, sender)
            authoritative = next(sender.root.glob('*.json')).read_bytes()
            FrozenClock.current = datetime.fromisoformat('2026-01-02T12:00:01Z')
            restarted = OfflinePublisher(root / 'channel')
            recovered = self.release(root, f, restarted, resume=True)
            self.assertEqual(recovered['publication_status'], 'DELIVERED')
            self.assertEqual(recovered['display_delivery']['confirmation'], json.loads(authoritative))
            local = root / 'report.json.display-receipt.json'
            event = local.read_bytes()
            again = self.release(root, f, restarted, resume=True)
            self.assertEqual(again, recovered)
            self.assertEqual(local.read_bytes(), event)
            self.assertEqual(restarted.sends, 0)
            self.assertEqual(len(list((root / 'freezes').glob('*/*.json'))), 1)
            self.assertFalse((root / 'report.json').exists())
            self.assertFalse((root / 'report.md').exists())

    def test_delivered_target_loss_never_reissues_after_kickoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = self.fixture('eng.1')
            sender = OfflinePublisher(root / 'channel')
            published = self.release(root, f, sender)
            out, report = root / 'report.json', root / 'report.md'
            out.unlink()
            report.unlink()
            restarted = OfflinePublisher(root / 'channel')
            self.assertEqual(self.release(root, f, restarted, resume=True), published)
            self.assertFalse(out.exists())
            self.assertFalse(report.exists())
            FrozenClock.current = datetime.fromisoformat(f['kickoff_at'])
            local = root / 'report.json.display-receipt.json'
            original_event = local.read_bytes()
            self.assertEqual(self.release(root, f, restarted, resume=True), published)
            with patch.object(restarted, 'lookup_first', return_value=None):
                pending = self.release(root, f, restarted, resume=True)
                self.assertEqual(pending['publication_status'], 'UNCONFIRMED')
            self.assertEqual(local.read_bytes(), original_event)
            self.assertEqual(restarted.sends, 0)
            self.assertFalse(out.exists())
            self.assertFalse(report.exists())

    def test_prematch_retry_reuses_the_freeze_and_can_confirm_the_first_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = self.fixture('eng.1')
            sender = OfflinePublisher(root / 'channel')
            pending = self.release(root, f, None)
            released = self.release(root, f, sender, resume=True)
            self.assertEqual(released['publication_status'], 'DELIVERED')
            self.assertEqual(pending['freeze_receipt'], released['freeze_receipt'])
            self.assertEqual(self.release(root, f, sender, resume=True), released)
            self.assertEqual(sender.sends, 1)
            self.assertTrue((root / 'report.md').exists())
    def test_shared_end_to_end_collection_no_markets_and_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            f=self.fixture('usa.1');publisher=OfflinePublisher(Path(root)/'channel');r=self.record(self.release(root,f,publisher));raw=self.raw(f,1,1);event={**raw['header'],'date':f['kickoff_at']};calls=[]
            def fetch(url,headers=None):
                calls.append(url)
                self.assertIn('site.api.espn.com',url)
                return raw if '/summary?' in url else {'leagues':[{'slug':f['cohort']}],'events':[event]}
            scopes=[{'competition':'usa.1','cohort':'usa.1','stages':['regular']}]
            first=collect(root,scopes,now=datetime(2026,1,3,tzinfo=timezone.utc),lookback_days=0,fetcher=fetch,include_market=False)
            self.assertEqual(len(first['new_rows']),1);meta=first['new_rows'][0]['raw_reference'];self.assertIn('scoreboard_reference',meta);s=d.summarize([self.settle(r,f,publisher,'espn',meta,root)])
            self.assertEqual(s['top3']['hits'],1)
            before=(Path(root)/'normalized-results.jsonl').read_bytes();second=collect(root,scopes,now=datetime(2026,1,3,tzinfo=timezone.utc),lookback_days=0,fetcher=fetch,include_market=False)
            self.assertEqual(len(second['new_rows']),0);self.assertEqual(before,(Path(root)/'normalized-results.jsonl').read_bytes());self.assertFalse(first['market_requested'])
    def test_results_only_never_enters_training(self):
        from automation.run import execute
        with self.assertRaises(ValueError):execute('/tmp/not-used','train',results_only=True)

    def test_wrong_source_extra_time_late_data_and_tampering(self):
        with tempfile.TemporaryDirectory() as root:
            f=self.fixture('eng.1','fdorg');raw=self.raw(f,1,0,'football-data.org');m=self.meta(root,f,raw,'football-data.org');raw['score']['duration']='EXTRA_TIME';m=self.meta(root,f,raw,'football-data.org')
            with self.assertRaises(ValueError):d.certify_retained('football-data.org',f,m,root)
            m['sha256']='0'*64
            with self.assertRaises(ValueError):d.certify_retained('football-data.org',f,m,root)
            f=self.fixture('usa.1');raw=self.raw(f,1,0);raw['header']['season']['name']='Playoffs';m=self.meta(root,f,raw)
            with self.assertRaises(ValueError):d.certify_retained('espn',f,m,root)
            f=self.fixture();r=self.record(self.release(root,f,None));r['payload']['displayed_scores']['first']='9-9'
            with self.assertRaises(ValueError):self.settle(r,f)

    def test_finished_extra_time_and_shootout_use_explicit_regular_time(self):
        for duration in ('EXTRA_TIME','PENALTY_SHOOTOUT'):
            for keys in [('home','away'),('homeTeam','awayTeam')]:
                with self.subTest(duration=duration,keys=keys),tempfile.TemporaryDirectory() as root:
                    f=self.fixture('eng.1','fdorg');raw=self.raw(f,7,6,'football-data.org')
                    raw['score'].update(duration=duration,regularTime=dict(zip(keys,[1,1])),extraTime={'home':1,'away':0},penalties={'home':5,'away':5})
                    m=self.meta(root,f,raw,'football-data.org');r=d.certify_retained('football-data.org',f,m,root)
                    self.assertEqual((r['home_goals'],r['away_goals']),(1,1))

    def test_regular_time_missing_invalid_conflicting_or_unfinished_rejected(self):
        cases=[None,{}, {'home':None,'away':1},{'home':True,'away':1},{'home':-1,'away':1},{'home':1,'away':1,'homeTeam':2,'awayTeam':1}]
        for value in cases:
            with self.subTest(value=value),tempfile.TemporaryDirectory() as root:
                f=self.fixture('eng.1','fdorg');raw=self.raw(f,7,6,'football-data.org');raw['score'].update(duration='PENALTY_SHOOTOUT',regularTime=value);m=self.meta(root,f,raw,'football-data.org')
                with self.assertRaises(ValueError):d.certify_retained('football-data.org',f,m,root)
        with tempfile.TemporaryDirectory() as root:
            f=self.fixture('eng.1','fdorg');raw=self.raw(f,1,1,'football-data.org');raw['score']['regularTime']={'home':None,'away':None};m=self.meta(root,f,raw,'football-data.org')
            self.assertEqual(d.certify_retained('football-data.org',f,m,root)['home_goals'],1)
            raw['score']['regularTime']={'home':1,'away':0};m=self.meta(root,f,raw,'football-data.org')
            with self.assertRaises(ValueError):d.certify_retained('football-data.org',f,m,root)
            raw['score'].update(duration='EXTRA_TIME',regularTime={'home':1,'away':1});raw['status']='IN_PLAY';m=self.meta(root,f,raw,'football-data.org')
            with self.assertRaises(ValueError):d.certify_retained('football-data.org',f,m,root)
            raw['status']='FINISHED';raw['homeTeam']['id']=999;m=self.meta(root,f,raw,'football-data.org')
            with self.assertRaises(ValueError):d.certify_retained('football-data.org',f,m,root)

    def board_meta(self,root,f,raw,stage):
        event=copy.deepcopy(raw['header']);event['date']=f['kickoff_at'];event['season']['slug']=stage
        board={'leagues':[{'slug':f['cohort']}],'events':[event]}
        p=Path(root)/'scoreboard.json';p.write_text(json.dumps(board))
        meta={'path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'captured_at':'2026-01-03T11:00:00Z','source_url':f"https://site.api.espn.com/apis/site/v2/sports/soccer/{f['cohort']}/scoreboard?dates=20260102"}
        return board,meta

    def test_paired_native_stage_survives_missing_summary_slug(self):
        for league,stage in [('uefa.europa','league-phase'),('uefa.europa.conf','league-phase'),('fifa.world','group-stage')]:
            with self.subTest(league=league),tempfile.TemporaryDirectory() as root:
                f=dict(self.fixture(league),stage=stage);raw=self.raw(f,1,1);m=self.meta(root,f,raw)
                with self.assertRaises(ValueError):d.certify_retained('espn',f,m,root)
                board,bm=self.board_meta(root,f,raw,stage);m['scoreboard_reference']=bm
                self.assertEqual(d.certify_retained('espn',f,m,root)['home_goals'],1)

    def test_paired_stage_missing_conflict_wrong_fixture_and_root_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            f=dict(self.fixture('uefa.europa'),stage='league-phase');raw=self.raw(f,1,1);m=self.meta(root,f,raw);board,bm=self.board_meta(root,f,raw,'league-phase');m['scoreboard_reference']=bm
            def check_bad(changed):
                p=Path(root)/'scoreboard.json';p.write_text(json.dumps(changed));bm['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
                with self.assertRaises(ValueError):d.certify_retained('espn',f,m,root)
            bad=copy.deepcopy(board);del bad['events'][0]['season']['slug'];check_bad(bad)
            bad=copy.deepcopy(board);bad['events'][0]['season']['slug']='final';check_bad(bad)
            bad=copy.deepcopy(board);bad['leagues'][0]['slug']='fifa.world';check_bad(bad)
            bad=copy.deepcopy(board);bad['events'][0]['id']='999';check_bad(bad)
            bad=copy.deepcopy(board);bad['events'][0]['competitions'][0]['competitors'][0]['team']['id']='999';check_bad(bad)
            bad=copy.deepcopy(board);bad['events'][0]['date']='2026-01-02T14:00:00Z';check_bad(bad)
            bad=copy.deepcopy(board);bad['events']*=2;check_bad(bad)
            board,bm=self.board_meta(root,f,raw,'league-phase');m['scoreboard_reference']=bm;raw['header']['season']['slug']='final';m.update(self.meta(root,f,raw))
            with self.assertRaises(ValueError):d.certify_retained('espn',f,m,root)
            bm['path']='../outside.json'
            with self.assertRaisesRegex(ValueError,'outside retained root'):d.certify_retained('espn',f,m,root)

if __name__=='__main__':unittest.main()
