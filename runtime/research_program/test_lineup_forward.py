from contextlib import ExitStack
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research_program import lineup_forward as f
from research_program.test_lineup_study import archives, encoded, fixtures, lines, pack, roster
from research_program.test_territory_forward import record


def clock(value):
    instant = f.aware(value)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    stack = ExitStack()
    stack.enter_context(patch.object(f.ledger, '_now', return_value=instant.isoformat()))
    stack.enter_context(patch('research.analysis_freeze.datetime', Clock))
    return stack


class LineupForwardTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.out, self.store, self.study = (self.root / k for k in ('out', 'store', 'study'))
        self.rosters = self.root / 'rosters.jsonl'
        games = fixtures()
        self.folders = dict(zip(('r5', 'r7', 'r10'), archives(self.root, games)))
        rows = [dict(roster(g), source_query_time_utc='2026-01-10T01:00Z') for g in games]
        self.rosters.write_bytes(lines(rows))
        self.snapshot = self.root / 'snapshot-manifest.json'
        self.snapshot.write_bytes(encoded(dict(schema='j1-lineups-history-intake-snapshot-v1',
              events_jsonl_sha256=f.sha(self.rosters), snapshot_as_of_utc='2026-01-11T12:00Z',
              events_jsonl_rows=len(rows), unique_source_match_ids=len(rows),
              r5_panel_package_sha256=f.sha(self.folders['r5']/f.MODELS['r5']),
              r5_panel_member_sha256=sha256(lines(games)).hexdigest())))
        self.cohort = {}
        for i in range(17):
            mid = f'espn:{800 + i}'
            day = 12 if i < 7 else 17 if i < 12 else 18
            self.cohort[mid] = dict(match_id=mid, cohort=f.CODE, stage='Japanese J1 League',
                                    kickoff_at=f'2026-01-{day}T09:00Z', home='espn:1', away='espn:2')
        self.make_sources()
        f.study.run(*self.folders.values(), self.rosters, self.study)
        self.now = '2026-01-12T09:00:00Z'

    def make_sources(self, fault=None):
        schedule, markets, files = [], [], {}
        at = '2026-01-04T03:00:00Z'
        quotes = {}
        for mid, fixture in self.cohort.items():
            eid = mid.split(':')[1]
            key = 'raw/' + eid + '.json'
            board = dict(leagues=[dict(slug=f.CODE)], events=[dict(id=eid, competitions=[dict(id=eid,
                         date=fixture['kickoff_at'], status=dict(type=dict(name='STATUS_SCHEDULED')),
                         competitors=[dict(homeAway=s, team=dict(id=i)) for s, i in [('home', '1'), ('away', '2')]])])])
            if fault == 'raw_schedule' and eid == '807':
                board['events'][0]['competitions'][0]['competitors'][0]['team']['id'] = '999'
            files[key] = encoded(board)
            source = dict(sha256=sha256(files[key]).hexdigest(), raw_path=key, url='https://example.test/' + eid)
            shared = dict(match_id=mid, event_id=eid, competition=f.CODE, home='Club 1', away='Club 2',
                          home_id='1', away_id='2', stage=fixture['stage'], kickoff_at=fixture['kickoff_at'],
                          identity_verified=True, observed_at=at)
            schedule.append(dict(shared, status='STATUS_SCHEDULED', score_basis='regulation_90', sources=[source]))
            priced = eid in ('800', '807')
            meta = dict(observed_at=at, quoted_at=None, bookmaker='Book', snapshot_label='close',
                        source=dict(sha256=source['sha256'], file=key, url=source['url']))
            hda = dict(meta, prices={side: dict(decimal=n) for side, n in [('home', 2.), ('away', 4.), ('draw', 3.)]})
            total = dict(meta, over=dict(line=2.25, decimal=1.9), under=dict(line=2.25, decimal=2.))
            markets.append(dict(shared, paired_market=priced, h2h={'close': hda} if priced else None,
                                total={'close': total} if priced else None, market_status='PAIRED' if priced else 'NO_CURRENT_PRICE'))
            evidence = dict(observed_at=at, quoted_at=None, bookmaker='Book', source_sha256=source['sha256'],
                            source_url=source['url'], snapshot_label='close')
            quotes[mid] = {k: dict(evidence) for k in ('h2h', 'total')} if priced else {}
        files.update({'fixtures.jsonl': lines(schedule), 'market-latest-status.jsonl': lines(markets)})
        pack(self.folders['r5']/'forward-observations.tar.gz', {'forward/' + k: v for k, v in files.items()})
        weights = {}
        for key, member in (('r7', 'main/forward.json'), ('r10', 'main/jpn.1/forward.json')):
            with tarfile.open(self.folders[key]/f.MODELS[key]) as t:
                weights[key] = sha256(t.extractfile(member).read()).hexdigest()
        plans = dict(r5=dict(model=f.study.base.VERSION, schedule_sha256=sha256(lines(schedule)).hexdigest(),
                             market_status_sha256=sha256(lines(markets)).hexdigest()),
                     r7=dict(version=f.r7.model.VERSION, recipe_sha256={f.CODE: weights['r7']}),
                     r10=dict(version=f.study.decision.VERSION, recipe_sha256={f.CODE: weights['r10']}))
        origin = {}
        for i, (key, name) in enumerate(f.FIRSTS.items()):
            stamp = f.aware(f'2026-01-0{4 + i}T05:00:00Z')
            files = {}
            track = 'test-' + key
            subset = dict(self.cohort)
            if fault == 'missing_r7' and key == 'r7':
                del subset['espn:807']
            plan = record(files, track + '-registration', dict(match_id=track + '-plan'),
                          dict(fixtures=subset, track=track, identity=plans[key]), stamp)
            matches = {}
            for mid, sched, market in zip(self.cohort, schedule, markets, strict=True):
                if mid not in subset:
                    continue
                fix = dict(subset[mid], cutoff_at=stamp.isoformat())
                hda, total, paired, label = f.r5_model.prices(market)
                line = total['line'] if total else None
                control = dict(primary='4-4', top3=['4-4', '2-3', '4-1'], wdl='away',
                               total_side='under' if total else None, total_line=line)
                market_tip = dict(wdl='draw') if hda else {}
                if key == 'r5':
                    p = dict(prices=dict(h2h=hda, total=total), market_mode='paired' if paired else 'team_fallback',
                             displayed_label=label, market_evidence=quotes[mid], fixture_sources=sched['sources'],
                             forecasts=dict(candidate=dict(control, wdl='home'), market=market_tip),
                             home_name='Club 1', away_name='Club 2', model_teams=['Alias 1', 'Alias 2'],
                             model_stage='regular', neutral=False)
                    if mid == 'espn:807' and fault == 'price':
                        p['prices']['total']['under'] = 2.2
                    if mid == 'espn:807' and fault == 'quote':
                        p['market_evidence']['h2h']['observed_at'] = '2026-01-05T08:00Z'
                elif key == 'r7':
                    p = dict(input_sha256=origin[mid], model_sha256=weights[key], training_through='2026-01-03',
                             forecasts=dict(candidate=control, market=market_tip))
                else:
                    p = dict(odds_source_first_issue=origin[mid], r7_forward_sha256=weights['r7'],
                             archived_cutoff_at='2026-01-04T05:00:00Z', recipe_sha256=weights[key],
                             training_through='2026-01-03', forecasts=dict(candidate=dict(control, wdl='draw'),
                             r7=dict(control, wdl='home') if fault == 'r7_pick' else control, market=market_tip))
                p.update(line=line, registration_sha256=plan['sha256'])
                ref = record(files, track, fix, p, stamp)
                if fault == 'not_first' and key == 'r7' and mid == 'espn:807':
                    saved = json.loads(files[ref['file']])
                    saved.update(revision=2, evaluation_slot='SEPARATE_REVISION_OR_STAGE')
                    del saved['record_sha256']
                    saved['record_sha256'] = ref['sha256'] = f.digest(saved)
                    files[ref['file']] = encoded(saved)
                matches[mid] = ref
                if key == 'r5':
                    origin[mid] = ref['sha256']
            files['receipts.json'] = encoded(dict(registration=plan, matches=matches))
            prefix = 'issued/' if key == 'r5' else ''
            pack(self.folders[key]/name, {prefix + n: b for n, b in files.items()})
        for folder in self.folders.values():
            hashes = {p.relative_to(folder).as_posix(): f.sha(p) for p in folder.rglob('*') if p.is_file() and p.name != 'manifest.json'}
            (folder/'manifest.json').write_bytes(encoded(dict(files=hashes)))

    def run_adapter(self, *, issue=False, at=None):
        with clock(at or self.now):
            return f.run(*self.folders.values(), self.study, self.rosters, self.out, issue=issue, freeze_root=self.store)

    def test_preview_and_firsts_lock_the_same_ten_slots_with_three_seats(self):
        preview = self.run_adapter()
        self.assertEqual(preview['mode'], 'preview')
        self.assertFalse(self.out.exists())
        self.assertFalse(self.store.exists())
        plan = self.run_adapter(issue=True)
        saved = f.ledger._read(self.out, plan)
        body = saved['payload']
        self.assertEqual(body['identity'], preview['identity'])
        self.assertEqual(len(body['fixtures']), 10)
        self.assertEqual(body['identity']['already_started'], list(self.cohort)[:7])
        refs = f.read(self.out/'receipts.json')['matches']
        payloads = {}
        for mid, receipt in refs.items():
            first = f.ledger._read(self.out, receipt)
            self.assertEqual(first['evaluation_slot'], 'FIRST_PREMATCH')
            p = first['payload'].copy()
            self.assertEqual(p.pop('registration_sha256'), plan['sha256'])
            self.assertEqual(f.digest(p), body['identity']['payload_sha256'][mid])
            self.assertEqual(p['roster_manifest_sha256'], f.sha(self.snapshot))
            self.assertEqual(p['recipe_sha256'], f.sha(self.study/'forward.json'))
            self.assertEqual(p['reference'], 'original_prospective_R7_first')
            self.assertEqual((p['home_name'], p['process']['home']['team_id']), ('Alias 1', '1'))
            self.assertEqual(p['process']['home']['previous']['date'], '2026-01-03')
            self.assertEqual(p['history_cutoff'], '2026-01-12')
            self.assertFalse(p['formal_kpi_eligible'])
            tips = p['forecasts']
            self.assertEqual([tips[k]['wdl'] for k in ('r5', 'r7', 'r10')], ['home', 'away', 'draw'])
            self.assertEqual(tips['first_only'], dict(primary=tips['candidate']['primary']))
            for arm in ('form_only', 'form_matched_dose', 'fixed_full', 'fixed_form', 'local_candidate'):
                self.assertEqual(tips[arm]['total_line'], p['line'])
            payloads[mid] = p
        no_quote = payloads['espn:808']
        self.assertIsNone(no_quote['line'])
        self.assertEqual(no_quote['forecasts']['market'], {})
        self.assertIsNone(no_quote['forecasts']['candidate']['total_side'])
        priced = payloads['espn:807']
        self.assertEqual(priced['line'], 2.25)
        self.assertEqual(priced['prices']['total']['under'], 2.)
        self.assertIsNone(priced['market_evidence']['total']['quoted_at'])
        seats = body['identity']['selected'][f.CODE]
        expected = sorted(payloads, key=lambda mid: (-payloads[mid]['quality']['p_first'], mid))[:3]
        self.assertEqual([r['match_id'] for r in seats], expected)
        self.assertEqual([r['seat'] for r in seats], [1, 2, 3])
        self.assertLess(f.aware(saved['recorded_at']), min(f.aware(z['kickoff_at']) for z in body['fixtures'].values()))
        self.assertEqual(f.read(self.out/'pending.json')[f.CODE]['registered'], 10)

    def test_firsts_survive_interruption_and_late_retry_without_reselection(self):
        original = f.ledger.issue
        calls = []

        def interrupted(*args, **kwargs):
            if calls:
                raise RuntimeError('interruption')
            calls.append(original(*args, **kwargs))
            return calls[0]

        with patch.object(f.ledger, 'issue', side_effect=interrupted), self.assertRaisesRegex(RuntimeError, 'interruption'):
            self.run_adapter(issue=True)
        registered = f._existing(self.out)
        with self.assertRaisesRegex(ValueError, 'late prematch'):
            self.run_adapter(issue=True, at='2026-01-20T10:00Z')
        self.assertFalse((self.out/'receipts.json').exists())
        plan = self.run_adapter(issue=True)
        self.assertEqual(plan['sha256'], registered['record_sha256'])
        self.assertEqual(plan, self.run_adapter(issue=True, at='2026-01-20T10:00Z'))
        self.out = self.root / 'second-output'
        self.assertEqual(plan, self.run_adapter(issue=True, at='2026-01-20T10:00Z'))
        self.assertEqual(len(list(self.store.rglob('000001.json'))), 11)
        self.assertEqual(list(self.store.rglob('000002.json')), [])
        recipe = f.read(self.study/'forward.json')
        recipe['fitted']['full']['scoring_beta'][0] += .01
        (self.study/'forward.json').write_bytes(encoded(recipe))
        with self.assertRaisesRegex(ValueError, 'first registration differs'):
            self.run_adapter(issue=True)

    def test_rejects_spliced_source_prices_slots_and_reference_before_registering(self):
        for fault, why in [('raw_schedule', 'raw scheduled fixture'), ('price', 'source prices'), ('quote', 'quote source'),
                           ('r7_pick', 'controls disagree'), ('not_first', 'registered first issue'),
                           ('missing_r7', 'missing same-fixture')]:
            with self.subTest(fault=fault):
                self.make_sources(fault)
                with self.assertRaisesRegex(ValueError, why):
                    self.run_adapter(issue=True)
                self.assertFalse(list(self.store.rglob('000001.json')))

    def test_cutoffs_and_hashes_reject_future_or_different_inputs(self):
        with self.assertRaisesRegex(ValueError, 'source snapshot'):
            self.run_adapter(at='2026-01-10T15:00Z')
        self.assertEqual(len(self.run_adapter(at='2026-01-11T13:00Z')['fixtures']), 17)
        self.assertEqual(self.run_adapter(at='2026-01-19T13:00Z')['identity']['selected'][f.CODE], [])
        with self.assertRaisesRegex(ValueError, 'no unstarted'):
            self.run_adapter(issue=True, at='2026-01-19T13:00Z')
        self.rosters.write_bytes(self.rosters.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'snapshot manifest'):
            self.run_adapter(issue=True)
        self.assertFalse(list(self.store.rglob('000001.json')))

    def test_short_research_slate_and_equal_first_probabilities(self):
        p = dict(quality=dict(p_first=.1), forecasts=dict(candidate=dict(primary='1-0', top3=['1-0','1-1','2-0'])))
        seats = f._seats(dict(z=p, a=p))
        self.assertEqual([r['match_id'] for r in seats], ['a', 'z'])
        self.assertEqual([r['seat'] for r in seats], [1, 2])
        self.assertEqual(f._seats({}), [])

    def test_reads_earlier_display_seats_without_reranking_the_published_window(self):
        folder = self.root / '20261125-j1-stage-windows'
        early, weekend = list(self.cohort)[:7], list(self.cohort)[7:]
        with tarfile.open(self.folders['r10']/f.FIRSTS['r10']) as t:
            parent = json.load(t.extractfile('receipts.json'))['registration']['sha256']
        stamp = f.aware('2026-01-09T13:00Z')
        files = {}
        fields = dict(original_registration=parent, slates=dict(early=early, weekend=weekend),
                      current_sha256='a'*64, test_sha256='b'*64)
        plan = record(files, f.display_windows.TRACK+'-registration', dict(match_id='display-plan'),
                      dict(identity=fields, fixtures=self.cohort), stamp)
        reg = folder/'registration'
        reg.mkdir(parents=True)
        (reg/'plan.json').write_bytes(encoded(plan))
        (reg/plan['file']).write_bytes(files[plan['file']])
        display = folder/'early'
        display.mkdir()
        selected = [early[-1], early[1], early[2]]
        rows = {mid: dict(authored=dict(sha256='c'*64), pair=dict(sha256='d'*64)) for mid in early}
        identity = dict(parent=plan['sha256'], selected=selected, rows=rows)
        fixtures_by_id = {mid:self.cohort[mid] for mid in early}
        publication = record(files, f.display_windows.TRACK+'-early-registration', dict(match_id='early-display'),
                             dict(identity=identity,fixtures=fixtures_by_id), stamp)
        (display/'publication.json').write_bytes(encoded(publication))
        (display/publication['file']).write_bytes(files[publication['file']])
        (display/'entries.json').write_text('{}')
        (display/'message.txt').write_text('published firsts')
        with patch.object(f.display_windows, 'prepared_batch', return_value=(plan, list(fixtures_by_id.values()), identity)):
            preview = self.run_adapter()
        refs = preview['identity']['prior_display']
        self.assertEqual(refs['registration_sha256'], plan['sha256'])
        self.assertEqual([refs['slots'][mid]['published_seat'] for mid in selected], [1,2,3])
        self.assertTrue(all(refs['slots'][mid]['status']=='no_verified_publication_by_cutoff' for mid in weekend))
        self.assertFalse({r['match_id'] for r in preview['identity']['selected'][f.CODE]} & set(early))
        fields['original_registration'] = 'e'*64
        other = record(files, f.display_windows.TRACK+'-registration', dict(match_id='display-plan'),
                       dict(identity=fields, fixtures=self.cohort), stamp)
        (reg/'plan.json').write_bytes(encoded(other))
        (reg/other['file']).write_bytes(files[other['file']])
        with self.assertRaisesRegex(ValueError, 'display windows differ'):
            self.run_adapter(issue=True)

    def test_fit_input_and_protocol_hashes_are_checked_before_first(self):
        for path in (self.study/'fit.json', self.study/'inputs.json', self.snapshot):
            with self.subTest(path=path.name):
                body = path.read_bytes()
                path.write_bytes(body + b'\n')
                with self.assertRaisesRegex(ValueError, 'differs'):
                    self.run_adapter(issue=True)
                path.write_bytes(body)
        protocol = self.root/'changed-protocol.json'
        protocol.write_bytes(f.study.PROTOCOL.read_bytes() + b'\n')
        with clock(self.now), self.assertRaisesRegex(ValueError, 'protocol'):
            f.run(*self.folders.values(), self.study, self.rosters, self.out, issue=True,
                  protocol=protocol, freeze_root=self.store)
        self.assertFalse(list(self.store.rglob('000001.json')))


if __name__ == '__main__':
    unittest.main()
