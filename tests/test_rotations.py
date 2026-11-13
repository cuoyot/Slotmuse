import unittest
import server

class Rotations(unittest.TestCase):
    def catalog(self):
        cat=server.catalogue();cat['season']=dict(start='2026-11-09',end='2026-11-13',name='Curso')
        cat['rooms']=[dict(id='pool',name='Piscina',kind='pool',capacity=24,location_id='sports',availability=[{'from':'2026-11-09','windows':[dict(day=d,start='08:00',end='13:00') for d in [0,2]]}]),dict(id='track',name='Pista',kind='outdoor',capacity=24,location_id='sports')]
        cat['subjects']=[dict(id=sid,name=sid,kind=kind,scope='subgroup',blocks=1,weekly_hours=1,total_hours=1,session_minutes=60,start='2026-11-09',end='2026-11-13') for sid,kind in [('swim','pool'),('fitness','outdoor')]]
        return cat
    def test_pool_and_track_rotate_subgroups_at_same_location_and_time(self):
        cat=self.catalog();sessions,missing=server.generate(cat,week='2026-11-09')
        self.assertEqual(missing,[]);self.assertEqual(len(sessions),8)
        splits={s['split_id'] for s in sessions};self.assertEqual(len(splits),4)
        for split in splits:
            children=[s for s in sessions if s['split_id']==split]
            self.assertEqual(len(children),2)
            self.assertEqual({s['subject'] for s in children},{'swim','fitness'})
            self.assertEqual(len({(s['day'],server.session_start(s),s['duration_minutes']) for s in children}),1)
            self.assertEqual(len({s['audience'][0] for s in children}),1)
        self.assertEqual(server.validate(sessions,cat,'2026-11-09'),[])
        changed=[dict(s) for s in sessions];changed[0]['slot']+=1;changed[0]['start_minute']+=30
        self.assertTrue(any('desdoblamiento' in e.lower() for e in server.validate(changed,cat,'2026-11-09')))
    def test_capacity_automatically_selects_subgroups_and_rotations(self):
        cat=self.catalog()
        for subject in cat['subjects']:subject['scope']='auto'
        sessions,missing=server.generate(cat,week='2026-11-09');self.assertEqual(missing,[])
        self.assertTrue(all(s.get('split_id') for s in sessions))
        self.assertTrue(all(s['audience'] in ['A1','A2','B1','B2'] for s in sessions))
        cat['rooms'][0]['capacity']=60
        self.assertEqual(server.audiences_for(cat['subjects'][0],cat['groups'][0],cat,'2026-11-09'),['A'])

    def test_unavailable_member_marks_entire_split_for_replanning(self):
        cat=self.catalog();sessions,_=server.generate(cat,week='2026-11-09')
        cat['rooms'][0]['availability'][0]['windows']=[]
        conflicts=server.room_conflicts(sessions,cat,'2026-11-09')
        self.assertEqual(len(conflicts),8)
