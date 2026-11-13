import json
import unittest
import test_schools as fixtures
import school_app as app
import server

class RolesAPI(unittest.TestCase):
    setUp=fixtures.SchoolsAPI.setUp
    tearDown=fixtures.SchoolsAPI.tearDown
    call=fixtures.SchoolsAPI.call
    login=fixtures.SchoolsAPI.login
    create=fixtures.SchoolsAPI.create
    state=fixtures.SchoolsAPI.state
    add=fixtures.SchoolsAPI.add
    def prepare(self):
        self.login();self.root=(self.cookie,self.csrf);self.school=self.create()
        self.add(self.school,'groups',dict(name='Alfa',size=48,subgroup_count=2))
        self.add(self.school,'groups',dict(name='Bravo',size=48,subgroup_count=2))
        self.add(self.school,'rooms',dict(name='Aula',kind='theory',capacity=60))
        self.add(self.school,'subjects',dict(name='Derecho',kind='theory',scope='group',start='2026-11-09',end='2026-11-27',blocks=1,color='blue'))
        sub=self.state(self.school)['catalog']['subjects'][0]['id']
        self.add(self.school,'teachers',dict(name='Docente',subjects=[sub],days=[0,1,2,3,4],max_daily=3,max_weekly=12))
        self.cat=self.state(self.school)['catalog']
    def account(self,role,username=None,**extra):
        body=dict(name='Persona '+role,username=username or role,password='test-person-password-2026',role=role,**extra)
        status,result=self.call('users',body,school=self.school);self.assertEqual(status,200,result)
        return result['id']
    def enter(self,username):
        self.cookie='';self.csrf=''
        self.assertEqual(self.call('login',dict(username=username,password='test-person-password-2026'))[0],200)
        self.csrf=self.call('me')[1]['csrf']
    def root_session(self):self.cookie,self.csrf=self.root
    def test_personal_calendars_only_published_and_own(self):
        self.prepare();group=self.cat['groups'][0]
        self.account('student',subgroup=group['subgroups'][0]);self.account('teacher',teacher_id=self.cat['teachers'][0]['id'])
        state=self.state(self.school)
        self.assertEqual(self.call('generate',dict(week=state['week'],revision=state['data']['revision']),school=self.school)[0],200)
        self.enter('student');self.assertEqual(self.state(self.school)['data']['sessions'],[])
        for route in ['generate','publish','assign','save','teacher','season','users','catalog']:
            self.assertEqual(self.call(route,{},school=self.school)[0],403,route)
        self.assertEqual(self.call('users',school=self.school)[0],403)
        self.root_session();state=self.state(self.school)
        self.assertEqual(self.call('publish',dict(week=state['week'],revision=state['data']['revision']),school=self.school)[0],200)
        self.enter('student');data=self.state(self.school)
        self.assertEqual(len(data['data']['sessions']),1)
        self.assertEqual(data['data']['sessions'][0]['audience'],group['id'])
        self.assertNotIn('history',data['data']);self.assertNotIn('subjects',data['catalog']['teachers'][0])
        self.assertEqual(data['user']['role'],'student');self.assertTrue(data['permissions']['personal'])
        self.enter('teacher');data=self.state(self.school)
        self.assertEqual(len(data['data']['sessions']),2)
        self.assertTrue(all(s['teacher']==self.cat['teachers'][0]['id'] for s in data['data']['sessions']))
        self.assertEqual(self.call('publish',{},school=self.school)[0],403)
    def test_school_admin_scope_and_escalation(self):
        self.prepare();other=self.create('Otra escuela')
        self.account('school_admin');self.enter('school_admin')
        self.assertEqual([s['id'] for s in self.call('me')[1]['schools'] if not s['is_demo']],[self.school])
        self.assertEqual(self.call('state',school=other)[0],404)
        self.assertEqual(self.call('users',school=other)[0],404)
        self.assertEqual(self.call('schools',{})[0],403)
        self.assertEqual(self.call('users',dict(name='Escalada',username='evil',password='long-password-test',role='superadmin'),school=self.school)[0],400)
        self.account('coordinator');users=self.call('users',school=self.school)[1]['users']
        self.assertEqual(len(users),2);self.assertNotIn('password',json.dumps(users))
        own=next(u for u in users if u['role']=='school_admin')
        self.assertEqual(self.call('users',{**own,'active':False},school=self.school)[0],400)
    def test_coordinator_only_assignment_and_revocation(self):
        self.prepare();uid=self.account('coordinator');self.enter('coordinator')
        self.assertEqual(self.state(self.school)['user']['role'],'coordinator')
        for route in ['users','catalog','publish','generate','season','save']:
            self.assertEqual(self.call(route,{},school=self.school)[0],403,route)
        self.assertNotEqual(self.call('assign',{},school=self.school)[0],403)
        coordinator=(self.cookie,self.csrf);self.root_session()
        self.assertEqual(self.call('users',dict(id=uid,name='Coord',role='coordinator',active=False),school=self.school)[0],200)
        self.cookie,self.csrf=coordinator
        self.assertEqual(self.call('state',school=self.school)[0],404)
    def test_bindings_uniqueness_and_password_policy(self):
        self.prepare()
        self.assertEqual(self.call('users',dict(name='Alumno',username='a',password='long-password-test',role='student',subgroup='foreign'),school=self.school)[0],400)
        self.assertEqual(self.call('users',dict(name='Coord',username='a',password='short',role='coordinator'),school=self.school)[0],400)
        self.account('teacher',teacher_id=self.cat['teachers'][0]['id'])
        self.assertEqual(self.call('users',dict(name='Otro',username='other',password='long-password-test',role='teacher',teacher_id=self.cat['teachers'][0]['id']),school=self.school)[0],400)
        self.assertEqual(len(self.call('users',school=self.school)[1]['users']),1)

if __name__=='__main__':unittest.main()
