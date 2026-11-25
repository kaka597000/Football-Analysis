from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from research_program import lineup_cutoff_forward as f
from research_program import test_lineup_forward as r15_tests
from research_program import test_nov25_first_review as review_tests
from research_program.test_lineup_study import encoded, lines, pack


def archive_files(path):
    with tarfile.open(path) as archive:
        return {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}


def write_capture(folder, path, body, **kwargs):
    target = folder / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return dict(path=path, http_status=200, bytes=len(body), sha256=sha256(body).hexdigest(), **kwargs)


def roster_group(side, team, *, count=11):
    names = dict(G='Goalkeeper', D='Defender', M='Midfielder', F='Forward')
    roles = [(names[role], role) for role in 'GDDDDMMMFFF']
    return dict(homeAway=side, team=dict(id=team), roster=[dict(starter=i < count, athlete=dict(id=team + str(i)),
                position=dict(displayName=name, abbreviation=abbr)) for i, (name, abbr) in enumerate(roles)])


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.mid = 'espn:401877530'
        self.fix = dict(match_id=self.mid, cohort=f.CODE, stage='Japanese J1 League', home='espn:131701', away='espn:3393',
                        kickoff_at='2026-11-25T10:00Z')
        self.early = {self.mid: self.fix}
        self.stamp = '2026-11-25T12:00Z'
        self.at = f.aware('2026-11-25T12:05Z')
        self.roles = [roster_group(s, self.fix[s].split(':')[1]) for s in f.SIDES]
        self.espn_rows, self.official_rows = [], []

    def event(self, status='STATUS_FULL_TIME', score=(2, 1)):
        full = status == 'STATUS_FULL_TIME'
        phase, slug, name, _ = f.review.STAGES[f.CODE]
        sides = [dict(homeAway=s, team=dict(id=self.fix[s].split(':')[1], displayName=s), score=str(n),
                      linescores=[dict(displayValue='0'), dict(displayValue=str(n))]) for s, n in zip(f.SIDES, score)]
        game = dict(id=self.mid.split(':')[1], date=self.fix['kickoff_at'], status=dict(period=2, type=dict(
                    name=status, completed=full, state='post' if full else 'in')), competitors=sides)
        return dict(id=game['id'], date=game['date'], league=dict(slug=f.CODE),
                    season=dict(year=2026, type=phase, slug=slug, name=name), competitions=[game])

    def summary(self, stamp=None, status='STATUS_FULL_TIME', score=(2, 1), roles=None, mutate=None):
        event = self.event(status, score)
        body = dict(header=event, format=dict(regulation=dict(periods=2)), rosters=self.roles if roles is None else roles)
        if mutate:
            mutate(body)
        stamp = stamp or self.stamp
        ref = write_capture(self.root, f'raw/test-summary-{len(self.espn_rows)}.json', encoded(body), method='GET',
                            kind='summary', league_slug=f.CODE, event_id=event['id'], requested_at_utc=stamp,
                            captured_at_utc=stamp, url='https://site.api.espn.com/apis/site/v2/sports/soccer/jpn.1/summary?event=' + event['id'])
        self.espn_rows.append(ref)
        (self.root / 'refreshes.jsonl').write_bytes(lines(self.espn_rows))
        return ref

    def card(self, stamp=None, *, final=True, score=(2, 1), kind='match-card-refresh', live_text='後半 8分'):
        stamp = stamp or self.stamp
        helper = review_tests.OfficialJLeagueTests()
        helper.setUp()
        with patch.object(f.review, 'official_card', side_effect=lambda b, *_: b):
            body = helper.card(state='post-game' if final else 'in-game', status='試合終了' if final else live_text, score=score)
        clock = f.aware(stamp).strftime('%Y%m%dT%H%M%S%fZ')
        prefix = 'FT-refresh' if kind == 'match-card-FT' else 'refresh'
        ref = write_capture(self.root, f'raw/jleague-official/match-112501-{prefix}-{clock}.html', body,
                            kind=kind, event_id='401877530', jleague_match_code='112501',
                            url=f.review.J1_URL.format('112501'), requested_at_utc=stamp, captured_at_utc=stamp)
        self.official_rows.append(ref)
        (self.root / f.OFFICIAL_LOGS[1]).write_bytes(lines(self.official_rows))
        return ref

    def observe(self):
        with patch.object(f, '_calendar', return_value={'counts': {}}):
            return f._evidence(self.root, self.root, self.early, self.at)

    def test_both_native_finals_required_and_missing_xi_keeps_the_fixture(self):
        self.roles[1]['roster'][0]['starter'] = False
        self.summary(status='STATUS_HALFTIME')
        self.card(final=True)
        report, _ = self.observe()
        self.assertEqual(report['pending'], [self.mid])
        raw = self.summary(stamp='2026-11-25T12:01Z')
        report, rosters = self.observe()
        self.assertEqual((report['dual_ft90'], report['pending']), (1, []))
        row = report['events'][self.mid]
        self.assertEqual(row['score'], [2, 1])
        self.assertEqual([s['sha256'] for s in row['ft_summary_sources']], [raw['sha256']])
        self.assertEqual(row['official']['sha256'], self.official_rows[0]['sha256'])
        self.assertEqual(f.aware(row['ft_pair_observed_at_utc']), f.aware(raw['captured_at_utc']))
        self.assertEqual(len(rosters[self.mid]['home_starters']), 11)
        self.assertEqual({p['position'] for p in rosters[self.mid]['home_starters']}, set('GDMF'))
        self.assertIsNone(rosters[self.mid]['away_starters'])
        self.assertEqual(row['roster_coverage']['away']['checks'][0]['explicit_starters'], 10)
        (self.root / raw['path']).write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'SHA'):
            self.observe()

    def test_ft_refresh_after_espn_waits_for_the_official_rendered_final(self):
        espn = self.summary()
        self.card('2026-11-25T12:01Z', kind='match-card-FT', final=False, live_text='後半終了')
        official = self.card('2026-11-25T12:02Z', kind='match-card-FT')
        self.summary(stamp='2026-11-25T12:03Z')
        self.at = f.aware('2026-11-25T12:01:59Z')
        report, rosters = self.observe()
        row = report['events'][self.mid]
        self.assertEqual((row['espn_status'], row['official_status']), ('STATUS_FULL_TIME', 'live'))
        self.assertEqual((report['dual_ft90'], report['pending'], rosters), (0, [self.mid], {}))
        self.at = f.aware('2026-11-25T12:03Z')
        report, _ = self.observe()
        row = report['events'][self.mid]
        self.assertEqual((row['verdict'], row['score']), (f.AGREED, [2, 1]))
        self.assertEqual((row['official']['sha256'], row['espn']['sha256']), (official['sha256'], espn['sha256']))
        self.assertEqual(f.aware(row['ft_pair_observed_at_utc']), f.aware(official['captured_at_utc']))
        self.assertEqual(f.aware(row['history_observed_at_utc']), self.at)

    def test_ft_refresh_kind_path_and_card_bytes_stay_bound(self):
        self.summary()
        for kind, other in [('match-card-FT', 'match-card-refresh'), ('match-card-refresh', 'match-card-FT')]:
            with self.subTest(kind=kind):
                self.official_rows.clear()
                ref = self.card(kind=kind)
                log = self.root / f.OFFICIAL_LOGS[1]
                log.write_bytes(lines([dict(ref, kind=other)]))
                with self.assertRaisesRegex(ValueError, 'raw match card path'):
                    self.observe()
                log.write_bytes(lines([dict(ref, match_code='112502')]))
                with self.assertRaisesRegex(ValueError, 'match card code'):
                    self.observe()
                log.write_bytes(lines([ref]))
                (self.root / ref['path']).write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'SHA'):
                    self.observe()

    def test_later_conflict_withdrawal_and_future_captures(self):
        self.summary()
        self.card('2026-11-25T12:30Z')
        report, _ = self.observe()
        self.assertEqual(report['events'][self.mid]['verdict'], 'espn_ft90_only')
        self.card('2026-11-25T12:02Z', score=(0, 0))
        report, rosters = self.observe()
        self.assertFalse(rosters)
        self.assertEqual(report['events'][self.mid]['verdict'], 'pending_conflict')
        self.official_rows.clear()
        self.card()
        self.card('2026-11-25T12:02Z', final=False)
        self.assertEqual(self.observe()[0]['pending'], [self.mid])

    def test_extra_period_or_wrong_teams_cannot_enter_history(self):
        self.card()
        self.summary(mutate=lambda d: d['header']['competitions'][0]['status'].update(period=3))
        self.assertEqual(self.observe()[0]['pending'], [self.mid])
        self.summary(mutate=lambda d: d['header']['competitions'][0]['competitors'][0]['team'].update(id='3393'))
        with self.assertRaisesRegex(ValueError, 'side mismatch'):
            self.observe()

    def test_independent_explicit_eleven_conflicts_and_wrong_side(self):
        for change in ('wrong_team', 'duplicate_id', 'unmapped_role', 'ambiguous_side', 'starter_string'):
            with self.subTest(change=change):
                groups = deepcopy(self.roles)
                row = groups[0]['roster'][0]
                if change == 'wrong_team':
                    groups[0]['team']['id'] = '1'
                elif change == 'duplicate_id':
                    row['athlete']['id'] = groups[0]['roster'][1]['athlete']['id']
                elif change == 'unmapped_role':
                    row['position'] = dict(displayName='Center Left Defender', abbreviation='CF-L')
                elif change == 'ambiguous_side':
                    groups.append(groups[0])
                else:
                    row['starter'] = 'true'
                roster, _ = f._roster(self.fix, [(dict(rosters=groups), {'sha256': 'a'})])
                self.assertIsNone(roster['home_starters'])
                self.assertEqual(len(roster['away_starters']), 11)
        groups = deepcopy(self.roles)
        groups[0]['roster'][0]['athlete']['id'] += '0'
        roster, audit = f._roster(self.fix, [(dict(rosters=r), {'sha256': h}) for r, h in [(groups, 'a'), (self.roles, 'b')]])
        self.assertIsNone(roster['home_starters'])
        self.assertEqual(audit['home']['status'], 'conflicting_starters')
        groups[0]['roster'][0]['athlete']['id'] = groups[1]['roster'][0]['athlete']['id']
        roster, _ = f._roster(self.fix, [(dict(rosters=groups), {'sha256': 'c'})])
        self.assertTrue(all(roster[s + '_starters'] is None for s in f.SIDES))

    def test_real_detailed_espn_roles_stay_missing_under_the_original_four_groups(self):
        self.roles[0]['roster'][1]['position'] = dict(displayName='Center Left Defender', abbreviation='CD-L')
        self.roles[1]['roster'][8]['position'] = dict(displayName='Center Right Forward', abbreviation='CF-R')
        self.summary()
        self.card()
        report, rosters = self.observe()
        self.assertEqual(report['dual_ft90'], 1)
        self.assertEqual(set(rosters), {self.mid})
        for side in f.SIDES:
            self.assertIsNone(rosters[self.mid][side + '_starters'])
            status = report['events'][self.mid]['roster_coverage'][side]
            self.assertEqual(status['status'], 'unknown_or_ambiguous_position')
            self.assertEqual(status['checks'][0]['explicit_starters'], 11)
        with patch.dict(f.study.POSITIONS, {'CD-L': 'D'}), self.assertRaisesRegex(ValueError, 'position vocabulary'):
            self.observe()

    def calendar_inputs(self):
        event = self.event()
        board = dict(leagues=[dict(slug=f.CODE)], events=[event])
        body = encoded(board)
        local = self.root / 'annual.json'
        local.write_bytes(body)
        annual = dict(path='annual.json', sha256=f.sha(local), bytes=len(body))
        cap = dict(annual, retrieved_at_utc=self.stamp, url=f.BOARD + '2026&limit=1000', http_status=200,
                   response_event_count=1, limit=1000)
        provenance = self.root / 'registration/calendar-provenance.json'
        provenance.parent.mkdir()
        provenance.write_bytes(encoded(dict(later_capture=[dict(annual, year=2026, source_capture=cap)])))
        rows = []
        for day in f.DAYS:
            populated = day == f.DAYS[-1]
            ref = write_capture(self.root, 'audit/' + day + '.json', encoded(board if populated else dict(board, events=[])),
                                date_query=day, requested_at_utc=self.stamp, captured_at_utc=self.stamp, url=f.BOARD + day,
                                event_count=int(populated), events=[dict(event_id=event['id'])] if populated else [])
            rows.append(ref)
        log = self.root / 'audit/boards.jsonl'
        log.write_bytes(lines(rows))
        audit = dict(audit_created_at_utc=self.stamp, scoreboard_evidence_path='audit/boards.jsonl',
                     scoreboard_event_counts_by_query_date={r['date_query']: r['event_count'] for r in rows},
                     annual_calendar_source=dict(annual, path=str(local), event_count=1),
                     annual_calendar_events_in_scope=[dict(event_id=event['id'], kickoff_raw=event['date'], home='home', away='away')])
        (self.root / 'audit/nov22-25-annual-calendar-audit.json').write_bytes(encoded(audit))
        self.enterContext(patch.object(f, 'CALENDAR_PROVENANCE_SHA', f.sha(provenance)))
        return rows, log

    def test_calendar_reads_raw_even_on_empty_days_and_rejects_unresolved_slots(self):
        rows, log = self.calendar_inputs()
        check = lambda: f._calendar(self.root, self.root, self.early, self.at, None)
        self.assertEqual(list(check()['counts'].values()), [0, 0, 0, 1])
        log.write_bytes(lines(rows[1:]))
        with self.assertRaisesRegex(ValueError, 'missing'):
            check()
        log.write_bytes(lines(rows))
        raw = self.root / rows[0]['path']
        raw.write_bytes(raw.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'SHA'):
            check()
        body = encoded(dict(leagues=[dict(slug=f.CODE)], events=[dict(self.event(), id='unexpected')]))
        raw.write_bytes(body)
        rows[0].update(bytes=len(body), sha256=sha256(body).hexdigest())
        log.write_bytes(lines(rows))
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            check()

    def test_raw_readback_request_after_capture_and_official_url_rejected(self):
        self.summary()
        self.card()
        self.official_rows[0]['requested_at_utc'] = '2026-11-25T12:03Z'
        path = self.root / f.OFFICIAL_LOGS[1]
        path.write_bytes(lines(self.official_rows))
        with self.assertRaisesRegex(ValueError, 'clocks'):
            self.observe()
        self.official_rows[0].update(requested_at_utc=self.stamp, url='https://example.test/112501/')
        path.write_bytes(lines(self.official_rows))
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.observe()


class WeekendBatchTests(unittest.TestCase):
    """Use real archive readers, R15 training and issuance, R18 prediction math and ledger in a temporary store."""

    def setUp(self):
        self.data = r15_tests.LineupForwardTests()
        self.data.setUp()
        self.addCleanup(self.data.doCleanups)
        self.root = self.data.root
        self.out = self.root / 'r18-issued'
        self.store = self.root / 'r18-store'
        self.display = self.root / 'display'
        self.intake = self.root / 'intake'
        self.early = dict(list(self.data.cohort.items())[:7])
        self.weekend = dict(list(self.data.cohort.items())[7:])
        self.set_native_r7_controls()
        self.data.run_adapter(issue=True)
        firsts = f.FirstIssues.load(self.data.out)
        self.original_plan = firsts.registration
        self.spec = self.original_plan['payload']['identity']
        self.window = dict(record_sha256='51' * 32, payload=dict(identity=dict(original_registration=self.spec['source_registrations']['r10'])))
        events, rosters = {}, {}
        for i, (mid, fixture) in enumerate(self.early.items()):
            roster = dict(match_id=mid, home_id='1', away_id='2', home_starters=None,
                          away_starters=[dict(id='20' + str(n), position=p) for n, p in enumerate('GDDDDMMMFFF')])
            rosters[mid] = roster
            checksum = sha256(mid.encode()).hexdigest()
            events[mid] = dict(fixture=fixture, verdict=f.AGREED, score=[1 + i % 3, 0], espn=dict(sha256=checksum),
                               official=dict(sha256=checksum), ft_summary_sources=[dict(sha256=checksum)], roster=roster)
        self.evidence = dict(events=events, pending=[], prior_slots=7, dual_ft90=7, weekend_slots=10)
        self.stack = self.enterContext(ExitStack())
        self.stack.enter_context(patch.object(f, 'R15_REGISTRATION_SHA', self.original_plan['record_sha256']))
        self.stack.enter_context(patch.object(f, 'THROUGH', '2026-01-03'))
        self.stack.enter_context(patch.object(f, '_windows', return_value=(self.window, self.early, self.weekend)))
        self.evidence_mock = self.stack.enter_context(patch.object(f, '_evidence', return_value=(self.evidence, rosters)))
        self.at = '2026-01-12T12:00:00Z'

    def set_native_r7_controls(self):
        # The reusable R15 fixture builder deliberately uses arbitrary issued R7 choices.
        # Rebuild the test publisher's R7/R10 leaves using the original native R7 grid.
        folders = self.data.folders
        r5s = archive_files(folders['r5'] / f.r15.FIRSTS['r5'])
        model = json.loads(archive_files(folders['r7'] / f.r15.MODELS['r7'])['main/forward.json'])
        parent = json.loads(r5s['issued/receipts.json'])
        grids = {}
        for mid, fixture in self.data.cohort.items():
            raw = json.loads(r5s['issued/' + parent['matches'][mid]['file']])['payload']
            grids[mid] = f.r7.predict(f._game(fixture, raw), model)
        for key in ('r7', 'r10'):
            folder = folders[key]
            name = f.r15.FIRSTS[key]
            files = archive_files(folder / name)
            index = json.loads(files['receipts.json'])
            for mid, ref in index['matches'].items():
                rec = json.loads(files[ref['file']])
                pick, quality = grids[mid]
                if key == 'r7':
                    rec['payload']['forecasts']['candidate'] = pick
                    rec['payload']['quality'] = quality
                else:
                    rec['payload']['forecasts']['r7'] = pick
                rec.pop('record_sha256')
                ref['sha256'] = rec['record_sha256'] = f.digest(rec)
                files[ref['file']] = encoded(rec)
            files['receipts.json'] = encoded(index)
            pack(folder / name, files)
            manifest = f.read(folder / 'manifest.json')
            manifest['files'][name] = f.sha(folder / name)
            (folder / 'manifest.json').write_bytes(encoded(manifest))

    def adapter(self, *, issue=False, at=None, out=None, firsts=None):
        with r15_tests.clock(at or self.at):
            return f.run(*self.data.folders.values(), self.data.study, self.data.rosters, firsts or self.data.out,
                         self.intake, self.display, out or self.out, issue=issue, freeze_root=self.store)

    def test_preview_full_population_and_original_slots_with_same_day_evidence(self):
        before = {p.name: f.sha(p) for p in self.data.out.iterdir() if p.is_file()}
        with patch.object(f.study, 'fit', side_effect=AssertionError('unexpected refit')):
            preview = self.adapter()
        self.assertEqual(preview['mode'], 'preview')
        self.assertFalse(self.out.exists() or self.store.exists())
        ident, payloads = preview['identity'], preview['payloads']
        self.assertEqual(set(payloads), set(self.weekend))
        self.assertEqual((ident['scheduled'], ident['eligible'], ident['filled']), (10, 10, 3))
        self.assertEqual(ident['r15_selected'], self.spec['selected'])
        self.assertEqual(ident['selected'][f.CODE], f.r15._seats(payloads))
        self.assertEqual(ident['ranking'], sorted(payloads, key=lambda m: (-payloads[m]['quality']['p_first'], m)))
        self.assertEqual(ident['r15_history_coverage']['normalized'], 13)
        self.assertEqual(len(ident['unpriced']), 9)
        for mid, p in payloads.items():
            ref = f.read(self.data.out / 'receipts.json')['matches'][mid]
            signed = f.ledger._read(self.data.out, ref)
            orig = signed['payload']
            self.assertEqual(p['forecasts']['r15'], orig['forecasts']['candidate'])
            self.assertEqual(p['r15_first_sha256'], ref['sha256'])
            self.assertEqual(p['prices'], orig['prices'])
            self.assertEqual(p['doses'], orig['doses'])
            self.assertEqual(p['r7_forward_sha256'], orig['r7_forward_sha256'])
            self.assertEqual(p['r7_grid_sha256'], f.digest(p['r7_grid']))
            self.assertEqual(p['process_before'], orig['process'])
            self.assertEqual(p['process_before']['home']['previous']['date'], '2026-01-03')
            self.assertEqual(p['process_after']['home']['previous']['date'], '2026-01-12')
            self.assertEqual(p['history_cutoff'], '2026-01-13')
            self.assertTrue(p['features_changed'])
            self.assertEqual(len(p['features']['away']), 6)
            for side in f.SIDES:
                for values in p['features'][side].values():
                    self.assertAlmostEqual(values['delta'], values['after'] - values['before'])
            self.assertEqual(set(p['new_history']), set(self.early))
            self.assertEqual(ident['new_history_sha256'], f.digest(p['new_history']))
            self.assertEqual(p['forecasts']['candidate']['top3'][0], p['forecasts']['candidate']['primary'])
            if mid in ident['unpriced']:
                self.assertIsNone(p['line'])
                self.assertIsNone(p['forecasts']['candidate']['total_side'])
            self.assertEqual(ident['payload_sha256'][mid], f.digest(p))
        self.assertEqual(before, {p.name: f.sha(p) for p in self.data.out.iterdir() if p.is_file()})

    def test_atomic_firsts_idempotent_after_kickoff_and_relocatable_archive(self):
        tar = self.root / 'original-firsts.tar.gz'
        pack(tar, {'issued/' + p.name: p.read_bytes() for p in self.data.out.glob('*.json')})
        result = self.adapter(issue=True, firsts=tar)
        self.assertEqual(len(result['matches']), 10)
        index = f.read(self.out / 'receipts.json')
        self.assertEqual(result['registration'], index['registration'])
        earliest = min(f.aware(v['kickoff_at']) for v in self.weekend.values())
        for ref in index['matches'].values():
            rec = f.ledger._read(self.out, ref)
            self.assertEqual(rec['track'], f.TRACK)
            self.assertEqual(rec['evaluation_slot'], 'FIRST_PREMATCH')
            self.assertLess(f.aware(ref['recorded_at_utc']), earliest)
            self.assertTrue(ref['recorded_at_utc'].endswith('+00:00'))
        restore = self.root / 'restored'
        self.assertEqual(self.adapter(issue=True, at='2026-02-01T12:00Z', out=restore), result)
        self.assertEqual(sorted(p.read_bytes() for p in self.out.iterdir()), sorted(p.read_bytes() for p in restore.iterdir()))
        self.assertTrue(all(call.args[3] == f.aware(self.at) for call in self.evidence_mock.call_args_list))
        original = self.out / index['matches'][next(iter(self.weekend))]['file']
        original.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'output differs'):
            self.adapter(issue=True)

    def test_incomplete_ft_never_creates_track_and_late_first_cannot_shrink_batch(self):
        self.evidence['pending'].append(next(iter(self.early)))
        with self.assertRaisesRegex(ValueError, 'awaiting dual FT90'):
            self.adapter(issue=True)
        self.assertFalse(self.out.exists() or self.store.exists())
        self.evidence['pending'].clear()
        with self.assertRaisesRegex(ValueError, 'earliest'):
            self.adapter(issue=True, at='2026-01-17T09:00Z')
        self.assertFalse(self.out.exists() or self.store.exists())

    def test_pending_preview_verifies_all_originals_and_unfitted_anchors_without_ranking_updated_targets(self):
        mid = next(iter(self.early))
        self.evidence['pending'] = [mid]
        self.evidence_mock.return_value[1].pop(mid)
        with patch.object(f, '_updated', side_effect=AssertionError('partial weekend comparison')):
            preview = self.adapter()
        self.assertEqual(preview['mode'], 'preview_pending')
        self.assertEqual(preview['payloads'], {})
        self.assertEqual(set(preview['identity']['reproduced_r15']), set(self.weekend))
        self.assertEqual(set(preview['identity']['prior_anchors']), set(self.early))
        self.assertNotIn('selected', preview['identity'])
        self.assertFalse(self.out.exists() or self.store.exists())

    def test_zero_full_doses_issue_tied_predictions_and_original_seats_even_when_features_change(self):
        # Issue an independent synthetic R15 original using the actual registered dose pattern.
        folder = self.root / 'zero-dose-study'
        folder.mkdir()
        (folder / 'inputs.json').write_bytes((self.data.study / 'inputs.json').read_bytes())
        doses = dict(full=dict(score=0., wdl=0., total=0.), form=dict(score=1., wdl=0., total=0.))
        fit, recipe = (f.read(self.data.study / n) for n in ('fit.json', 'forward.json'))
        (folder / 'fit.json').write_bytes(encoded(dict(fit, doses=doses)))
        (folder / 'forward.json').write_bytes(encoded(dict(recipe, doses=doses, fit_sha256=f.sha(folder / 'fit.json'))))
        originals = self.root / 'zero-dose-firsts'
        with r15_tests.clock(self.data.now):
            plan = f.r15.run(*self.data.folders.values(), folder, self.data.rosters, originals, issue=True,
                             freeze_root=self.root / 'zero-dose-r15-store')
        with patch.object(f, 'R15_REGISTRATION_SHA', plan['sha256']), patch.object(self.data, 'study', folder):
            result = self.adapter(issue=True, firsts=originals)
        identity = f.ledger._read(self.out, result['registration'])['payload']['identity']
        self.assertEqual(identity['doses'], doses)
        self.assertEqual(identity['selected'], identity['r15_selected'])
        self.assertEqual(identity['ranking'], identity['r15_ranking'])
        self.assertEqual(len(result['matches']), 10)
        for ref in result['matches'].values():
            body = f.ledger._read(self.out, ref)['payload']
            self.assertTrue(body['features_changed'])
            self.assertEqual(body['forecasts']['candidate'], body['forecasts']['r15'])
            self.assertEqual(body['quality'], body['r15_quality'])

    def test_reject_missing_original_first_and_prevent_reusing_its_output(self):
        with self.assertRaisesRegex(ValueError, 'separate from the original'):
            self.adapter(issue=True, out=self.data.out)
        index = f.read(self.data.out / 'receipts.json')
        del index['matches'][next(iter(self.weekend))]
        (self.data.out / 'receipts.json').write_bytes(encoded(index))
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.adapter(issue=True)
        self.assertFalse(self.store.exists())

    def test_other_checksum_valid_r15_registration_cannot_substitute_for_the_signed_ten(self):
        firsts = self.root / 'later-r15-firsts'
        with r15_tests.clock('2026-01-12T10:00Z'):
            later = f.r15.run(*self.data.folders.values(), self.data.study, self.data.rosters, firsts,
                              issue=True, freeze_root=self.root / 'later-r15-store')
        self.assertNotEqual(later['sha256'], self.original_plan['record_sha256'])
        with self.assertRaisesRegex(ValueError, 'different signed R15 registration'):
            self.adapter(issue=True, firsts=firsts)
        self.assertFalse(self.store.exists() or self.out.exists())

    def test_crossing_earliest_kickoff_while_signing_discards_all_ten_staged_firsts(self):
        before = f.aware(self.at)
        kickoff = min(f.aware(v['kickoff_at']) for v in self.weekend.values())
        with patch.object(f, '_now', side_effect=[before, before, kickoff]), self.assertRaisesRegex(ValueError, 'batch crossed'):
            self.adapter(issue=True)
        self.assertFalse(self.out.exists() or (self.store / f.TRACK).exists())

    def test_failed_mid_batch_creates_no_canonical_first_then_retry_succeeds(self):
        publish = f.ledger.issue
        attempted = []

        def interrupted(plan, fixture, *args, **kwargs):
            attempted.append(fixture['match_id'])
            if len(attempted) == 5:
                raise OSError('interrupted')
            return publish(plan, fixture, *args, **kwargs)

        with patch.object(f.ledger, 'issue', side_effect=interrupted), self.assertRaisesRegex(OSError, 'interrupted'):
            self.adapter(issue=True)
        self.assertEqual(len(attempted), 5)
        self.assertFalse((self.store / f.TRACK).exists() or self.out.exists())
        self.assertEqual(len(self.adapter(issue=True)['matches']), 10)

    def test_cannot_create_first_batch_when_inputs_or_source_archive_differ(self):
        saved = self.data.study / 'forward.json'
        body = saved.read_bytes()
        recipe = json.loads(body)
        recipe['fitted']['full']['training_games'] -= 1
        saved.write_bytes(encoded(recipe))
        with self.assertRaisesRegex(ValueError, 'full-panel recipe'):
            self.adapter(issue=True)
        saved.write_bytes(body)
        folder = self.data.folders['r5']
        path = folder / f.r15.FIRSTS['r5']
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.adapter(issue=True)
        self.assertFalse(self.out.exists() or self.store.exists())

    def test_unfitted_anchor_uses_original_prices_and_is_independent_of_fitted_grid(self):
        baseline = json.loads(archive_files(self.data.folders['r7'] / f.r15.MODELS['r7'])['main/forward.json'])
        archive = archive_files(self.data.folders['r5'] / f.r15.FIRSTS['r5'])
        mid, fixture = next(iter(self.early.items()))
        ref = json.loads(archive['issued/receipts.json'])['matches'][mid]
        odds = json.loads(archive['issued/' + ref['file']])['payload']
        game = f._game(fixture, odds)
        before = deepcopy(baseline)
        anchor = f._unfitted(game, baseline)
        with patch.object(f.r7, 'matrix', side_effect=AssertionError('used fitted grid for residual')):
            np.testing.assert_array_equal(anchor, f._unfitted(game, dict(baseline, policy={'mix': 1})))
        self.assertEqual(before, baseline)
        self.assertFalse(np.allclose(anchor, f._unfitted(dict(game, h2h=None, total=None, paired_market=False), baseline)))
        p = self.adapter()['payloads'][next(iter(self.weekend))]
        self.assertEqual(p['new_history'][mid]['unfitted_anchor_sha256'], f.digest(anchor.tolist()))
        signed, q = f.r7.predict(game, baseline)
        with self.assertRaisesRegex(ValueError, 'did not reproduce'):
            f._check_grid(game, baseline, dict(forecasts=dict(candidate=signed), quality=dict(q, p_first=0.99)))

    def test_existing_foreign_output_is_preserved(self):
        self.out.mkdir()
        sentinel = self.out / 'original.json'
        sentinel.write_bytes(b'original')
        with self.assertRaisesRegex(ValueError, 'output already contains'):
            self.adapter(issue=True)
        self.assertEqual(sentinel.read_bytes(), b'original')
        self.assertFalse((self.store / f.TRACK).exists())


if __name__ == '__main__':
    unittest.main()
