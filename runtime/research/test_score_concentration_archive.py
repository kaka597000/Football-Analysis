import copy
import unittest
from analysis_freeze import digest
from score_concentration_archive import inspect_archive_records


def seal(record):
    record.pop('record_sha256',None)
    record['record_sha256']=digest(record)
    return record


class ArchiveTests(unittest.TestCase):
    def data(self):
        fixture={'match_id':'synthetic-one','cohort':'synthetic',
                 'kickoff_at':'2035-01-01T03:00:00+00:00','cutoff_at':'2035-01-01T01:00:00+00:00'}
        pred={'version':'synthetic-v1','primary':'1-1','matrix':[[.1,.2],[.3,.4]]}
        record=seal({'schema':'WCPP-ISSUED-FREEZE-1','freeze_id':'synthetic-freeze',
                     'match_id':'synthetic-one','stage':'prematch','track':'current-test-pair',
                     'revision':1,'previous_sha256':None,'evaluation_slot':'FIRST_PREMATCH',
                     'recorded_at':'2035-01-01T02:00:00+00:00','fixture':fixture,
                     'payload':{k:copy.deepcopy(pred) for k in ('current_model','test_model')}})
        entry={'batch':'night','fotmob_match_id':123,'pair':record}
        results={'matches':[{'fotmob_match_id':123,'status':'FT_90_MIN','identity_verified':True,
                  'home_goals_90':1,'away_goals_90':1,'source':{'fotmob_score_match_espn':True,
                  'source_url':'https://example.org/synthetic','sha256':'e'*64}}]}
        return [entry],results

    def test_verified_records_and_first_versions(self):
        entries,results=self.data()
        report=inspect_archive_records(entries,results,batch='night',earliest=[entries[0]['pair']])
        self.assertEqual(report['population_kind'],'RECOVERED_FIRST_PREMATCH')
        self.assertEqual(report['mixed_cohort_descriptive_overviews'][0]['primary']['hits'],1)

    def test_tampered_record_duplicate_batch_and_missing_population_rejected(self):
        entries,results=self.data(); entries[0]['pair']['payload']['current_model']['primary']='2-1'
        with self.assertRaisesRegex(ValueError,'checksum'): inspect_archive_records(entries,results,batch='night')
        entries,results=self.data()
        with self.assertRaisesRegex(ValueError,'duplicate'): inspect_archive_records(entries*2,results,batch='night')
        with self.assertRaisesRegex(ValueError,'populations'): inspect_archive_records(entries,results,batch='night',earliest=[])

    def test_two_internal_ids_cannot_count_same_provider_match_twice(self):
        entries,results=self.data(); other=copy.deepcopy(entries[0])
        other['pair']['match_id']='synthetic-other'; other['pair']['fixture']['match_id']='synthetic-other'
        seal(other['pair']); entries.append(other)
        with self.assertRaisesRegex(ValueError,'duplicate'): inspect_archive_records(entries,results,batch='night')

    def test_first_matrix_does_not_silently_use_changed_kickoff(self):
        entries,results=self.data(); earliest=copy.deepcopy(entries[0]['pair'])
        earliest['fixture']['kickoff_at']='2035-01-02T03:00:00+00:00'; seal(earliest)
        with self.assertRaisesRegex(ValueError,'kickoff conflict'):
            inspect_archive_records(entries,results,batch='night',earliest=[earliest])

    def test_first_revision_contract_rejected(self):
        entries,results=self.data(); r=entries[0]['pair']; r['revision']=2; seal(r)
        with self.assertRaisesRegex(ValueError,'first prematch'):
            inspect_archive_records(entries,results,batch='night',earliest=[r])


if __name__=='__main__': unittest.main()
