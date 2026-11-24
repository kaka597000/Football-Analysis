import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from assemble_native import assemble
from join_labels import join
from study import fit, grade, input_rows, packed, save, seal, sha, write


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.market = dict(provider='Bet365', competition='eng.1', home='home', away='away',
                           latest_h2h={'home': 2.2, 'draw': 3.1, 'away': 3.3},
                           opening_total={'line': 2.5, 'over': 2.1, 'under': 1.8},
                           current_total={'line': 2.5, 'over': 1.9, 'under': 2.0})

    def file(self, name, rows):
        p = self.root/name
        write(p, b''.join(packed(x) for x in rows))
        return p

    def test_real_stages_only_open_late_labels_after_forecast_lock(self):
        train = [dict(self.market, id=str(i), split='train', season='1920', date='2020-01-01') for i in range(12)]
        test = dict(self.market, id='target', split='test', season='2324', date='2023-09-08')
        inputs = self.file('inputs', train + [test])
        labels = self.file('early', [dict(r, score=[2,2]) for r in train])
        fitted = self.root/'fit.json'
        self.assertGreater(fit('proxy', inputs, labels, fitted)['coefficient'], 0)
        seal('proxy', inputs, fitted, None, self.root)
        later = self.file('late', [dict(test, score=[1,1])])
        result = grade('proxy', inputs, later, self.root/'lock.json', self.root/'results.json')
        self.assertEqual(result['all_paired']['paired'], 1)
        self.assertFalse(result['eligible_for_future_proposal'])
        with self.assertRaisesRegex(ValueError, 'late target'):
            fit('proxy', inputs, later, self.root/'wrong.json')
        with (self.root/'forecasts.jsonl').open('ab') as f:
            f.write(b' ')
        with self.assertRaisesRegex(ValueError, 'changed'):
            grade('proxy', inputs, later, self.root/'lock.json', self.root/'different')

    def test_results_in_inputs_or_wrong_native_capture_do_not_get_graded(self):
        p = self.file('leak', [dict(self.market, id='1', split='test', score=[0,0])])
        with self.assertRaisesRegex(ValueError, 'outcome'):
            input_rows(p, 'proxy')
        r = dict(self.market, provider='DraftKings', id='espn:1', home_id='1', away_id='2',
                 date='2026-11-09', cutoff_at='2026-11-09T12:00:00Z', kickoff_at='2026-11-09T13:00:00Z',
                 capture_at='2026-11-09T12:00:00Z', freeze_sha256='f'*64, raw_sha256='a'*64)
        with self.assertRaisesRegex(ValueError, 'strictly predecision'):
            assemble([self.file('late-capture', [r])], set(), self.root/'bad')

    def test_later_revision_never_upgrades_missing_first_and_alias_protects_r7(self):
        first = dict(id='fotmob:1', date='2026-11-09', provider='DraftKings', competition='eng.1',
                     cutoff_at='2026-11-09T10:00:00Z', kickoff_at='2026-11-09T14:00:00Z',
                     source_ids={'espn':'11', 'fotmob':'1'}, home_id='1', away_id='2',
                     excluded='missing_original_body')
        newer = dict(first, id='espn:11', cutoff_at='2026-11-09T11:00:00Z', excluded=None)
        p = self.file('two-views', [newer, first])
        for directory, protected, reason in [('ordinary', set(), 'missing_original_body'),
                                             ('held', {'espn:11'}, 'excluded_R7')]:
            out = self.root/directory
            stats = assemble([p], protected, out)
            self.assertEqual(stats['unique'], 1)
            stored = json.loads((out/'inputs.jsonl').read_bytes())
            self.assertEqual(stored['excluded'], reason)
            self.assertEqual(stored['cutoff_at'], first['cutoff_at'])

    def test_revision_only_reference_cannot_hide_an_authenticated_original(self):
        source = dict(id='fotmob:2', date='2026-11-09', provider='DraftKings', competition='eng.1',
                      cutoff_at='2026-11-09T10:00:00Z', kickoff_at='2026-11-09T14:00:00Z',
                      source_ids={'espn_event_id':'espn:22'}, excluded='missing_original_body')
        revision = dict(source, id='espn:22', cutoff_at=None, recorded_at='2026-11-09T09:59:00Z',
                        excluded='unavailable_unambiguous_original_first')
        inputs = self.file('versions', [revision, source])
        for directory, ids, expected in [('keep', set(), 'missing_original_body'),
                                         ('protected', {'espn:22'}, 'excluded_R7')]:
            out = self.root/directory
            assemble([inputs], ids, out)
            first = json.loads((out/'inputs.jsonl').read_bytes())
            self.assertEqual((first['id'], first['excluded']), ('fotmob:2', expected))

    def test_independent_certifications_cannot_double_count_or_disagree(self):
        identity = dict(id='espn:1',home_id='espn:10',away_id='espn:11',kickoff_at='2026-11-09T14:00:00Z')
        inputs = self.file('inputs', [identity])
        forecasts = self.file('forecasts.jsonl', [dict(id='espn:1',forecasts={'baseline':{},'candidate':{}})])
        lock = self.root/'lock.json'
        save(lock,dict(panel='native',input_sha256=sha(inputs),forecasts_sha256=sha(forecasts),
                       result_cutoff_at='2026-11-24T11:26:32Z'))
        label = dict(identity,observed_at='2026-11-09T17:00:00Z',evidence_sha256='a'*64,score=[1,2])
        first = self.file('first', [label])
        other = self.file('other', [dict(label,observed_at='2026-11-10T00:00:00Z',evidence_sha256='b'*64)])
        join(inputs,lock,[other,first],self.root/'combined')
        self.assertEqual(json.loads((self.root/'combined').read_bytes()), label)
        for number,edit in enumerate(({'score':[2,1]}, {'away_id':'espn:12'},
                                       {'observed_at':'2026-11-24T11:26:32Z'})):
            invalid = self.file(f'invalid{number}',[dict(label,**edit)])
            with self.assertRaises(ValueError):
                join(inputs,lock,[first,invalid],self.root/'rejected')
