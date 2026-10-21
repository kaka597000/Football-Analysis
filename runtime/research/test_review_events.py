import copy
import unittest
from review_events import normalize_fotmob_goal

class GoalReviewTests(unittest.TestCase):
    def event(self):
        return {'type':'Goal','homeScore':3,'awayScore':2,'newScore':[4,2],
                'isHome':True,'ownGoal':True,'timeStr':88,'fullName':'synthetic player',
                'shotmapEvent':{'teamId':20,'isOwnGoal':True}}
    def run_event(self,e):return normalize_fotmob_goal(e,home_team_id=10,away_team_id=20)
    def test_home_credited_own_goal_belongs_to_away_player(self):
        e=self.event();old=copy.deepcopy(e);d=self.run_event(e)['detail']
        self.assertEqual(d['team_side'],'away');self.assertEqual(d['beneficiary_side'],'home')
        self.assertEqual(e,old)
    def test_away_credited_own_goal_belongs_to_home_player(self):
        e=self.event();e.update(newScore=[3,3],isHome=False);e['shotmapEvent']['teamId']=10
        d=self.run_event(e)['detail'];self.assertEqual((d['team_side'],d['beneficiary_side']),('home','away'))
    def test_normal_goal_keeps_side_and_added_time(self):
        e=self.event();e.update(ownGoal=False,overloadTime=4,timeStr=90)
        e['shotmapEvent']={'teamId':10,'isOwnGoal':False}
        d=self.run_event(e);self.assertEqual(d['minute'],'90 + 4')
        self.assertEqual(d['detail']['team_side'],d['detail']['beneficiary_side'])
    def test_wrong_provider_side_rejected(self):
        e=self.event();e['isHome']=False
        with self.assertRaises(ValueError):self.run_event(e)
    def test_null_event_flag_uses_explicit_shot_flag(self):
        e=self.event();e['ownGoal']=None
        self.assertTrue(self.run_event(e)['detail']['own_goal'])
        e['shotmapEvent']={}
        with self.assertRaises(ValueError):self.run_event(e)
    def test_wrong_actor_team_or_own_goal_flag_rejected(self):
        for field,value in [('teamId',10),('isOwnGoal',False)]:
            e=self.event();e['shotmapEvent'][field]=value
            with self.assertRaises(ValueError):self.run_event(e)
    def test_invalid_scores_and_flags_rejected(self):
        for change in [{'newScore':[4,3]},{'newScore':[3,2]},{'homeScore':True},{'newScore':None},{'ownGoal':'true'}]:
            e=self.event();e.update(change)
            with self.assertRaises(ValueError):self.run_event(e)
    def test_shootout_and_non_goal_rejected(self):
        for change in [{'isPenaltyShootoutEvent':True},{'type':'Card'}]:
            e=self.event();e.update(change)
            with self.assertRaises(ValueError):self.run_event(e)

if __name__=='__main__':unittest.main()
