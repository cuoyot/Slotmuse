import copy
import unittest
import server
import test_schools as fixtures

class SchoolCalendarRules(unittest.TestCase):
    def test_closed_week_skips_hours_and_single_day_blocks_sessions(self):
        cat=server.catalogue();cat['season']=dict(name='Curso',start='2026-11-02',end='2026-11-27')
        cat['non_teaching_days']=[dict(start='2026-11-09',end='2026-11-13',name='Vacaciones')]
        sub=dict(id='law',name='Derecho',scope='group',kind='theory',weekly_hours=2,total_hours=6,session_minutes=60,blocks=2,start='2026-11-02',end='2026-11-27');cat['subjects']=[sub]
        self.assertEqual([server.subject_week_minutes(sub,cat,w) for w in server.season_weeks(cat)],[120,0,120,120])
        sessions,missing=server.generate(cat,week='2026-11-09');self.assertEqual(sessions,[]);self.assertEqual(missing,[])
        cat['non_teaching_days']=[dict(start='2026-11-09',end='2026-11-09',name='Festivo')]
        sessions,_=server.generate(cat,week='2026-11-09');self.assertTrue(all(s['day']!=0 for s in sessions))
    def test_explicit_period_ends_and_legacy_rules_remain_compatible(self):
        room=dict(availability_mode='periods',availability=[{'from':'2026-11-09','until':'2026-11-16','windows':[dict(day=0,start='08:00',end='13:00')]}])
        self.assertFalse(server.room_available(room,'2026-11-02',0,0,60))
        self.assertTrue(server.room_available(room,'2026-11-16',0,0,60))
        self.assertFalse(server.room_available(room,'2026-11-23',0,0,60))

class SchoolCalendarAPI(unittest.TestCase):
    setUp=fixtures.SchoolsAPI.setUp
    tearDown=fixtures.SchoolsAPI.tearDown
    call=fixtures.SchoolsAPI.call
    login=fixtures.SchoolsAPI.login
    create=fixtures.SchoolsAPI.create
    state=fixtures.SchoolsAPI.state
    add=fixtures.SchoolsAPI.add
    def prepare(self):
        self.login();self.school=self.create();self.add(self.school,'groups',dict(name='Alfa',size=40,subgroup_count=2));self.add(self.school,'rooms',dict(name='Aula',kind='theory',capacity=50))
        self.add(self.school,'subjects',dict(name='Derecho',kind='theory',scope='group',start='2026-11-09',end='2026-12-04',session_minutes=60,weekly_sessions=2,total_hours=6,color='blue'))
    def test_without_nominal_teacher_can_plan_pending_but_not_publish(self):
        self.prepare();state=self.state(self.school)
        self.assertEqual(state['setup_issues'],[])
        self.assertEqual(self.call('generate',dict(week=state['week'],revision=state['data']['revision']),school=self.school)[0],200)
        state=self.state(self.school);self.assertEqual(len(state['data']['sessions']),2)
        self.assertTrue(all(next(t for t in state['catalog']['teachers'] if t['id']==s['teacher'])['placeholder'] for s in state['data']['sessions']))
        self.assertEqual(self.call('publish',dict(week=state['week'],revision=state['data']['revision']),school=self.school)[0],400)
    def test_holiday_replans_draft_and_explicit_space_periods_preview(self):
        self.prepare();state=self.state(self.school)
        self.call('generate',dict(week=state['week'],revision=state['data']['revision']),school=self.school)
        self.assertEqual(self.add(self.school,'non_teaching_days',dict(name='Vacaciones',start='2026-11-09',end='2026-11-13'))[0],200)
        state=self.state(self.school);self.assertEqual(state['data']['sessions'],[])
        body=dict(room=state['catalog']['rooms'][0]['id'],catalog_revision=state['catalog_revision'],**{'from':'2026-11-16'},until='2026-11-20',replace_from=state['catalog']['season']['start'],windows=[dict(day=0,start='08:00',end='13:00')],apply=True,replan=True)
        status,response=self.call('room-availability',body,school=self.school);self.assertEqual(status,200,response)
        room=self.state(self.school)['catalog']['rooms'][0];self.assertEqual(room['availability_mode'],'periods')
        self.assertEqual(room['availability'][0]['until'],'2026-11-20')
        self.assertFalse(server.room_available(room,'2026-11-23',0,0,60))
    def test_course_configuration_accepts_summer_break_ranges(self):
        self.prepare();state=self.state(self.school)
        status,result=self.call('season',dict(week=state['week'],revision=state['data']['revision'],name='Curso',start='2026-09-14',end='2027-06-25',non_teaching_days=[dict(name='Periodo estival',start='2027-06-01',end='2027-06-25',enabled=True)]),school=self.school)
        self.assertEqual(status,200,result)
        saved=self.state(self.school)['catalog']['non_teaching_days'][0]
        self.assertEqual(saved['name'],'Periodo estival');self.assertEqual(saved['end'],'2027-06-25')
    def test_week_view_is_limited_to_course_dates(self):
        self.prepare();before=self.call('state?week=2020-01-06',school=self.school)[1]
        self.assertEqual(before['week'],'2026-09-14')
        after=self.call('state?week=2030-01-07',school=self.school)[1]
        self.assertEqual(after['week'],'2027-06-21')

    def test_teachers_can_have_no_subject_yet_and_later_receive_specific_ids(self):
        self.prepare();self.assertEqual(self.add(self.school,'teachers',dict(name='Juan',subjects=[],days=[0,2],placeholder=False,max_daily=4,max_weekly=20))[0],200)
        state=self.state(self.school);teacher=next(t for t in state['catalog']['teachers'] if t['name']=='Juan')
        self.assertEqual(self.add(self.school,'teachers',{**teacher,'subjects':[state['catalog']['subjects'][0]['id']]})[0],200)
