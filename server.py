#!/usr/bin/env python3
"""Academia · planificador demo sin dependencias externas."""
import copy
from contextvars import ContextVar
import datetime as dt
import json
import os
from pathlib import Path
import random
import sqlite3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer as BaseThreadingHTTPServer

class ThreadingHTTPServer(BaseThreadingHTTPServer):
    request_queue_size = 128
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('DATABASE_PATH', str(ROOT / 'data' / 'academia.db')))
CURRENT_DB = ContextVar('school_database', default=None)

def database_path():
    return CURRENT_DB.get() or DB

SLOTS = ['08:00–09:30', '09:45–11:15', '11:45–13:15', '13:30–15:00']
USERS = [dict(id='manager', name='Elena Martín', role='manager', label='Gestora de horarios'), dict(id='coordinator', name='Carlos Ruiz', role='coordinator', label='Coordinador docente'), dict(id='student-a1', name='Lucía Torres', role='student', label='Alumna · Alfa / A1', subgroup='A1'), dict(id='student-b2', name='Pablo García', role='student', label='Alumno · Bravo / B2', subgroup='B2')]

def catalogue():
    return dict(groups=[dict(id='A', name='Alfa', size=48, subgroups=['A1','A2']),dict(id='B', name='Bravo', size=48, subgroups=['B1','B2'])],
        rooms=[dict(id='aula1',name='Aula 01',kind='theory',capacity=60),dict(id='aula2',name='Aula 02',kind='theory',capacity=60),dict(id='tatami',name='Tatami',kind='tatami',capacity=24),dict(id='pool',name='Piscina',kind='pool',capacity=24),dict(id='patio',name='Pista exterior',kind='outdoor',capacity=30)],
        subjects=[dict(id='law',name='Derecho y deontología',kind='theory',color='blue',scope='group',blocks=2),dict(id='procedure',name='Procedimiento policial',kind='theory',color='violet',scope='group',blocks=2),dict(id='defense',name='Defensa personal',kind='tatami',color='orange',scope='subgroup',blocks=2),dict(id='swim',name='Salvamento acuático',kind='pool',color='teal',scope='subgroup',blocks=1),dict(id='fitness',name='Preparación física',kind='outdoor',color='green',scope='subgroup',blocks=1)],
        teachers=[dict(id='t1',name='Ana Beltrán',subjects=['law'],placeholder=False,days=[0,1,2,3,4],max_daily=3,max_weekly=12),dict(id='t2',name='Javier Medina',subjects=['procedure'],placeholder=False,days=[0,1,2,3,4],max_daily=3,max_weekly=12),dict(id='t3',name='Marta Salas',subjects=['defense'],placeholder=False,days=[0,1,2,3,4],max_daily=3,max_weekly=12),dict(id='t4',name='Diego Navarro',subjects=['swim'],placeholder=False,days=[0,1,2,3,4],max_daily=3,max_weekly=12),dict(id='t5',name='Laura Vega',subjects=['fitness'],placeholder=False,days=[0,1,2,3,4],max_daily=3,max_weekly=12),dict(id='p1',name='Por asignar · Defensa',subjects=['defense'],placeholder=True,days=[0,1,2,3,4],max_daily=3,max_weekly=12),dict(id='p2',name='Por asignar · Acuático',subjects=['swim'],placeholder=True,days=[0,1,2,3,4],max_daily=3,max_weekly=12)])

def monday(value=None):
    date = dt.date.fromisoformat(value) if value else dt.date.today()
    return (date-dt.timedelta(days=date.weekday())).isoformat()

def connect():
    path=database_path()
    path.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(path,timeout=15)
    conn.execute('CREATE TABLE IF NOT EXISTS weeks (week TEXT PRIMARY KEY, data TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
    conn.execute('INSERT OR IGNORE INTO settings VALUES (1,?)',(json.dumps(catalogue()),))
    conn.commit()
    return conn

def get_catalog(conn):
    cat=json.loads(conn.execute('SELECT data FROM settings WHERE id=1').fetchone()[0])
    original=copy.deepcopy(cat)
    # Compatibilidad con las bases de datos iniciales de la demo.
    cat.setdefault('season',dict(name='Promoción 2026 / 2027',start='2026-09-14',end='2027-06-25'))
    for subject in cat['subjects']:
        subject.setdefault('start','2026-09-14')
        subject.setdefault('end', {'law':'2027-01-29','procedure':'2027-03-26','swim':'2026-12-18'}.get(subject['id'],'2027-06-25'))
    for subject in cat['subjects']:
        subject.setdefault('weekly_hours',subject['blocks']*1.5)
        subject.setdefault('total_hours',sum(active(subject,w) for w in season_weeks(cat))*subject['weekly_hours'])
    if cat!=original:conn.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(cat,ensure_ascii=False),))
    return cat

def active(subject,week,day=None):
    start=dt.date.fromisoformat(subject.get('start','2026-09-14'))
    end=dt.date.fromisoformat(subject.get('end','2027-06-25'))
    first=dt.date.fromisoformat(week)
    if day is not None: return start <= first+dt.timedelta(days=day) <= end
    return start <= first+dt.timedelta(days=4) and end >= first


def season_weeks(cat):
    season=cat['season']
    first=dt.date.fromisoformat(monday(season['start']))
    end=dt.date.fromisoformat(season['end'])
    return [(first+dt.timedelta(days=7*i)).isoformat() for i in range((end-first).days//7+1)]

def season_summary(conn,cat,user):
    stored={w:json.loads(raw) for w,raw in conn.execute('SELECT week,data FROM weeks')}
    rows=[]
    for week in season_weeks(cat):
        item=stored.get(week)
        public=item and item['published']
        rows.append(dict(week=week,required=len(requirements(cat,week)),
            planned=len(public['sessions']) if user['role']=='student' and public else len(item['sessions']) if item and user['role']!='student' else 0,
            status='published' if public else 'draft' if item and user['role']!='student' else 'empty',
            pending=sum(next(t for t in cat['teachers'] if t['id']==s['teacher'])['placeholder'] for s in item['sessions']) if item and user['role']!='student' else 0,
            missing=len(coverage(item['sessions'],cat,week)) if item and user['role']!='student' else None))
    return rows

def participants(session, cat):
    for group in cat['groups']:
        if session['audience']==group['id']: return set(group['subgroups'])
        if session['audience'] in group['subgroups']: return {session['audience']}
    raise ValueError('Grupo o subgrupo desconocido.')

def validate(sessions, cat, week=None):
    errors=[]
    for index,s in enumerate(sessions):
        tag=f"Sesión {index+1}"
        subject=next((x for x in cat['subjects'] if x['id']==s.get('subject')),None)
        room=next((x for x in cat['rooms'] if x['id']==s.get('room')),None)
        teacher=next((x for x in cat['teachers'] if x['id']==s.get('teacher')),None)
        if not subject or not room or not teacher:
            errors.append(f'{tag}: asignatura, espacio o docente no válido.'); continue
        if type(s.get('day')) is not int or s['day'] not in range(5) or type(s.get('slot')) is not int or s['slot'] not in range(4):
            errors.append(f'{tag}: franja no válida.'); continue
        try: people=participants(s,cat)
        except ValueError as exc: errors.append(str(exc)); continue
        group=next(g for g in cat['groups'] if people <= set(g['subgroups']))
        size=group['size'] if len(people)>1 else (group['size']+len(group['subgroups'])-1)//len(group['subgroups'])
        if week and not active(subject,week,s['day']): errors.append(f'{tag}: materia fuera de su periodo de impartición.')
        if subject['scope']=='subgroup' and len(people)!=1: errors.append(f'{tag}: la práctica requiere un subgrupo.')
        if subject['scope']=='group' and len(people)!=len(group['subgroups']): errors.append(f'{tag}: la teoría requiere el grupo completo.')
        if room['kind']!=subject['kind'] or room['capacity']<size: errors.append(f'{tag}: espacio incompatible o aforo insuficiente.')
        if subject['id'] not in teacher['subjects']: errors.append(f'{tag}: docente sin habilitación para esta materia.')
        if s['day'] not in teacher['days']: errors.append(f'{tag}: docente no disponible este día.')
        same=[x for x in sessions if x.get('teacher')==teacher['id']]
        if len(same)>teacher['max_weekly'] or sum(x.get('day')==s['day'] for x in same)>teacher['max_daily']: errors.append(f'{tag}: carga docente excedida.')
        for other in sessions[:index]:
            if (s['day'],s['slot'])!=(other.get('day'),other.get('slot')): continue
            if s['room']==other.get('room'): errors.append(f'{tag}: el espacio ya está ocupado.')
            if s['teacher']==other.get('teacher'): errors.append(f'{tag}: el docente tiene otra sesión simultánea.')
            try:
                if people & participants(other,cat): errors.append(f'{tag}: cruce de alumnos (grupo/subgrupo).')
            except ValueError: pass
    if week:
        for sub in cat['subjects']:
            if 'total_hours' not in sub:continue
            quota=subject_blocks(sub,cat,week)
            for group in cat['groups']:
                for audience in ([group['id']] if sub['scope']=='group' else group['subgroups']):
                    if sum(s['subject']==sub['id'] and s['audience']==audience for s in sessions)>quota:
                        errors.append(sub['name']+': se supera la carga horaria prevista para esta semana.')
    return list(dict.fromkeys(errors))

def subject_hours(item,cat=None):
    """Horas por alumno; las sesiones actuales tienen una duración fija de 90 min."""
    weekly=item.get('weekly_hours',item.get('blocks',1)*1.5)
    total=item.get('total_hours')
    for value,label in [(weekly,'Horas semanales'),(total,'Horas totales')]:
        if value is None and label=='Horas totales':continue
        if type(value) not in (int,float) or not 0<value<=10000 or abs(value/1.5-round(value/1.5))>1e-8:
            raise ValueError(label+': indica un múltiplo positivo de 1,5 horas (sesiones de 90 minutos).')
    if weekly>15:raise ValueError('Las horas semanales no pueden superar 15 horas.')
    if total is None:
        total=sum(active(item,w) for w in season_weeks(cat))*weekly if cat else weekly
    if total<=0:raise ValueError('El periodo debe incluir al menos una semana de formación.')
    return dict(weekly_hours=weekly,total_hours=total,blocks=round(weekly/1.5))


def subject_blocks(subject,cat,week=None):
    weekly=round(subject.get('weekly_hours',subject['blocks']*1.5)/1.5)
    if week and not active(subject,week):return 0
    if 'total_hours' not in subject:return weekly
    weeks=[w for w in season_weeks(cat) if active(subject,w)]
    if week is None:return weekly
    if week not in weeks:return 0
    remaining=round(subject['total_hours']/1.5)-weeks.index(week)*weekly
    return max(0,min(weekly,remaining))


def requirements(cat,week=None):
    result=[]
    for subject in cat['subjects']:
        if week and not active(subject,week): continue
        for group in cat['groups']:
            for audience in ([group['id']] if subject['scope']=='group' else group['subgroups']):
                for _ in range(subject_blocks(subject,cat,week)): result.append((subject['id'],audience))
    return result

def coverage(sessions,cat,week=None):
    pending=requirements(cat,week)
    for s in sessions:
        pair=(s['subject'],s['audience'])
        if pair in pending: pending.remove(pair)
    return pending

def generate(cat, existing=None, seed=1, week=None):
    locked=[copy.deepcopy(s) for s in (existing or []) if s.get('locked')]
    if validate(locked,cat,week): raise ValueError('Las sesiones fijadas contienen conflictos.')
    tasks=coverage(locked,cat,week)
    rng=random.Random(seed)
    best=None
    for attempt in range(40):
        sessions=copy.deepcopy(locked)
        ordered=list(tasks)
        rng.shuffle(ordered)
        # Los espacios técnicos de capacidad limitada se resuelven primero.
        ordered.sort(key=lambda p: next(s for s in cat['subjects'] if s['id']==p[0])['kind']=='theory')
        missing=[]
        for subject_id,audience in ordered:
            subject=next(s for s in cat['subjects'] if s['id']==subject_id)
            candidates=[]
            for day in range(5):
                for slot in range(4):
                    for room in cat['rooms']:
                        if room['kind']!=subject['kind']: continue
                        for teacher in cat['teachers']:
                            if subject_id not in teacher['subjects']: continue
                            item=dict(id='s-'+os.urandom(6).hex(),subject=subject_id,audience=audience,day=day,slot=slot,room=room['id'],teacher=teacher['id'],locked=False)
                            if validate(sessions+[item],cat,week): continue
                            people=participants(item,cat)
                            daily=[s for s in sessions if s['day']==day and people & participants(s,cat)]
                            repeated=sum(s['subject']==subject_id for s in daily)
                            score=len(daily)*3+repeated*9+slot*.25+(2 if teacher['placeholder'] else 0)+rng.random()*2
                            candidates.append((score,item))
            if candidates: sessions.append(min(candidates,key=lambda x:x[0])[1])
            else: missing.append((subject_id,audience))
        if best is None or len(missing)<len(best[1]): best=(sessions,missing)
        if not missing: break
    return best

def new_week(cat,week):
    sessions,missing=generate(cat,week=week)
    # La demo muestra una reserva pendiente real para el flujo del coordinador.
    for s in sessions:
        if cat.get('is_demo',True) and s['subject']=='swim' and any(t['id']=='p2' for t in cat['teachers']): s['teacher']='p2'; break
    return dict(sessions=sessions,published=None,revision=1,updated=dt.datetime.now().isoformat(timespec='seconds'),history=[],missing=missing)

def load_week(conn,week,cat):
    row=conn.execute('SELECT data FROM weeks WHERE week=?',(week,)).fetchone()
    if row: return json.loads(row[0])
    data=new_week(cat,week) if cat.get('is_demo',True) else dict(sessions=[],published=None,revision=1,updated=dt.datetime.now().isoformat(timespec='seconds'),history=[],missing=coverage([],cat,week))
    if cat.get('is_demo',True): save_week(conn,week,data)
    return data

def save_week(conn,week,data):
    conn.execute('INSERT OR REPLACE INTO weeks VALUES (?,?)',(week,json.dumps(data,ensure_ascii=False)))
    conn.commit()

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT/'static'),**kwargs)
    def send_json(self,value,status=200):
        body=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        url=urlparse(self.path)
        if url.path=='/api/health':
            try:
                connection=sqlite3.connect(str(database_path().resolve().as_uri())+'?mode=rw',uri=True,timeout=2)
                try:
                    connection.execute('SELECT id FROM settings LIMIT 1').fetchone()
                finally:
                    connection.close()
                return self.send_json({'status':'ok'})
            except sqlite3.Error:
                return self.send_json({'status':'unavailable'},503)
        if url.path=='/api/state':
            try:
                user=getattr(self,'acting_user',None) or next((u for u in USERS if u['id']==self.headers.get('X-Demo-User','manager')),None)
                if not user: return self.send_json({'error':'Perfil demo desconocido.'},403)
                week=monday(parse_qs(url.query).get('week',[None])[0])
                with connect() as conn:
                    cat=get_catalog(conn); data=load_week(conn,week,cat)
                    summary=season_summary(conn,cat,user)
                data['missing']=coverage(data['sessions'],cat,week)
                if user['role']=='student':
                    published=data['published']
                    sessions=[] if not published else [s for s in published['sessions'] if user['subgroup'] in participants(s,cat)]
                    visible=dict(sessions=sessions,published={'at':published['at']} if published else None,revision=data['revision'])
                else: visible=data
                return self.send_json(dict(catalog=cat,users=USERS,user=user,week=week,slots=SLOTS,data=visible,season_summary=summary))
            except ValueError as exc: return self.send_json({'error':str(exc)},400)
        if url.path.startswith('/api/'): return self.send_json({'error':'Ruta no encontrada.'},404)
        super().do_GET()
    def do_POST(self):
        try:
            user=getattr(self,'acting_user',None) or next((u for u in USERS if u['id']==self.headers.get('X-Demo-User','')),None)
            if not user or user['role']=='student': return self.send_json({'error':'Este perfil no puede modificar horarios.'},403)
            length=int(self.headers.get('Content-Length','0'))
            if length>100000: raise ValueError('Solicitud demasiado grande.')
            body=json.loads(self.rfile.read(length))
            week=monday(body.get('week')); action=urlparse(self.path).path
            with connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                cat=get_catalog(conn); data=load_week(conn,week,cat)
                if body.get('revision')!=data['revision']: return self.send_json({'error':'Otro usuario ha cambiado esta semana. Recarga antes de guardar.'},409)
                if action=='/api/assign':
                    session=next((s for s in data['sessions'] if s['id']==body.get('id')),None)
                    if not session: raise ValueError('Sesión no encontrada.')
                    session['teacher']=body.get('teacher')
                    message='Asignación docente actualizada'
                elif user['role']!='manager': return self.send_json({'error':'Solo la gestora puede editar o publicar la planificación.'},403)
                elif action=='/api/save':
                    session=body['session']
                    clean={key:session.get(key) for key in ['id','subject','audience','day','slot','room','teacher','locked']}
                    if not clean['id']: clean['id']='s-'+os.urandom(6).hex()
                    data['sessions']=[s for s in data['sessions'] if s['id']!=clean['id']]+[clean]
                    message='Sesión guardada'
                elif action=='/api/delete':
                    data['sessions']=[s for s in data['sessions'] if s['id']!=body['id']]; message='Sesión eliminada'
                elif action=='/api/generate':
                    data['sessions'],data['missing']=generate(cat,data['sessions'],seed=data['revision'],week=week); message='Borrador regenerado; sesiones fijadas conservadas'
                elif action=='/api/publish':
                    if any(next(t for t in cat['teachers'] if t['id']==s['teacher'])['placeholder'] for s in data['sessions']): raise ValueError('Asigna todos los docentes pendientes antes de publicar.')
                    if coverage(data['sessions'],cat,week): raise ValueError('Faltan sesiones del plan formativo. Completa la cobertura antes de publicar.')
                    data['published']=dict(sessions=copy.deepcopy(data['sessions']),at=dt.datetime.now().isoformat(timespec='seconds'))
                    message='Horario publicado para el alumnado'
                elif action=='/api/season':
                    start=dt.date.fromisoformat(body['start']); end=dt.date.fromisoformat(body['end'])
                    if start>end or (end-start).days>550: raise ValueError('La temporada debe durar entre 1 y 551 días.')
                    name=str(body.get('name','')).strip()[:80]
                    if not name: raise ValueError('Indica el nombre de la temporada.')
                    cat['season']=dict(name=name,start=start.isoformat(),end=end.isoformat())
                    conn.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(cat,ensure_ascii=False),))
                    message='Temporada actualizada; revisa los periodos de las materias'
                elif action=='/api/season-generate':
                    created=0
                    for target in season_weeks(cat):
                        previous=conn.execute('SELECT data FROM weeks WHERE week=?',(target,)).fetchone()
                        if previous:
                            untouched=json.loads(previous[0])
                            if untouched['sessions'] or untouched['published'] or untouched['history']: continue
                        item=new_week(cat,target)
                        item['history'].append(dict(at=item['updated'],user=user['name'],message='Borrador creado desde la planificación de temporada'))
                        conn.execute('INSERT OR REPLACE INTO weeks VALUES (?,?)',(target,json.dumps(item,ensure_ascii=False)))
                        if target==week: data=item
                        created+=1
                    message=f'Temporada planificada · {created} borradores creados; semanas existentes conservadas'
                elif action=='/api/subject':
                    sub=next((s for s in cat['subjects'] if s['id']==body.get('id')),None)
                    if not sub: raise ValueError('Materia no encontrada.')
                    start=dt.date.fromisoformat(body['start']); end=dt.date.fromisoformat(body['end'])
                    if start>end: raise ValueError('La fecha de fin debe ser igual o posterior al inicio.')
                    if 'weekly_hours' not in body and (type(body.get('blocks')) is not int or not 1<=body['blocks']<=10): raise ValueError('Indica entre 1 y 10 bloques semanales.')
                    proposed={**sub,**body,'start':start.isoformat(),'end':end.isoformat()}
                    if 'weekly_hours' not in body:proposed['weekly_hours']=body['blocks']*1.5
                    sub.update(start=start.isoformat(),end=end.isoformat(),**subject_hours(proposed,cat))
                    conn.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(cat,ensure_ascii=False),))
                    # Las fechas son globales: limpiar borradores, nunca versiones publicadas.
                    removed=0
                    for other_week,raw in conn.execute('SELECT week,data FROM weeks').fetchall():
                        other=data if other_week==week else json.loads(raw)
                        kept=[s for s in other['sessions'] if s['subject']!=sub['id'] or active(sub,other_week,s['day'])]
                        quota=subject_blocks(sub,cat,other_week);counts={};trimmed=[]
                        for session in kept:
                            if session['subject']==sub['id']:
                                audience=session['audience'];counts[audience]=counts.get(audience,0)+1
                                if counts[audience]>quota:continue
                            trimmed.append(session)
                        kept=trimmed
                        count=len(other['sessions'])-len(kept); removed+=count
                        if count:
                            other['sessions']=kept
                            if other_week!=week:
                                other['revision']+=1
                                other['updated']=dt.datetime.now().isoformat(timespec='seconds')
                                other['history'].insert(0,dict(at=other['updated'],user=user['name'],message='Borrador ajustado al periodo de '+sub['name']))
                                conn.execute('UPDATE weeks SET data=? WHERE week=?',(json.dumps(other,ensure_ascii=False),other_week))
                    message=f'Periodo de impartición actualizado · {removed} sesiones fuera de periodo retiradas de los borradores'
                elif action=='/api/teacher':
                    teacher=body['teacher']
                    if not str(teacher.get('name','')).strip() or not teacher.get('subjects'): raise ValueError('Indica nombre y al menos una habilitación.')
                    if any(s not in [x['id'] for x in cat['subjects']] for s in teacher['subjects']): raise ValueError('Habilitación no válida.')
                    if not teacher.get('days') or any(type(d) is not int or d not in range(5) for d in teacher['days']): raise ValueError('Selecciona días válidos.')
                    teacher=dict(id='t-'+os.urandom(4).hex(),name=str(teacher['name']).strip()[:80],subjects=teacher['subjects'],days=teacher['days'],placeholder=bool(teacher.get('placeholder')),max_daily=3,max_weekly=12)
                    cat['teachers'].append(teacher)
                    conn.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(cat,ensure_ascii=False),)); message='Docente añadido al catálogo'
                else: return self.send_json({'error':'Acción no encontrada.'},404)
                errors=validate(data['sessions'],cat,week)
                if errors: raise ValueError(' '.join(errors))
                data['revision']+=1; data['updated']=dt.datetime.now().isoformat(timespec='seconds')
                data['history'].insert(0,dict(at=data['updated'],user=user['name'],message=message))
                data['history']=data['history'][:50]
                save_week(conn,week,data)
            self.send_json({'ok':True,'message':message})
        except (ValueError,KeyError,TypeError,StopIteration) as exc: self.send_json({'error':str(exc) or 'Datos no válidos.'},400)

if __name__=='__main__':
    host=os.environ.get('HOST','127.0.0.1'); port=int(os.environ.get('PORT','8000'))
    with connect(): pass
    print(f'Academia disponible en http://{host}:{port}',flush=True)
    ThreadingHTTPServer((host,port),Handler).serve_forever()
