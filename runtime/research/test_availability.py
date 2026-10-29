import unittest
from research.availability import extract,compare

class AvailabilityTests(unittest.TestCase):
    def fixture(self,team):return {'content':{'lineup':{'homeTeam':team}}}

    def test_unavailable_is_not_lost_when_legacy_arrays_are_empty(self):
        source=self.fixture({'injuredPlayers':[],'suspendedPlayers':[],
            'unavailable':[{'id':690230,'name':'Lautaro Martínez','unavailability':{'type':'injury','expectedReturn':'Mid November 2026'}}]})
        row=extract(source)['sides']['home']
        self.assertEqual([p['name'] for p in row['items']],['Lautaro Martínez'])
        self.assertFalse(row['items'][0]['official_confirmation'])

    def test_coach_suspension_is_not_assigned_to_player_group(self):
        person={'id':99,'name':'Coach','unavailability':{'type':'suspension'}}
        row=extract(self.fixture({'coach':{'id':'99','name':'Coach'},'unavailable':[person]}))['sides']['home']['items'][0]
        self.assertEqual(row['role'],'coach')
        self.assertEqual(row['provider_type'],'suspension')

    def test_empty_and_missing_remain_unknown(self):
        self.assertEqual(extract(self.fixture({'unavailable':[]}))['sides']['home']['status'],'PROVIDER_EMPTY_UNKNOWN')
        row=extract({})['sides']['home']
        self.assertEqual(row['status'],'SCHEMA_MISSING_UNKNOWN')
        self.assertFalse(row['health_clearance'])

    def test_suspended_coach_can_differ_from_current_standin(self):
        source=self.fixture({'coach':{'id':8,'name':'Stand-in'},'unavailable':[
            {'id':99,'name':'Head coach','unavailability':{'type':'suspension'}}]})
        self.assertEqual(extract(source)['sides']['home']['items'][0]['role'],'unverified_person_role')
        self.assertEqual(extract(source,coach_ids=[99])['sides']['home']['items'][0]['role'],'coach')

    def test_same_raw_schema_does_not_create_new_injuries(self):
        person={'id':2,'name':'Player','unavailability':{'type':'injury'}}
        source=self.fixture({'unavailable':[person]})
        self.assertEqual(compare(source,source)['new_provider_listings'],[])
        duplicated=self.fixture({'unavailable':[person],'injuredPlayers':[person]})
        self.assertEqual(len(extract(duplicated)['sides']['home']['items']),1)

if __name__=='__main__':unittest.main()
