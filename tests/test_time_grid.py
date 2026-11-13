import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import server

class TimeGrid(unittest.TestCase):
    def catalog(self):
        cat=server.catalogue();cat['season']=dict(start='2026-11-09',end='2026-11-13',name='Curso')
        cat['locations']=[dict(id='main',name='Campus'),dict(id='remote',name='Piscina municipal')]
        cat['rooms']=[dict(id='aula1',name='Aula',kind='theory',capacity=60,location_id='main'),dict(id='aula2',name='Aula externa',kind='theory',capacity=60,location_id='remote')]
        cat['subjects']=[dict(id='law',name='Derecho',kind='theory',scope='group',blocks=2,weekly_hours=2.5,total_hours=2.5,session_minutes=60,start='2026-11-09',end='2026-11-13')]
        return cat
    def session(self,**extra):
        return dict(id='one',subject='law',audience='A',day=0,slot=0,room='aula1',teacher='t1',locked=False,duration_minutes=60,**extra)
    def test_generation_uses_30_minute_grid_and_remaining_short_session(self):
        cat=self.catalog();sessions,missing=server.generate(cat,week='2026-11-09')
        self.assertEqual(missing,[])
        for group in ['A','B']:
            own=[s for s in sessions if s['audience']==group]
            self.assertEqual(sorted(s['duration_minutes'] for s in own),[30,60,60])
            self.assertEqual(sum(s['duration_minutes'] for s in own),150)
        self.assertTrue(all(server.session_start(s)%30==0 for s in sessions))
    def test_overlap_and_thirty_minute_travel_for_students_and_teachers(self):
        cat=self.catalog();a=self.session();b={**a,'id':'two','slot':1,'room':'aula2'}
        self.assertTrue(any('simultánea' in e or 'cruce' in e for e in server.validate([a,b],cat)))
        b['slot']=2
        self.assertTrue(any('desplazarse' in e for e in server.validate([a,b],cat)))
        b['slot']=3
        self.assertEqual(server.validate([a,b],cat),[])
        b.update(audience='B',slot=2)
        self.assertTrue(any('desplazarse' in e for e in server.validate([a,b],cat)))
        cat['teachers'].append({**cat['teachers'][0],'id':'other'})
        b['teacher']='other';b['audience']='A'
        self.assertTrue(any('desplazarse' in e for e in server.validate([a,b],cat)))
        b['audience']='B'
        self.assertEqual(server.validate([a,b],cat),[])
    def test_room_window_checks_real_session_end(self):
        room=dict(availability=[{'from':'2026-11-09','windows':[dict(day=0,start='08:00',end='13:00')]}])
        self.assertTrue(server.room_available(room,'2026-11-09',0,8,60))
        self.assertFalse(server.room_available(room,'2026-11-09',0,9,60))
    def test_half_hour_hours_and_duration_validation(self):
        self.assertEqual(server.subject_hours(dict(weekly_hours=2.5,total_hours=10.5,session_minutes=60))['session_minutes'],60)
        for duration in [0,45,61,True,450]:
            with self.assertRaises(ValueError):server.subject_hours(dict(weekly_hours=2,total_hours=10,session_minutes=duration))
    def test_weekly_sessions_derive_hours_and_required_weeks(self):
        value=server.subject_hours(dict(session_minutes=60,weekly_sessions=3,total_hours=30))
        self.assertEqual(value['weekly_hours'],3);self.assertEqual(value['required_weeks'],10)
        value=server.subject_hours(dict(session_minutes=90,weekly_sessions=2,total_hours=31.5))
        self.assertEqual(value['weekly_hours'],3);self.assertEqual(value['required_weeks'],11)
        for count in [0,1.5,True,71]:
            with self.assertRaises(ValueError):server.subject_hours(dict(session_minutes=60,weekly_sessions=count,total_hours=30))

    def test_subject_can_require_one_specific_room(self):
        cat=self.catalog();cat['subjects'][0]['room_id']='aula2'
        sessions,missing=server.generate(cat,week='2026-11-09');self.assertEqual(missing,[])
        self.assertTrue(all(s['room']=='aula2' for s in sessions))
        sessions[0]['room']='aula1'
        self.assertTrue(any('espacio concreto' in e for e in server.validate(sessions,cat,'2026-11-09')))

    def test_room_affinity_prefers_reusing_the_same_room_when_feasible(self):
        cat=self.catalog();sessions,missing=server.generate(cat,week='2026-11-09')
        self.assertEqual(missing,[])
        for group in cat['groups']:
            self.assertEqual(len({s['room'] for s in sessions if s['audience']==group['id']}),1)
        assigned=[s for s in sessions if s['audience']=='A'];same=dict(assigned[0]);other={**same,'room':'aula2' if same['room']=='aula1' else 'aula1'}
        self.assertLess(server.room_affinity_cost([same],assigned,cat),server.room_affinity_cost([other],assigned,cat))

    def test_legacy_migration_preserves_published_clock_times(self):
        with tempfile.TemporaryDirectory() as root,patch.object(server,'DB',Path(root)/'old.db'):
            with server.connect() as conn:
                cat=server.catalogue();session=self.session();session.pop('duration_minutes');session['slot']=1
                data=dict(sessions=[session],published=dict(sessions=[copy.deepcopy(session)],at='old'),history=[],revision=4)
                conn.execute('INSERT INTO weeks VALUES (?,?)',('2026-11-09',json.dumps(data)))
                migrated=server.get_catalog(conn)
                updated=json.loads(conn.execute('SELECT data FROM weeks').fetchone()[0])
                for current in [updated['sessions'][0],updated['published']['sessions'][0]]:
                    self.assertEqual(current['start_minute'],585);self.assertEqual(current['duration_minutes'],90)
                self.assertEqual(updated['revision'],4)
                self.assertEqual(migrated['time_grid_version'],2)
