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
COLORS = {'blue','violet','orange','teal','green'}
SCHOOL_ROLES = {'student','teacher','coordinator','school_admin'}
ROLE_LABELS = {'student':'Alumno/a','teacher':'Profesor/a','coordinator':'Coordinador/a','school_admin':'Administrador/a de escuela','superadmin':'Superadministrador'}

def role_permissions(role, demo=False):
    admin=role in {'superadmin','school_admin','manager'}
    return dict(configure=admin and not demo, manage_users=admin and not demo,
                plan=admin, coordinate=admin or role=='coordinator',
                personal=role in {'student','teacher'}, manage_schools=role=='superadmin')



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
          CREATE TABLE IF NOT EXISTS memberships (
            user_id TEXT NOT NULL REFERENCES users(id), school_id TEXT NOT NULL REFERENCES schools(id),
            role TEXT NOT NULL, subgroup TEXT, teacher_id TEXT, active INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(user_id,school_id));
          CREATE TABLE IF NOT EXISTS login_attempts (identity TEXT PRIMARY KEY, failures INTEGER NOT NULL, reset REAL NOT NULL);
        ''')
        columns={row[1] for row in conn.execute('PRAGMA table_info(users)')}
        if 'display_name' not in columns:conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
        if 'active' not in columns:conn.execute('ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
        conn.execute("UPDATE users SET display_name=username WHERE display_name=''")
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
            conn.execute('INSERT INTO users (id,username,salt,password,role,display_name) VALUES (?,?,?,?,?,?)',('root',username,salt,password_hash(password,salt),'superadmin',username))
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
    for sub in cat['subjects']:
        capacity=sum(server.active(sub,w) for w in server.season_weeks(cat))*sub.get('weekly_hours',sub['blocks']*1.5)
        if sub.get('total_hours',0)>capacity:issues.append('Las horas totales de '+sub['name']+' no caben en su periodo con la carga semanal indicada.')
    return list(dict.fromkeys(issues))


class Handler(server.Handler):
    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','same-origin')
        super().end_headers()

    def send_json(self,value,status=200):
        if isinstance(value,dict) and 'catalog' in value and hasattr(self,'current_school'):
            school=self.current_school
            value['school']=dict(school)
            if not school['is_demo']:
                value['user']=dict(self.member)
                value['users']=[value['user']]
            role=value['user']['role']
            value['permissions']=role_permissions(role,bool(school['is_demo']))
            if school['is_demo'] and getattr(self,'current_principal',None):
                value['permissions']['manage_schools']=self.current_principal['role']=='superadmin'
                value['permissions']['configure']=self.current_principal['role']=='superadmin'
            value['setup_issues']=readiness(value['catalog']) if value['permissions']['coordinate'] else []
            if value['permissions']['coordinate']:
                with school_db(school['id']) as conn:
                    cat=server.get_catalog(conn)
                    value['resource_alerts']=[dict(week=w,draft=len(server.room_conflicts(d['sessions'],cat,w)),published=len(server.room_conflicts((d.get('published') or {}).get('sessions',[]),cat,w))) for w,raw in conn.execute('SELECT week,data FROM weeks') for d in [json.loads(raw)] if server.room_conflicts(d['sessions'],cat,w) or server.room_conflicts((d.get('published') or {}).get('sessions',[]),cat,w)]
            value['catalog_revision']=getattr(self,'catalog_revision',1)
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
            row=conn.execute('SELECT u.id,u.username,u.display_name,u.role,s.csrf,s.token FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.active=1',(token_hash(token.value),time.time())).fetchone()
        return dict(row) if row else None

    def schools(self,principal):
        with platform_db() as conn:
            if principal and principal['role']=='superadmin':
                rows=conn.execute("SELECT id,name,is_demo,'superadmin' AS role FROM schools ORDER BY is_demo,name").fetchall()
            elif principal:
                rows=conn.execute("SELECT s.id,s.name,s.is_demo,m.role FROM schools s JOIN memberships m ON m.school_id=s.id WHERE m.user_id=? AND m.active=1 AND s.is_demo=0 UNION SELECT id,name,is_demo,NULL AS role FROM schools WHERE is_demo=1 ORDER BY is_demo,name",(principal['id'],)).fetchall()
            else:
                rows=conn.execute('SELECT id,name,is_demo,NULL AS role FROM schools WHERE is_demo=1').fetchall()
        return [dict(r) for r in rows]

    def selected_school(self,principal):
        school_id=self.headers.get('X-School-ID','demo')
        result=next((s for s in self.schools(principal) if s['id']==school_id),None)
        if not result:self.send_json({'error':'Escuela no encontrada o sin acceso.'},404 if principal else 401)
        return result

    def bind_school(self,principal,school):
        self.current_principal=principal
        self.current_school=school
        self.member=None
        if school['is_demo']:return
        if principal['role']=='superadmin':
            self.member=dict(id=principal['id'],name=principal['display_name'],username=principal['username'],role='superadmin',label=ROLE_LABELS['superadmin'])
        else:
            with platform_db() as conn:
                row=conn.execute('SELECT role,subgroup,teacher_id FROM memberships WHERE user_id=? AND school_id=? AND active=1',(principal['id'],school['id'])).fetchone()
            if not row:raise ValueError('Escuela no encontrada o sin acceso.')
            self.member=dict(id=principal['id'],name=principal['display_name'],username=principal['username'],role=row['role'],label=ROLE_LABELS[row['role']],subgroup=row['subgroup'],teacher_id=row['teacher_id'])
        internal_role='manager' if self.member['role'] in {'superadmin','school_admin'} else self.member['role']
        self.acting_user={**self.member,'role':internal_role}

    def personal_state(self,school):
        week=server.monday(parse_qs(urlparse(self.path).query).get('week',[None])[0])
        user=self.member
        def own(s,cat):
            return user['subgroup'] in server.participants(s,cat) if user['role']=='student' else s['teacher']==user['teacher_id']
        with server.connect() as conn:
            cat=server.get_catalog(conn)
            stored={w:json.loads(raw) for w,raw in conn.execute('SELECT week,data FROM weeks')}
        published=stored.get(week,{}).get('published')
        sessions=[s for s in published['sessions'] if own(s,cat)] if published else []
        summary=[]
        for target in server.season_weeks(cat):
            public=stored.get(target,{}).get('published')
            count=sum(own(s,cat) for s in public['sessions']) if public else 0
            summary.append(dict(week=target,required=count,planned=count,status='published' if public else 'empty',pending=0,missing=None))
        # Solo se envían los recursos necesarios para representar la agenda personal.
        audience_ids={s['audience'] for s in sessions}
        visible=dict(cat)
        visible['groups']=[g for g in cat['groups'] if (user['role']=='student' and user['subgroup'] in g['subgroups']) or g['id'] in audience_ids or any(i in audience_ids for i in g['subgroups'])]
        visible['subjects']=[x for x in cat['subjects'] if x['id'] in {s['subject'] for s in sessions}]
        visible['rooms']=[x for x in cat['rooms'] if x['id'] in {s['room'] for s in sessions}]
        visible['teachers']=[dict(id=x['id'],name=x['name'],placeholder=x['placeholder']) for x in cat['teachers'] if x['id'] in {s['teacher'] for s in sessions}]
        return self.send_json(dict(catalog=visible,users=[user],user=user,week=week,slots=server.SLOTS,
            schedule_warnings=len(server.room_conflicts(sessions,cat,week)),data=dict(sessions=sessions,published={'at':published['at']} if published else None,revision=1),season_summary=summary))

    def mutation_allowed(self,principal,require_admin=False):
        origin=self.headers.get('Origin')
        if origin and urlparse(origin).netloc!=self.headers.get('Host'):
            self.send_json({'error':'Origen no permitido.'},403);return False
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            self.send_json({'error':'Se requiere application/json.'},415);return False
        if require_admin and not principal:
            self.send_json({'error':'Inicia sesión para continuar.'},401);return False
        if principal and not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),principal['csrf']):
            self.send_json({'error':'Sesión no válida. Recarga la página.'},403);return False
        return True

    def do_GET(self):
        self.acting_user=None
        path=urlparse(self.path).path
        principal=self.principal()
        if path=='/api/me':
            return self.send_json(dict(user={k:principal[k] for k in ['id','username','display_name','role']} if principal else None,csrf=principal['csrf'] if principal else None,schools=self.schools(principal)))
        if path=='/api/health':
            try:
                with platform_db() as conn:conn.execute('SELECT 1 FROM users LIMIT 1').fetchone()
                return self.send_json({'status':'ok'})
            except sqlite3.Error:return self.send_json({'status':'unavailable'},503)
        if path=='/api/users':
            school=self.selected_school(principal)
            if not school:return
            self.bind_school(principal,school)
            if not self.member or not role_permissions(self.member['role'])['manage_users']:
                return self.send_json({'error':'No tienes permisos para esta acción.'},403)
            return self.list_users()
        if path=='/api/state':
            school=self.selected_school(principal)
            if not school:return
            self.bind_school(principal,school)
            token=server.CURRENT_DB.set(school_path(school['id']))
            try:
                with server.connect() as conn:self.catalog_revision=server.get_catalog(conn).get('revision',1)
                if self.member and self.member['role'] in {'student','teacher'}:return self.personal_state(school)
                return super().do_GET()
            except (ValueError,KeyError,TypeError) as error:return self.send_json({'error':str(error)},400)
            finally:server.CURRENT_DB.reset(token)
        if path.startswith('/api/'):return self.send_json({'error':'Ruta no encontrada.'},404)
        return super().do_GET()

    def do_POST(self):
        self.acting_user=None
        path=urlparse(self.path).path
        principal=self.principal()
        if path=='/api/login':return self.login()
        if not self.mutation_allowed(principal,require_admin=path in ['/api/schools','/api/catalog','/api/logout','/api/users']):return
        if path=='/api/logout':
            with platform_db() as conn:conn.execute('DELETE FROM sessions WHERE token=?',(principal['token'],))
            return self.cookie_response({'ok':True},'',max_age=0)
        if path=='/api/schools':
            if principal['role']!='superadmin':return self.send_json({'error':'Solo el superadministrador puede crear escuelas.'},403)
            return self.create_school()
        school=self.selected_school(principal)
        if not school:return
        self.bind_school(principal,school)
        if not school['is_demo']:
            permissions=role_permissions(self.member['role'])
            if path=='/api/users':
                if not permissions['manage_users']:return self.send_json({'error':'No tienes permisos para esta acción.'},403)
                return self.save_user(principal)
            if self.member['role']=='coordinator' and path!='/api/assign':return self.send_json({'error':'No tienes permisos para esta acción.'},403)
            if permissions['personal']:return self.send_json({'error':'Este perfil solo puede consultar su calendario.'},403)
            if path=='/api/catalog' and not permissions['configure']:return self.send_json({'error':'No tienes permisos para esta acción.'},403)
        token=server.CURRENT_DB.set(school_path(school['id']))
        try:
            if path=='/api/room-availability':return self.room_availability()
            if path=='/api/catalog':return self.edit_catalog()
            if path in ['/api/generate','/api/season-generate','/api/publish']:
                with server.connect() as conn:issues=readiness(server.get_catalog(conn))
                if issues:return self.send_json({'error':'Completa la configuración antes de planificar. '+' '.join(issues)},400)
            return super().do_POST()
        finally:server.CURRENT_DB.reset(token)

    def list_users(self):
        with platform_db() as conn:
            rows=conn.execute('SELECT u.id,u.username,u.display_name AS name,m.role,m.subgroup,m.teacher_id,m.active FROM users u JOIN memberships m ON u.id=m.user_id WHERE m.school_id=? ORDER BY u.display_name,u.username',(self.current_school['id'],)).fetchall()
        return self.send_json({'users':[dict(row) for row in rows]})

    def save_user(self,principal):
        try:
            body=self.read_body()
            role=body.get('role')
            if role not in SCHOOL_ROLES:raise ValueError('Rol no válido.')
            name=text(body.get('name'),'Nombre',100)
            active=body.get('active',True)
            if type(active) is not bool:raise ValueError('Estado no válido.')
            with school_db(self.current_school['id']) as school_conn:cat=server.get_catalog(school_conn)
            subgroup=body.get('subgroup') if role=='student' else None
            teacher_id=body.get('teacher_id') if role=='teacher' else None
            if role=='student' and subgroup not in [i for g in cat['groups'] for i in g['subgroups']]:raise ValueError('Selecciona un subgrupo de esta escuela.')
            if role=='teacher' and not any(t['id']==teacher_id and not t['placeholder'] for t in cat['teachers']):raise ValueError('Selecciona un docente nominal de esta escuela.')
            with platform_db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                user_id=body.get('id')
                if user_id:
                    existing=conn.execute('SELECT * FROM memberships WHERE user_id=? AND school_id=?',(user_id,self.current_school['id'])).fetchone()
                    if not existing:return self.send_json({'error':'Usuario no encontrado en esta escuela.'},404)
                    if existing['role']=='school_admin' and existing['active'] and (role!='school_admin' or not active):
                        remaining=conn.execute("SELECT COUNT(*) FROM memberships WHERE school_id=? AND role='school_admin' AND active=1 AND user_id!=?",(self.current_school['id'],user_id)).fetchone()[0]
                        if not remaining and principal['role']!='superadmin':raise ValueError('No puedes desactivar al último administrador de la escuela.')
                else:
                    username=text(body.get('username'),'Usuario',80)
                    password=body.get('password','')
                    if not isinstance(password,str) or not 12<=len(password)<=256:raise ValueError('La contraseña inicial debe tener entre 12 y 256 caracteres.')
                    user_id=secrets.token_hex(12)
                    salt=secrets.token_bytes(16)
                    conn.execute('INSERT INTO users (id,username,salt,password,role,display_name) VALUES (?,?,?,?,?,?)',(user_id,username,salt,password_hash(password,salt),'user',name))
                if teacher_id and active and conn.execute('SELECT 1 FROM memberships WHERE school_id=? AND teacher_id=? AND user_id!=? AND active=1',(self.current_school['id'],teacher_id,user_id)).fetchone():raise ValueError('Este docente ya tiene una cuenta activa en esta escuela.')
                conn.execute('UPDATE users SET display_name=? WHERE id=?',(name,user_id))
                conn.execute('INSERT OR REPLACE INTO memberships (user_id,school_id,role,subgroup,teacher_id,active) VALUES (?,?,?,?,?,?)',(user_id,self.current_school['id'],role,subgroup,teacher_id,int(active)))
            return self.send_json({'ok':True,'message':'Usuario guardado.','id':user_id})
        except sqlite3.IntegrityError:return self.send_json({'error':'Ese nombre de usuario ya existe.'},409)
        except (ValueError,KeyError,TypeError) as error:return self.send_json({'error':str(error)},400)

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
                if not user or not user['active'] or not hmac.compare_digest(hashed,user['password']):
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

    def room_availability(self):
        try:
            if self.current_school['is_demo'] or not self.member or not role_permissions(self.member['role'])['configure']:
                return self.send_json({'error':'No tienes permisos para esta acción.'},403)
            body=self.read_body();effective=dt.date.fromisoformat(body['from']).isoformat()
            windows=body.get('windows')
            if not isinstance(windows,list) or len(windows)>100:raise ValueError('Franjas no válidas.')
            clean=[]
            for w in windows:
                if not isinstance(w,dict) or type(w.get('day')) is not int or w['day'] not in range(5):raise ValueError('Día no válido.')
                for key in ['start','end']:
                    t=w.get(key,'')
                    if not isinstance(t,str) or len(t)!=5 or dt.time.fromisoformat(t).strftime('%H:%M')!=t:raise ValueError('Hora no válida.')
                if w['start']>=w['end']:raise ValueError('La hora final debe ser posterior al inicio.')
                clean.append(dict(day=w['day'],start=w['start'],end=w['end']))
            merged=[]
            for window in sorted(clean,key=lambda w:(w['day'],w['start'])):
                if merged and merged[-1]['day']==window['day'] and window['start']<=merged[-1]['end']:
                    merged[-1]['end']=max(merged[-1]['end'],window['end'])
                else:merged.append(dict(window))
            clean=merged
            with server.connect() as conn:
                conn.execute('BEGIN IMMEDIATE');cat=server.get_catalog(conn)
                if body.get('catalog_revision')!=cat.get('revision',1):return self.send_json({'error':'El catálogo ha cambiado. Recarga antes de guardar.'},409)
                room=next((r for r in cat['rooms'] if r['id']==body.get('room')),None)
                if not room:raise ValueError('Espacio no encontrado.')
                room['availability']=sorted([v for v in room.get('availability',[]) if v['from']!=effective]+[dict(**{'from':effective},windows=clean)],key=lambda v:v['from'])
                stored=[(w,json.loads(raw)) for w,raw in conn.execute('SELECT week,data FROM weeks ORDER BY week')]
                preview_token=token_hash(json.dumps(dict(catalog=cat,weeks=stored),sort_keys=True,ensure_ascii=False))
                if body.get('preview_token') and body['preview_token']!=preview_token:return self.send_json({'error':'La planificación ha cambiado. Vuelve a calcular el impacto.'},409)
                impact=[];missing=0
                for week,data in stored:
                    bad=server.room_conflicts(data['sessions'],cat,week)
                    public=server.room_conflicts((data.get('published') or {}).get('sessions',[]),cat,week)
                    if bad or public:impact.append(dict(week=week,draft=len(bad),published=len(public),locked=sum(bool(s.get('locked')) for s in bad)))
                    if body.get('apply') is True and body.get('replan') is True and bad:
                        bad_ids={s['id'] for s in bad};preserved=[dict(s) for s in data['sessions'] if s['id'] not in bad_ids]
                        locks={s['id']:s.get('locked',False) for s in preserved}
                        for s in preserved:s['locked']=True
                        sessions,remaining=server.generate(cat,existing=preserved,week=week)
                        for s in sessions:
                            if s['id'] in locks:s['locked']=locks[s['id']]
                        data['sessions']=sessions;data['missing']=remaining;missing+=len(remaining)
                        data['revision']+=1;data['updated']=dt.datetime.now().isoformat(timespec='seconds')
                        data['history'].insert(0,dict(at=data['updated'],user=self.member['name'],message='Disponibilidad de '+room['name']+': borrador reajustado'))
                        conn.execute('UPDATE weeks SET data=? WHERE week=?',(json.dumps(data,ensure_ascii=False),week))
                if body.get('apply') is True:
                    cat['revision']=cat.get('revision',1)+1
                    conn.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(cat,ensure_ascii=False),))
            return self.send_json(dict(ok=True,impact=impact,missing=missing,preview_token=preview_token,message='Disponibilidad guardada. Revisa y publica los borradores afectados.' if body.get('apply') is True else 'Vista previa calculada.'))
        except (ValueError,KeyError,TypeError) as error:return self.send_json({'error':str(error)},400)

    def edit_catalog(self):
        try:
            body=self.read_body();collection=body.get('collection')
            if collection not in ['space_types','locations','rooms','groups','subjects','teachers']:raise ValueError('Concepto no válido.')
            items=body.get('items',[body.get('item')])
            if not isinstance(items,list) or not 1<=len(items)<=100 or any(not isinstance(i,dict) for i in items):raise ValueError('Indica entre 1 y 100 filas válidas.')
            with server.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                cat=server.get_catalog(conn)
                if body.get('catalog_revision')!=cat.get('revision',1):
                    return self.send_json({'error':'El catálogo ha cambiado. Recarga antes de guardar.'},409)
                prior_errors={w:server.validate(json.loads(raw)['sessions'],cat,w) for w,raw in conn.execute('SELECT week,data FROM weeks')}
                for row_number,item in enumerate(items,1):
                    existing=next((x for x in cat[collection] if x['id']==item.get('id')),None)
                    if item.get('id') and not existing:raise ValueError('Concepto no encontrado.')
                    clean=dict(id=existing['id'] if existing else secrets.token_hex(6),name=text(item.get('name'),'Nombre'))
                    if collection in ['locations','space_types']:
                        if any(x['name'].strip().casefold()==clean['name'].casefold() and x['id']!=clean['id'] for x in cat[collection]):raise ValueError('Ya existe un elemento con ese nombre.')
                        if collection=='space_types':clean['builtin']=existing.get('builtin',False) if existing else False
                    if collection in ['rooms','subjects']:
                        if item.get('kind') not in [t['id'] for t in cat['space_types']]:raise ValueError('Tipo de espacio no válido.')
                        clean['kind']=item['kind']
                    if collection=='rooms':
                        clean['capacity']=number(item.get('capacity'),'Aforo')
                        clean['location_id']=item.get('location_id',existing.get('location_id','main') if existing else 'main')
                        if clean['location_id'] not in [location['id'] for location in cat['locations']]:raise ValueError('Selecciona una ubicación de esta escuela.')
                        if existing and 'availability' in existing:clean['availability']=existing['availability']
                    elif collection=='groups':
                        clean['size']=number(item.get('size'),'Alumnos')
                        count=number(item.get('subgroup_count'),'Subgrupos',2,20)
                        if count>clean['size']:raise ValueError('No puede haber más subgrupos que alumnos.')
                        clean['subgroups']=[clean['id']+'-'+str(i+1) for i in range(count)]
                        # Conservar identificadores históricos al editar el tamaño/nombre.
                        if existing and len(existing['subgroups'])==count:clean['subgroups']=existing['subgroups']
                    elif collection=='subjects':
                        if item.get('scope') not in ['group','subgroup']:raise ValueError('Formato de grupo no válido.')
                        clean.update(scope=item['scope'],color=item.get('color','blue'),**period(item))
                        clean.update(server.subject_hours({**item,**clean},cat))
                        if clean['color'] not in COLORS:raise ValueError('Color no válido.')
                    elif collection=='teachers':
                        subjects=item.get('subjects');days=item.get('days')
                        if not isinstance(subjects,list) or not subjects or any(s not in [x['id'] for x in cat['subjects']] for s in subjects):raise ValueError('Selecciona materias habilitadas de esta escuela.')
                        if not isinstance(days,list) or not days or any(type(d) is not int or d not in range(5) for d in days):raise ValueError('Selecciona días disponibles válidos.')
                        clean.update(subjects=list(dict.fromkeys(subjects)),days=list(dict.fromkeys(days)),placeholder=bool(item.get('placeholder')),max_daily=number(item.get('max_daily'),'Carga diaria',1,4),max_weekly=number(item.get('max_weekly'),'Carga semanal',1,20))
                    if existing:
                        with platform_db() as auth:
                            bindings=auth.execute('SELECT role,subgroup,teacher_id FROM memberships WHERE school_id=?',(self.current_school['id'],)).fetchall()
                        if collection=='groups' and any(b['subgroup'] in existing['subgroups'] and b['subgroup'] not in clean['subgroups'] for b in bindings):raise ValueError('Reasigna primero los usuarios de los subgrupos afectados.')
                        if collection=='teachers' and clean['placeholder'] and any(b['teacher_id']==clean['id'] for b in bindings):raise ValueError('Un docente vinculado a una cuenta no puede convertirse en reserva.')
                    cat[collection]=[x for x in cat[collection] if x['id']!=clean['id']]+[clean]
                cat['revision']=cat.get('revision',1)+1
                # No invalidar silenciosamente semanas ya configuradas.
                for week,raw in conn.execute('SELECT week,data FROM weeks'):
                    stored=json.loads(raw)
                    errors=server.validate(stored['sessions'],cat,week)
                    previous=list(prior_errors.get(week,[]))
                    introduced=[]
                    for error in errors:
                        if error in previous:previous.remove(error)
                        else:introduced.append(error)
                    errors=introduced
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
