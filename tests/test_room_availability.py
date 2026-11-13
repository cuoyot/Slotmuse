import copy
import json
import unittest
import server
import school_app as app
import test_schools as fixtures

class AvailabilityRules(unittest.TestCase):
    def test_full_session_must_fit_and_effective_date_preserves_past(self):
        room=dict(availability=[{'from':'2026-11-09','windows':[dict(day=d,start='08:00',end='13:00') for d in [0,2]]}])
        self.assertTrue(server.room_available(room,'2026-11-02',1,3))
        self.assertTrue(server.room_available(room,'2026-11-09',0,0))
        self.assertTrue(server.room_available(room,'2026-11-09',2,1))
        self.assertFalse(server.room_available(room,'2026-11-09',0,8))
        self.assertFalse(server.room_available(room,'2026-11-09',1,0))
        room['availability'].append({'from':'2026-11-11','windows':[]})
        self.assertTrue(server.room_available(room,'2026-11-09',0,0))
        self.assertFalse(server.room_available(room,'2026-11-09',2,0))
    def test_generator_obeys_windows_and_closed_rooms_remain_incomplete(self):
        cat=server.catalogue();cat['rooms']=[dict(id='pool',name='Piscina',kind='pool',capacity=24,availability=[{'from':'2026-09-01','windows':[dict(day=d,start='08:00',end='13:00') for d in [0,2]]}])]
        cat['subjects']=[s for s in cat['subjects'] if s['id']=='swim']
        sessions,missing=server.generate(cat,week='2026-11-09')
        self.assertEqual(missing,[])
        self.assertTrue(all(s['day'] in [0,2] and server.session_start(s)+server.session_duration(s)<=780 for s in sessions))
        cat['rooms'][0]['availability'][0]['windows']=[]
        self.assertTrue(server.validate(sessions,cat,'2026-11-09'))
        self.assertEqual(len(server.generate(cat,week='2026-11-09')[1]),4)

class AvailabilityAPI(unittest.TestCase):
    setUp=fixtures.SchoolsAPI.setUp
    tearDown=fixtures.SchoolsAPI.tearDown
    call=fixtures.SchoolsAPI.call
    login=fixtures.SchoolsAPI.login
    create=fixtures.SchoolsAPI.create
    state=fixtures.SchoolsAPI.state
    add=fixtures.SchoolsAPI.add
    def prepare(self):
        self.login();self.school=self.create()
        self.add(self.school,'rooms',dict(name='Aula',kind='theory',capacity=60))
        self.add(self.school,'groups',dict(name='Grupo',size=48,subgroup_count=2))
        self.add(self.school,'subjects',dict(name='Derecho',kind='theory',scope='group',start='2026-11-02',end='2026-11-27',weekly_hours=3,total_hours=12,color='blue'))
        state=self.state(self.school);sid=state['catalog']['subjects'][0]['id']
        self.add(self.school,'teachers',dict(name='Docente',subjects=[sid],days=[0,1,2,3,4],max_daily=3,max_weekly=12))
        for week in ['2026-11-02','2026-11-09']:
            state=self.call('state?week='+week,school=self.school)[1]
            self.assertEqual(self.call('generate',dict(week=week,revision=state['data']['revision']),school=self.school)[0],200)
        state=self.state(self.school)
        self.assertEqual(self.call('publish',dict(week=state['week'],revision=state['data']['revision']),school=self.school)[0],200)
    def payload(self,**extra):
        state=self.state(self.school)
        return dict(room=state['catalog']['rooms'][0]['id'],catalog_revision=state['catalog_revision'],**{'from':'2026-11-09'},windows=[dict(day=4,start='13:30',end='15:00'),dict(day=3,start='13:30',end='15:00')],**extra)
    def test_preview_repair_preserves_earlier_weeks_and_published_snapshot(self):
        self.prepare();before=self.state(self.school);old=self.call('state?week=2026-11-02',school=self.school)[1]['data']
        body=self.payload();status,preview=self.call('room-availability',body,school=self.school)
        self.assertEqual(status,200,preview);self.assertTrue(preview['impact'])
        self.assertNotIn('availability',self.state(self.school)['catalog']['rooms'][0])
        status,result=self.call('room-availability',{**body,'apply':True,'replan':True},school=self.school)
        self.assertEqual(status,200,result);self.assertEqual(result['missing'],0)
        after=self.state(self.school)
        self.assertEqual(after['data']['published'],before['data']['published'])
        self.assertEqual(self.call('state?week=2026-11-02',school=self.school)[1]['data'],old)
        self.assertTrue(all(s['day'] in [3,4] and s['slot']==11 for s in after['data']['sessions']))
        self.assertTrue(after['resource_alerts'][0]['published'])
        self.assertEqual(self.call('room-availability',{**body,'apply':True},school=self.school)[0],409)
    def test_preview_is_invalidated_by_another_week_edit(self):
        self.prepare();body=self.payload()
        preview=self.call('room-availability',body,school=self.school)[1]
        state=self.state(self.school);session=state['data']['sessions'][0]
        self.assertEqual(self.call('assign',dict(id=session['id'],teacher=session['teacher'],week=state['week'],revision=state['data']['revision']),school=self.school)[0],200)
        self.assertEqual(self.call('room-availability',{**body,'preview_token':preview['preview_token'],'apply':True,'replan':True},school=self.school)[0],409)

    def test_closure_saves_partial_draft_and_blocks_publication(self):
        self.prepare();body={**self.payload(),'windows':[],'apply':True,'replan':True}
        status,data=self.call('room-availability',body,school=self.school)
        self.assertEqual(status,200,data);self.assertEqual(data['missing'],2)
        state=self.state(self.school);self.assertEqual(state['data']['sessions'],[])
        self.assertEqual(self.call('publish',dict(week=state['week'],revision=state['data']['revision']),school=self.school)[0],400)
    def test_batch_is_atomic_and_single_revision(self):
        self.login();school=self.create();state=self.state(school)
        body=dict(collection='groups',catalog_revision=state['catalog_revision'],items=[dict(name='Uno',size=48,subgroup_count=2),dict(name='Dos',size=1,subgroup_count=2)])
        self.assertEqual(self.call('catalog',body,school=school)[0],400)
        self.assertEqual(self.state(school)['catalog']['groups'],[])
        body['items'][1]['size']=48
        self.assertEqual(self.call('catalog',body,school=school)[0],200)
        after=self.state(school);self.assertEqual(len(after['catalog']['groups']),2)
        self.assertEqual(after['catalog_revision'],state['catalog_revision']+1)
    def test_school_can_add_locations_and_custom_space_types(self):
        self.login();school=self.create();state=self.state(school)
        self.assertEqual(len(state['catalog']['space_types']),4)
        self.assertEqual(self.add(school,'locations',dict(name='Centro de tiro'))[0],200)
        self.assertEqual(self.add(school,'space_types',dict(name='Galería de tiro'))[0],200)
        state=self.state(school);kind=next(t for t in state['catalog']['space_types'] if t['name']=='Galería de tiro')
        location=next(l for l in state['catalog']['locations'] if l['name']=='Centro de tiro')
        self.assertFalse(kind['builtin'])
        self.assertEqual(self.add(school,'rooms',dict(name='Galería 1',kind=kind['id'],capacity=24,location_id=location['id']))[0],200)
        self.assertEqual(self.add(school,'subjects',dict(name='Tiro',kind=kind['id'],scope='subgroup',weekly_hours=1,total_hours=4,session_minutes=60,start='2026-11-09',end='2026-12-04',color='blue'))[0],200)
        other=self.create('Otra escuela');self.assertNotIn(kind['id'],[t['id'] for t in self.state(other)['catalog']['space_types']])
        self.assertEqual(self.add(other,'rooms',dict(name='Ajena',kind=kind['id'],capacity=24))[0],400)
        self.assertEqual(self.add(school,'locations',dict(name='centro de tiro'))[0],400)

    def test_permissions_and_invalid_windows(self):
        self.prepare();body=self.payload()
        for windows in [[dict(day=5,start='08:00',end='13:00')],[dict(day=0,start='13:00',end='08:00')],[dict(day=0,start='8:00',end='13:00')]]:
            self.assertEqual(self.call('room-availability',{**body,'windows':windows},school=self.school)[0],400)
        self.assertEqual(self.call('users',dict(name='Coord',username='coord',password='long-password-2026',role='coordinator'),school=self.school)[0],200)
        self.cookie='';self.csrf='';self.call('login',dict(username='coord',password='long-password-2026'));self.csrf=self.call('me')[1]['csrf']
        self.assertEqual(self.call('room-availability',body,school=self.school)[0],403)
