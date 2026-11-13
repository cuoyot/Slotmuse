import copy
import unittest
import server

class SubjectHours(unittest.TestCase):
    def catalog(self):
        cat=server.catalogue();cat['season']=dict(start='2026-11-02',end='2026-11-27',name='Curso')
        cat['subjects']=[dict(id='law',name='Derecho',start='2026-11-02',end='2026-11-27',scope='group',blocks=2,weekly_hours=3,total_hours=7.5,kind='theory',color='blue')]
        return cat
    def test_total_distributed_and_last_week_reduced(self):
        cat=self.catalog();sub=cat['subjects'][0]
        self.assertEqual([server.subject_blocks(sub,cat,w) for w in server.season_weeks(cat)],[2,2,1,0])
        self.assertEqual(sum(len(server.requirements(cat,w)) for w in server.season_weeks(cat)),10)
    def test_hours_validation(self):
        cat=self.catalog()
        for total,weekly in [(0,3),(2.25,3),(3,2.25),(True,3),(3,float('inf'))]:
            with self.assertRaises(ValueError):server.subject_hours(dict(total_hours=total,weekly_hours=weekly),cat)
        self.assertEqual(server.subject_hours(dict(total_hours=7.5,weekly_hours=3),cat)['blocks'],2)
    def test_outside_period_and_each_subgroup_receives_total(self):
        cat=self.catalog();sub=cat['subjects'][0];sub['scope']='subgroup'
        self.assertEqual(server.requirements(cat,'2026-10-26'),[])
        pairs=[pair for w in server.season_weeks(cat) for pair in server.requirements(cat,w)]
        for group in cat['groups']:
            for subgroup in group['subgroups']:self.assertEqual(pairs.count(('law',subgroup))*1.5,7.5)
    def test_manual_sessions_cannot_exceed_final_week_quota(self):
        cat=self.catalog();sessions=server.generate(cat,week='2026-11-16')[0]
        self.assertEqual(len(sessions),2)
        duplicate=copy.deepcopy(sessions[0]);duplicate['id']='extra';duplicate['day']=(duplicate['day']+1)%5
        self.assertTrue(any('carga horaria' in e for e in server.validate(sessions+[duplicate],cat,'2026-11-16')))
