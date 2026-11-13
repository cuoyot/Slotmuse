import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import server

WEEK='2026-11-09'

def session(**kw):
    s=dict(id='test',subject='law',audience='A',day=0,slot=0,room='aula1',teacher='t1',locked=False)
    s.update(kw)
    return s

class Constraints(unittest.TestCase):
    def setUp(self): self.cat=server.catalogue()
    def test_group_blocks_child(self):
        sessions=[session(),session(id='practice',subject='defense',audience='A1',room='tatami',teacher='t3')]
        self.assertTrue(any('alumnos' in e for e in server.validate(sessions,self.cat,WEEK)))
    def test_sibling_parallel_is_valid(self):
        sessions=[session(subject='defense',audience='A1',room='tatami',teacher='t3'),session(id='swim',subject='swim',audience='A2',room='pool',teacher='t4')]
        self.assertEqual(server.validate(sessions,self.cat,WEEK),[])
    def test_room_capacity(self):
        self.cat['rooms'][0]['capacity']=24
        self.assertTrue(any('aforo' in e for e in server.validate([session()],self.cat,WEEK)))
    def test_room_kind_and_qualification(self):
        errors=server.validate([session(room='pool',teacher='t3')],self.cat,WEEK)
        self.assertTrue(any('espacio' in e for e in errors))
        self.assertTrue(any('habilitación' in e for e in errors))
    def test_teacher_availability(self):
        self.cat['teachers'][0]['days']=[1]
        self.assertTrue(any('disponible' in e for e in server.validate([session()],self.cat,WEEK)))
    def test_daily_load(self):
        self.assertTrue(any('carga' in e for e in server.validate([session(id=str(i),slot=i) for i in range(4)],self.cat,WEEK)))
    def test_no_shared_placeholder(self):
        self.cat['rooms'].append(dict(id='tatami2',name='Tatami 2',kind='tatami',capacity=24))
        errors=server.validate([session(subject='defense',audience='A1',room='tatami',teacher='p1'),session(id='2',subject='defense',audience='B1',room='tatami2',teacher='p1')],self.cat,WEEK)
        self.assertTrue(any('docente' in e for e in errors))
    def test_generated_schedule_complete_feasible_and_unique(self):
        for seed in range(3):
            result,missing=server.generate(self.cat,seed=seed,week=WEEK)
            self.assertEqual(missing,[])
            self.assertEqual(server.validate(result,self.cat,WEEK),[])
            self.assertEqual(server.coverage(result,self.cat,WEEK),[])
            self.assertEqual(len(set(s['id'] for s in result)),len(result))
    def test_locks_survive(self):
        result,_=server.generate(self.cat,week=WEEK)
        result[-1]['locked']=True
        locked=copy.deepcopy(result[-1])
        new,_=server.generate(self.cat,result,seed=9,week=WEEK)
        self.assertIn(locked,new)
        self.assertEqual(len(new),len(set(s['id'] for s in new)))
    def test_dates_exclude_inactive_subject(self):
        self.cat['subjects'][0].update(start='2027-01-01',end='2027-02-01')
        new,missing=server.generate(self.cat,week=WEEK)
        self.assertFalse(any(s['subject']=='law' for s in new))
        self.assertEqual(missing,[])
        self.assertTrue(any('periodo' in e for e in server.validate([session()],self.cat,WEEK)))
    def test_partial_week_respects_days(self):
        self.cat['subjects'][0].update(start='2026-11-12',end='2026-11-13')
        result,_=server.generate(self.cat,week=WEEK)
        self.assertTrue(all(s['day']>=3 for s in result if s['subject']=='law'))
        self.assertTrue(server.active(self.cat['subjects'][0],WEEK))
    def test_empty_off_term_week(self):
        result,missing=server.generate(self.cat,week='2028-01-03')
        self.assertEqual((result,missing),([],[]))

class API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.old_db=server.DB
        server.DB=Path(cls.temp.name)/'test.db'
        cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.base='http://127.0.0.1:'+str(cls.http.server_port)
        threading.Thread(target=cls.http.serve_forever,daemon=True).start()
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();server.DB=cls.old_db;cls.temp.cleanup()
    def setUp(self):
        with server.connect() as c:
            c.execute('DELETE FROM weeks');c.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(server.catalogue()),))
    def request(self,path='/api/state?week='+WEEK,user='manager',body=None):
        req=Request(self.base+path,headers={'X-Demo-User':user,'Content-Type':'application/json'},data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(req) as r:return r.status,json.load(r)
        except HTTPError as e:return e.code,json.load(e)
    def state(self):return self.request()[1]
    def post(self,action,state=None,user='manager',**kw):
        state=state or self.state()
        return self.request('/api/'+action,user,dict(week=WEEK,revision=state['data']['revision'],**kw))
    def confirm_teachers(self):
        state=self.state()
        for s in state['data']['sessions']:
            if s['teacher']=='p2':
                code,_=self.post('assign',id=s['id'],teacher='t4',user='coordinator')
                self.assertEqual(code,200)
    def test_student_cannot_write_or_view_draft(self):
        status,result=self.request(user='student-a1')
        self.assertEqual(status,200);self.assertEqual(result['data']['sessions'],[])
        self.assertEqual(self.post('generate',user='student-a1')[0],403)
        self.assertEqual(self.post('publish',user='coordinator')[0],403)
    def test_reject_pending_publish(self):self.assertEqual(self.post('publish')[0],400)
    def test_reject_conflict_without_mutating(self):
        before=self.state()
        s=copy.deepcopy(before['data']['sessions'][0]);s['id']=None
        self.assertEqual(self.post('save',state=before,session=s)[0],400)
        self.assertEqual(self.state()['data']['revision'],before['data']['revision'])
        self.assertEqual(len(self.state()['data']['sessions']),24)
    def test_concurrent_revision(self):
        old=self.state()
        self.assertEqual(self.post('generate',state=old)[0],200)
        self.assertEqual(self.post('generate',state=old)[0],409)
    def test_published_snapshot_is_stable_and_filtered(self):
        self.confirm_teachers();self.assertEqual(self.post('publish')[0],200)
        student=self.request(user='student-a1')[1]
        self.assertEqual(len(student['data']['sessions']),8)
        self.assertTrue(all(s['audience'] in ['A','A1'] for s in student['data']['sessions']))
        s=self.state()['data']['sessions'][0]
        self.assertEqual(self.post('delete',id=s['id'])[0],200)
        self.assertEqual(self.request(user='student-a1')[1]['data']['sessions'],student['data']['sessions'])
        self.assertEqual(self.post('publish')[0],400)
    def test_period_change_prunes_all_drafts_preserves_publication(self):
        self.confirm_teachers();self.assertEqual(self.post('publish')[0],200)
        self.request('/api/state?week=2026-11-16')
        before=self.request(user='student-a1')[1]['data']['sessions']
        self.assertEqual(self.post('subject',id='law',start='2027-01-01',end='2027-02-26',blocks=2)[0],200)
        for week in [WEEK,'2026-11-16']:
            result=self.request('/api/state?week='+week)[1]
            self.assertFalse(any(s['subject']=='law' for s in result['data']['sessions']))
        self.assertEqual(self.request(user='student-a1')[1]['data']['sessions'],before)
    def test_invalid_period_rolls_back(self):
        self.assertEqual(self.post('subject',id='law',start='2027-02-01',end='2027-01-01',blocks=2)[0],400)
        self.assertEqual(self.state()['catalog']['subjects'][0]['start'],'2026-09-14')
    def test_season_generation_preserves_existing_weeks(self):
        before=self.state()
        self.assertEqual(self.post('season',name='Temporada breve',start='2026-11-09',end='2026-11-27')[0],200)
        current=self.state()['data']
        self.assertEqual(self.post('season-generate')[0],200)
        after=self.state()
        self.assertEqual(len(after['season_summary']),3)
        self.assertTrue(all(r['status']=='draft' for r in after['season_summary']))
        self.assertEqual(after['data']['sessions'],current['sessions'])
        revisions=after['data']['revision']
        self.assertEqual(self.post('season-generate')[0],200)
        self.assertEqual(len(self.state()['season_summary']),3)
    def test_season_generation_permission_and_bounds(self):
        self.assertEqual(self.post('season-generate',user='coordinator')[0],403)
        self.assertEqual(self.post('season',name='Mal',start='2027-01-01',end='2026-01-01')[0],400)
    def test_season_demands_change_after_subject_ends(self):
        summary=self.state()['season_summary']
        self.assertEqual(next(r['required'] for r in summary if r['week']=='2026-12-14'),24)
        self.assertEqual(next(r['required'] for r in summary if r['week']=='2026-12-21'),20)

    def test_health_checks_database_without_creating_schedules(self):
        status,body=self.request('/api/health')
        self.assertEqual((status,body),(200,{'status':'ok'}))
        with server.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM weeks').fetchone()[0],0)

    def test_health_detects_database_failure(self):
        from unittest.mock import patch
        with patch.object(server,'DB',Path(self.temp.name)/'missing'/'missing.db'):
            self.assertEqual(self.request('/api/health')[0],503)

    def test_persistence(self):
        self.post('generate')
        with server.connect() as c:self.assertEqual(server.load_week(c,WEEK,server.get_catalog(c))['revision'],2)

if __name__=='__main__':unittest.main()
