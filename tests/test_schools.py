import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server
import school_app as app


class SchoolsAPI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=(app.DATA_ROOT,app.AUTH_DB,server.DB)
        app.DATA_ROOT=Path(self.temp.name)
        app.AUTH_DB=app.DATA_ROOT/'platform.db'
        server.DB=app.DATA_ROOT/'legacy.db'
        self.environment=patch.dict(os.environ,{'ADMIN_USERNAME':'superadmin','ADMIN_PASSWORD':'test-password-for-school-2026'})
        self.environment.start();app.initialize()
        self.http=server.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        threading.Thread(target=self.http.serve_forever,daemon=True).start()
        self.base='http://127.0.0.1:'+str(self.http.server_port)
        self.cookie='';self.csrf=''
    def tearDown(self):
        self.http.shutdown();self.http.server_close()
        app.DATA_ROOT,app.AUTH_DB,server.DB=self.old
        self.environment.stop();self.temp.cleanup()
    def call(self,path,body=None,school='demo',headers=None):
        hdr={'X-School-ID':school,'X-Demo-User':'manager','Content-Type':'application/json'}
        if self.cookie:hdr['Cookie']=self.cookie
        if self.csrf:hdr['X-CSRF-Token']=self.csrf
        hdr.update(headers or {})
        req=Request(self.base+'/api/'+path,data=json.dumps(body).encode() if body is not None else None,headers=hdr)
        try:
            with urlopen(req,timeout=15) as response:
                if response.headers.get('Set-Cookie'):self.cookie=response.headers['Set-Cookie'].split(';')[0]
                return response.status,json.load(response)
        except HTTPError as error:return error.code,json.load(error)
    def login(self):
        status,_=self.call('login',{'username':'superadmin','password':'test-password-for-school-2026'})
        self.assertEqual(status,200)
        self.csrf=self.call('me')[1]['csrf']
    def create(self,name='Escuela Real'):
        status,data=self.call('schools',dict(name=name,season_name='Curso 2026/27',start='2026-09-14',end='2027-06-25'))
        self.assertEqual(status,201,data)
        return data['school']['id']
    def state(self,school):return self.call('state?week=2026-11-09',school=school)[1]
    def add(self,school,collection,item):
        state=self.state(school)
        return self.call('catalog',dict(collection=collection,item=item,catalog_revision=state['catalog_revision']),school=school)
    def test_password_hashed_and_no_plaintext_database(self):
        with app.platform_db() as conn:
            row=conn.execute('SELECT * FROM users').fetchone()
            self.assertEqual(len(row['password']),32)
            self.assertNotIn(b'test-password',row['password'])
        self.assertFalse((app.DATA_ROOT/'admin-inicial.txt').exists())
    def test_login_cookie_csrf_and_logout(self):
        self.login();self.assertTrue(self.cookie.startswith('academia_session='))
        self.assertEqual(self.call('me')[1]['user']['role'],'superadmin')
        self.csrf='wrong'
        self.assertEqual(self.call('schools',{})[0],403)
        self.csrf=self.call('me')[1]['csrf']
        self.assertEqual(self.call('logout',{})[0],200)
        self.assertIsNone(self.call('me')[1]['user'])
    def test_login_rate_limit(self):
        for _ in range(5):self.assertEqual(self.call('login',{'username':'superadmin','password':'wrong'})[0],401)
        self.assertEqual(self.call('login',{'username':'superadmin','password':'test-password-for-school-2026'})[0],429)
    def test_demo_cannot_enumerate_real_schools_or_spoof_admin(self):
        self.login();school=self.create()
        self.cookie='';self.csrf=''
        self.assertEqual([s['id'] for s in self.call('me')[1]['schools']],['demo'])
        self.assertEqual(self.call('state',school=school,headers={'X-Demo-User':'manager'})[0],401)
        self.assertEqual(self.call('schools',{})[0],401)
        self.assertEqual(self.call('catalog',{},school=school)[0],401)
    def test_schools_start_empty_and_are_isolated(self):
        self.login();a=self.create('Escuela A');b=self.create('Escuela B')
        for school in [a,b]:
            state=self.state(school)
            self.assertEqual(state['catalog']['subjects'],[])
            self.assertEqual(state['catalog']['groups'],[])
            self.assertEqual(state['data']['sessions'],[])
        self.assertEqual(self.add(a,'rooms',dict(name='Aula exclusiva',kind='theory',capacity=60))[0],200)
        self.assertEqual(self.state(b)['catalog']['rooms'],[])
        self.assertNotEqual(self.state('demo')['catalog']['rooms'],self.state(a)['catalog']['rooms'])
    def test_concurrent_school_requests_do_not_share_database(self):
        self.login();a=self.create('Escuela A');b=self.create('Escuela B')
        self.add(a,'rooms',dict(name='Solo A',kind='theory',capacity=60))
        results=[]
        def read(school):results.append((school,self.state(school)['catalog']['rooms']))
        threads=[threading.Thread(target=read,args=(s,)) for s in [a,b]*4]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(len(results),8)
        for school,rooms in results:self.assertEqual(len(rooms),1 if school==a else 0)
    def test_complete_school_setup_and_generate(self):
        self.login();school=self.create()
        self.assertEqual(self.add(school,'rooms',dict(name='Aula 1',kind='theory',capacity=60))[0],200)
        self.assertEqual(self.add(school,'groups',dict(name='Grupo Alfa',size=48,subgroup_count=2))[0],200)
        self.assertEqual(self.add(school,'subjects',dict(name='Derecho',kind='theory',scope='group',start='2026-10-01',end='2026-12-18',blocks=2,color='blue'))[0],200)
        state=self.state(school);sub=state['catalog']['subjects'][0]['id']
        self.assertEqual(self.add(school,'teachers',dict(name='Docente Real',subjects=[sub],days=[0,1,2,3,4],placeholder=False,max_daily=3,max_weekly=12))[0],200)
        state=self.state(school)
        self.assertEqual(state['setup_issues'],[])
        status,_=self.call('generate',dict(week=state['week'],revision=state['data']['revision']),school=school)
        self.assertEqual(status,200)
        state=self.state(school)
        self.assertEqual(len(state['data']['sessions']),2)
        self.assertEqual(state['user']['role'],'superadmin')
        status,_=self.call('publish',dict(week=state['week'],revision=state['data']['revision']),school=school)
        self.assertEqual(status,200)
    def test_incomplete_setup_blocks_generation(self):
        self.login();school=self.create();state=self.state(school)
        self.assertEqual(self.call('generate',dict(week=state['week'],revision=state['data']['revision']),school=school)[0],400)
    def test_reject_foreign_subject_assignment(self):
        self.login();school=self.create()
        status,_=self.add(school,'teachers',dict(name='Docente',subjects=['law'],days=[0],max_daily=3,max_weekly=12))
        self.assertEqual(status,400)
    def test_cross_origin_mutation_rejected(self):
        self.assertEqual(self.call('login',{'username':'superadmin','password':'wrong'},headers={'Origin':'https://attacker.example'})[0],403)
    def test_session_expiry(self):
        self.login()
        with app.platform_db() as conn:conn.execute('UPDATE sessions SET expires=?',(time.time()-1,))
        self.assertIsNone(self.call('me')[1]['user'])
    def test_existing_demo_migration_preserves_original(self):
        self.assertEqual(self.state('demo')['school']['is_demo'],1)
        self.assertTrue(app.school_path('demo').exists())

if __name__=='__main__':unittest.main()
