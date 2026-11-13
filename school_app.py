#!/usr/bin/env python3
"""Plataforma de escuelas y autenticación para el planificador."""
from contextlib import contextmanager
import datetime as dt
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlparse, parse_qs

import server

DATA_ROOT = Path(os.environ.get('APP_DATA_DIR', str(server.DB.parent)))
AUTH_DB = DATA_ROOT / 'platform.db'
SESSION_TTL = 12 * 60 * 60
PASSWORD_ROUNDS = 600_000
KINDS = {'theory','tatami','pool','outdoor'}
COLORS = {'blue','violet','orange','teal','green'}


@contextmanager
def platform_db():
    connection = sqlite3.connect(AUTH_DB, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PASSWORD_ROUNDS)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def school_path(school_id):
    if school_id != 'demo' and (len(school_id) != 24 or any(c not in '0123456789abcdef' for c in school_id)):
        raise ValueError('Escuela no válida.')
    return DATA_ROOT / 'schools' / (school_id + '.db')


@contextmanager
def school_db(school_id):
    token = server.CURRENT_DB.set(school_path(school_id))
    try:
        with server.connect() as connection:
            yield connection
    finally:
        server.CURRENT_DB.reset(token)


def initialize():
    DATA_ROOT.mkdir(parents=True,exist_ok=True)
    (DATA_ROOT/'schools').mkdir(exist_ok=True)
    with platform_db() as conn:
        conn.executescript('''
          CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE, salt BLOB NOT NULL, password BLOB NOT NULL, role TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS schools (id TEXT PRIMARY KEY, name TEXT NOT NULL, is_demo INTEGER NOT NULL, created_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS login_attempts (identity TEXT PRIMARY KEY, failures INTEGER NOT NULL, reset REAL NOT NULL);
        ''')
        if not conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            username=os.environ.get('ADMIN_USERNAME','admin').strip()
            password=os.environ.get('ADMIN_PASSWORD','')
            generated=False
            if not password:
                if os.environ.get('RAILWAY_ENVIRONMENT_ID'):
                    raise RuntimeError('Configura ADMIN_USERNAME y ADMIN_PASSWORD antes de iniciar Railway.')
                password=secrets.token_urlsafe(24); generated=True
            if len(password)<12 or len(password)>256 or not username or len(username)>80:
                raise RuntimeError('El superadministrador requiere usuario y contraseña de 12 a 256 caracteres.')
            salt=secrets.token_bytes(16)
            conn.execute('INSERT INTO users VALUES (?,?,?,?,?)',('root',username,salt,password_hash(password,salt),'superadmin'))
            if generated:
                credential_file=DATA_ROOT/'admin-inicial.txt'
                descriptor=os.open(str(credential_file),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                with os.fdopen(descriptor,'w',encoding='utf-8') as f:
                    f.write('Usuario: '+username+'\nContraseña: '+password+'\n')
                print('Acceso inicial guardado en '+str(credential_file),flush=True)
        demo=conn.execute("SELECT 1 FROM schools WHERE id='demo'").fetchone()
        if not demo:
            # Copia consistente de la demo antigua, sin moverla ni sobrescribirla.
            source=server.DB
            target=school_path('demo')
            if source.exists() and source.resolve()!=target.resolve() and not target.exists():
                original=sqlite3.connect(source)
                destination=sqlite3.connect(target)
                try: original.backup(destination)
                finally: original.close(); destination.close()
            with school_db('demo') as demo_conn:
                cat=server.get_catalog(demo_conn);cat['is_demo']=True
                demo_conn.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(cat,ensure_ascii=False),))
            conn.execute('INSERT INTO schools VALUES (?,?,?,?)',('demo','Escuela Demo',1,dt.datetime.now(dt.timezone.utc).isoformat()))


def text(value,label,maximum=100):
    if not isinstance(value,str) or not value.strip() or len(value.strip())>maximum:
        raise ValueError(f'{label}: indica un texto de hasta {maximum} caracteres.')
    return value.strip()


def number(value,label,minimum=1,maximum=1000):
    if type(value) is not int or not minimum<=value<=maximum:
        raise ValueError(f'{label}: indica un entero entre {minimum} y {maximum}.')
    return value


def period(body):
    start=dt.date.fromisoformat(body['start']);end=dt.date.fromisoformat(body['end'])
    if start>end or (end-start).days>550:
        raise ValueError('El periodo debe durar entre 1 y 551 días.')
    return dict(start=start.isoformat(),end=end.isoformat())


def readiness(cat):
    issues=[]
    for key,label in [('groups','grupos'),('rooms','espacios'),('subjects','materias'),('teachers','docentes o reservas')]:
        if not cat[key]: issues.append('Añade '+label+'.')
    for sub in cat['subjects']:
        if not any(sub['id'] in t['subjects'] for t in cat['teachers']): issues.append('Falta un docente habilitado para '+sub['name']+'.')
        if not any(r['kind']==sub['kind'] for r in cat['rooms']): issues.append('Falta un espacio compatible para '+sub['name']+'.')
        for group in cat['groups']:
            size=group['size'] if sub['scope']=='group' else (group['size']+len(group['subgroups'])-1)//len(group['subgroups'])
            if not any(r['kind']==sub['kind'] and r['capacity']>=size for r in cat['rooms']):
                issues.append(f"Falta aforo para {sub['name']} / {group['name']}.")
    return list(dict.fromkeys(issues))


class Handler(server.Handler):
    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','same-origin')
        super().end_headers()

    def send_json(self,value,status=200):
        if isinstance(value,dict) and 'catalog' in value and hasattr(self,'current_school'):
            value['school']=dict(self.current_school)
            value['setup_issues']=readiness(value['catalog'])
            value['catalog_revision']=getattr(self,'catalog_revision',1)
            if not self.current_school['is_demo']:
                value['user']={**self.acting_user,'role':'superadmin','label':'Superadministrador'}
                value['users']=[value['user']]
        super().send_json(value,status)

    def read_body(self):
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            raise ValueError('Se requiere application/json.')
        size=int(self.headers.get('Content-Length','0'))
        if not 0<size<=100_000: raise ValueError('Tamaño de solicitud no válido.')
        body=json.loads(self.rfile.read(size))
        if not isinstance(body,dict): raise ValueError('Solicitud no válida.')
        return body

    def principal(self):
        cookie=SimpleCookie()
        try: cookie.load(self.headers.get('Cookie',''))
        except CookieError: return None
        token=cookie.get('academia_session')
        if not token:return None
        with platform_db() as conn:
            row=conn.execute('SELECT u.id,u.username,u.role,s.csrf,s.token FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?',(token_hash(token.value),time.time())).fetchone()
        return dict(row) if row else None

    def schools(self,principal):
        with platform_db() as conn:
            rows=conn.execute('SELECT id,name,is_demo FROM schools'+('' if principal else ' WHERE is_demo=1')+' ORDER BY is_demo,name').fetchall()
        return [dict(r) for r in rows]

    def selected_school(self,principal):
        school_id=self.headers.get('X-School-ID','demo')
        result=next((s for s in self.schools(principal) if s['id']==school_id),None)
        if not result:
            self.send_json({'error':'Escuela no encontrada o sin acceso.'},404 if principal else 401)
        return result

    def mutation_allowed(self,principal,require_admin=False):
        origin=self.headers.get('Origin')
        if origin and urlparse(origin).netloc!=self.headers.get('Host'):
            self.send_json({'error':'Origen no permitido.'},403);return False
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            self.send_json({'error':'Se requiere application/json.'},415);return False
        if require_admin and not principal:
            self.send_json({'error':'Inicia sesión como superadministrador.'},401);return False
        if principal and not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),principal['csrf']):
            self.send_json({'error':'Sesión no válida. Recarga la página.'},403);return False
        return True

    def do_GET(self):
        self.acting_user=None
        path=urlparse(self.path).path
        principal=self.principal()
        if path=='/api/me':
            return self.send_json(dict(user={k:principal[k] for k in ['id','username','role']} if principal else None,csrf=principal['csrf'] if principal else None,schools=self.schools(principal)))
        if path=='/api/health':
            try:
                with platform_db() as conn:conn.execute('SELECT 1 FROM users LIMIT 1').fetchone()
                return self.send_json({'status':'ok'})
            except sqlite3.Error:return self.send_json({'status':'unavailable'},503)
        if path=='/api/state':
            school=self.selected_school(principal)
            if not school:return
            self.current_school=school
            if not school['is_demo']:
                self.acting_user=dict(id=principal['id'],name=principal['username'],role='manager',label='Superadministrador')
            token=server.CURRENT_DB.set(school_path(school['id']))
            try:
                with server.connect() as conn:self.catalog_revision=server.get_catalog(conn).get('revision',1)
                return super().do_GET()
            finally:server.CURRENT_DB.reset(token)
        if path.startswith('/api/'):return self.send_json({'error':'Ruta no encontrada.'},404)
        return super().do_GET()

    def do_POST(self):
        self.acting_user=None
        path=urlparse(self.path).path
        principal=self.principal()
        if path=='/api/login':return self.login()
        if not self.mutation_allowed(principal,require_admin=path in ['/api/schools','/api/catalog','/api/logout']):return
        if path=='/api/logout':
            with platform_db() as conn:conn.execute('DELETE FROM sessions WHERE token=?',(principal['token'],))
            return self.cookie_response({'ok':True},'',max_age=0)
        if path=='/api/schools':return self.create_school()
        school=self.selected_school(principal)
        if not school:return
        self.current_school=school
        if not school['is_demo']:
            self.acting_user=dict(id=principal['id'],name=principal['username'],role='manager',label='Superadministrador')
        token=server.CURRENT_DB.set(school_path(school['id']))
        try:
            if path=='/api/catalog':return self.edit_catalog()
            if path in ['/api/generate','/api/season-generate','/api/publish']:
                with server.connect() as conn:issues=readiness(server.get_catalog(conn))
                if issues:return self.send_json({'error':'Completa la configuración antes de planificar. '+' '.join(issues)},400)
            return super().do_POST()
        finally:server.CURRENT_DB.reset(token)

    def cookie_response(self,value,token,max_age=SESSION_TTL,status=200):
        body=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status)
        secure='; Secure' if os.environ.get('COOKIE_SECURE')=='1' or os.environ.get('RAILWAY_ENVIRONMENT_ID') else ''
        self.send_header('Set-Cookie',f'academia_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}'+secure)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)

    def login(self):
        if not self.mutation_allowed(None):return
        try:
            body=self.read_body()
            username=text(body.get('username'),'Usuario',80)
            password=body.get('password','')
            if not isinstance(password,str) or len(password)>256:raise ValueError('Credenciales no válidas.')
            key=token_hash(self.client_address[0]+'|'+username.casefold())
            now=time.time()
            with platform_db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                attempt=conn.execute('SELECT * FROM login_attempts WHERE identity=?',(key,)).fetchone()
                if attempt and attempt['reset']>now and attempt['failures']>=5:
                    return self.send_json({'error':'Demasiados intentos. Espera 15 minutos.'},429)
                user=conn.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
                salt=user['salt'] if user else b'no-user-salt-0000'
                hashed=password_hash(password,salt)
                if not user or not hmac.compare_digest(hashed,user['password']):
                    failures=attempt['failures']+1 if attempt and attempt['reset']>now else 1
                    conn.execute('INSERT OR REPLACE INTO login_attempts VALUES (?,?,?)',(key,failures,now+900))
                    return self.send_json({'error':'Usuario o contraseña incorrectos.'},401)
                conn.execute('DELETE FROM login_attempts WHERE identity=?',(key,))
                conn.execute('DELETE FROM sessions WHERE expires<=?',(now,))
                token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
                conn.execute('INSERT INTO sessions VALUES (?,?,?,?)',(token_hash(token),user['id'],csrf,now+SESSION_TTL))
            return self.cookie_response({'ok':True},token)
        except (ValueError,KeyError,TypeError) as error:return self.send_json({'error':str(error)},400)

    def create_school(self):
        try:
            body=self.read_body();name=text(body.get('name'),'Nombre de la escuela')
            season=dict(name=text(body.get('season_name'),'Nombre del curso'),**period(body))
            school_id=secrets.token_hex(12)
            cat=dict(is_demo=False,revision=1,season=season,groups=[],rooms=[],subjects=[],teachers=[])
            with school_db(school_id) as conn:
                conn.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(cat,ensure_ascii=False),))
            with platform_db() as conn:
                conn.execute('INSERT INTO schools VALUES (?,?,?,?)',(school_id,name,0,dt.datetime.now(dt.timezone.utc).isoformat()))
            return self.send_json({'ok':True,'school':dict(id=school_id,name=name,is_demo=0)},201)
        except (ValueError,KeyError,TypeError) as error:return self.send_json({'error':str(error)},400)

    def edit_catalog(self):
        try:
            body=self.read_body();collection=body.get('collection')
            if collection not in ['rooms','groups','subjects','teachers']:raise ValueError('Concepto no válido.')
            item=body.get('item')
            if not isinstance(item,dict):raise ValueError('Datos no válidos.')
            with server.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                cat=server.get_catalog(conn)
                if body.get('catalog_revision')!=cat.get('revision',1):
                    return self.send_json({'error':'El catálogo ha cambiado. Recarga antes de guardar.'},409)
                existing=next((x for x in cat[collection] if x['id']==item.get('id')),None)
                if item.get('id') and not existing:raise ValueError('Concepto no encontrado.')
                clean=dict(id=existing['id'] if existing else secrets.token_hex(6),name=text(item.get('name'),'Nombre'))
                if collection in ['rooms','subjects']:
                    if item.get('kind') not in KINDS:raise ValueError('Tipo de espacio no válido.')
                    clean['kind']=item['kind']
                if collection=='rooms':clean['capacity']=number(item.get('capacity'),'Aforo')
                elif collection=='groups':
                    clean['size']=number(item.get('size'),'Alumnos')
                    count=number(item.get('subgroup_count'),'Subgrupos',2,20)
                    if count>clean['size']:raise ValueError('No puede haber más subgrupos que alumnos.')
                    clean['subgroups']=[clean['id']+'-'+str(i+1) for i in range(count)]
                    # Conservar identificadores históricos al editar el tamaño/nombre.
                    if existing and len(existing['subgroups'])==count:clean['subgroups']=existing['subgroups']
                elif collection=='subjects':
                    if item.get('scope') not in ['group','subgroup']:raise ValueError('Formato de grupo no válido.')
                    clean.update(scope=item['scope'],blocks=number(item.get('blocks'),'Bloques semanales',1,10),color=item.get('color','blue'),**period(item))
                    if clean['color'] not in COLORS:raise ValueError('Color no válido.')
                elif collection=='teachers':
                    subjects=item.get('subjects');days=item.get('days')
                    if not isinstance(subjects,list) or not subjects or any(s not in [x['id'] for x in cat['subjects']] for s in subjects):raise ValueError('Selecciona materias habilitadas de esta escuela.')
                    if not isinstance(days,list) or not days or any(type(d) is not int or d not in range(5) for d in days):raise ValueError('Selecciona días disponibles válidos.')
                    clean.update(subjects=list(dict.fromkeys(subjects)),days=list(dict.fromkeys(days)),placeholder=bool(item.get('placeholder')),max_daily=number(item.get('max_daily'),'Carga diaria',1,4),max_weekly=number(item.get('max_weekly'),'Carga semanal',1,20))
                cat[collection]=[x for x in cat[collection] if x['id']!=clean['id']]+[clean]
                cat['revision']=cat.get('revision',1)+1
                # No invalidar silenciosamente semanas ya configuradas.
                for week,raw in conn.execute('SELECT week,data FROM weeks'):
                    stored=json.loads(raw)
                    errors=server.validate(stored['sessions'],cat,week)
                    if errors:raise ValueError('El cambio afecta a sesiones existentes. Ajusta primero esas sesiones. '+errors[0])
                conn.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(cat,ensure_ascii=False),))
            return self.send_json({'ok':True,'message':'Configuración de la escuela guardada.'})
        except (ValueError,KeyError,TypeError,StopIteration) as error:return self.send_json({'error':str(error) or 'Datos no válidos.'},400)


def main():
    initialize()
    host=os.environ.get('HOST','127.0.0.1');port=int(os.environ.get('PORT','8000'))
    http=server.ThreadingHTTPServer((host,port),Handler)
    print(f'Plataforma de escuelas disponible en http://{host}:{port}',flush=True)
    try:http.serve_forever()
    except KeyboardInterrupt:pass
    finally:http.server_close()


if __name__=='__main__':main()
