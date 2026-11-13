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

def clock_text(minutes):return f'{minutes//60:02d}:{minutes%60:02d}'

SLOTS = [clock_text(480+i*30)+'–'+clock_text(510+i*30) for i in range(14)]
LEGACY_STARTS=[480,585,705,810]
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
    if cat.get('time_grid_version')!=2:
        for week,raw in conn.execute('SELECT week,data FROM weeks').fetchall():
            data=json.loads(raw)
            for sessions in [data['sessions'],(data.get('published') or {}).get('sessions',[])]:
                for session in sessions:
                    session.setdefault('start_minute',LEGACY_STARTS[session['slot']])
                    session.setdefault('duration_minutes',90)
                    if session['start_minute']%30:session['legacy_time']=True
                    session['slot']=(session['start_minute']-480)//30
            conn.execute('UPDATE weeks SET data=? WHERE week=?',(json.dumps(data,ensure_ascii=False),week))
        cat['time_grid_version']=2
    cat.setdefault('non_teaching_days',[])
    cat.setdefault('space_types',[dict(id=id,name=name,builtin=True) for id,name in [('theory','Aula de teoría'),('tatami','Defensa personal'),('pool','Salvamento acuático'),('outdoor','Preparación física')]])
    cat.setdefault('locations',[dict(id='main',name='Campus principal')])
    for room in cat['rooms']:room.setdefault('location_id','main')
    for subject in cat['subjects']:
        subject.setdefault('start','2026-09-14')
        subject.setdefault('end', {'law':'2027-01-29','procedure':'2027-03-26','swim':'2026-12-18'}.get(subject['id'],'2027-06-25'))
    for subject in cat['subjects']:
        subject.setdefault('room_id',None)
        subject.setdefault('session_minutes',90)
        subject.setdefault('weekly_hours',subject['blocks']*1.5)
        subject.setdefault('total_hours',sum(active(subject,w) for w in season_weeks(cat))*subject['weekly_hours'])
        subject.setdefault('weekly_sessions',(round(subject['weekly_hours']*60)+subject['session_minutes']-1)//subject['session_minutes'])
        subject['required_weeks']=int(__import__('math').ceil(subject['total_hours']/subject['weekly_hours']))
    ensure_pending_teachers(cat)
    cat['teachers'].sort(key=lambda t:(t.get('automatic',False),t['placeholder'],t['name'].casefold()))
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

def course_week(cat,week):
    weeks=season_weeks(cat)
    return min(max(week,weeks[0]),weeks[-1])


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

def audiences_for(subject,group,cat,week=None):
    if subject['scope']=='subgroup':return group['subgroups']
    rooms=[r for r in cat['rooms'] if r['kind']==subject['kind'] and (not subject.get('room_id') or r['id']==subject['room_id'])]
    full=[r for r in rooms if r['capacity']>=group['size']]
    if not week:return [group['id']] if full else group['subgroups']
    duration=min(subject.get('session_minutes',90),subject_week_minutes(subject,cat,week) or subject.get('session_minutes',90))
    def usable(room):return any(active(subject,week,d) and teaching_day(cat,(dt.date.fromisoformat(week)+dt.timedelta(days=d)).isoformat()) and room_available(room,week,d,slot,duration) for d in range(5) for slot in range(len(SLOTS)))
    if any(usable(r) for r in full):return [group['id']]
    # Un cierre total no cambia por sí solo la estructura de alumnos requerida.
    subgroup_size=(group['size']+len(group['subgroups'])-1)//len(group['subgroups'])
    if full and not any(r['capacity']>=subgroup_size and usable(r) for r in rooms):return [group['id']]
    return group['subgroups']


def ensure_pending_teachers(cat):
    # Una reserva por audiencia evita que un puesto pendiente bloquee otro simultáneo.
    for subject in cat['subjects']:
        if any(not t['placeholder'] and subject['id'] in t['subjects'] for t in cat['teachers']):continue
        for group in cat['groups']:
            for audience in [group['id']]+group['subgroups']:
                teacher_id='pending-'+subject['id']+'-'+audience
                if any(t['id']==teacher_id for t in cat['teachers']):continue
                cat['teachers'].append(dict(id=teacher_id,name='Pendiente de asignar · '+subject['name'],subjects=[subject['id']],days=[0,1,2,3,4],placeholder=True,automatic=True,audience=audience,max_daily=14,max_weekly=70))


def teaching_day(cat,date):
    value=dt.date.fromisoformat(date)
    return value.weekday()<5 and not any(p.get('enabled',True) and p['start']<=date<=p['end'] for p in cat.get('non_teaching_days',[]))


def teaching_week(subject,cat,week):
    return any(active(subject,week,day) and teaching_day(cat,(dt.date.fromisoformat(week)+dt.timedelta(days=day)).isoformat()) for day in range(5))


def room_windows(room,date):
    versions=room.get('availability',[])
    if room.get('availability_mode')=='periods':
        applicable=[v for v in versions if v['from']<=date<=v.get('until','9999-12-31')]
    else:applicable=[v for v in versions if v['from']<=date]
    if not applicable:return [] if room.get('availability_mode')=='periods' else [dict(day=day,start='08:00',end='15:00') for day in range(5)]
    return max(applicable,key=lambda v:v['from'])['windows']


def room_periods(room,cat):
    if room.get('availability_mode')=='periods':return copy.deepcopy(room.get('availability',[]))
    start,end=cat['season']['start'],cat['season']['end'];versions=sorted(room.get('availability',[]),key=lambda v:v['from']);result=[]
    if not versions:return [dict(**{'from':start},until=end,windows=[dict(day=day,start='08:00',end='15:00') for day in range(5)])]
    if start<versions[0]['from']:result.append(dict(**{'from':start},until=(dt.date.fromisoformat(versions[0]['from'])-dt.timedelta(days=1)).isoformat(),windows=[dict(day=day,start='08:00',end='15:00') for day in range(5)]))
    for index,v in enumerate(versions):
        until=(dt.date.fromisoformat(versions[index+1]['from'])-dt.timedelta(days=1)).isoformat() if index+1<len(versions) else max(end,v['from'])
        result.append(dict(**{'from':v['from']},until=until,windows=copy.deepcopy(v['windows'])))
    return result


def room_available(room,week,day,slot,duration=90,start_minute=None):
    if not week:return True
    date=(dt.date.fromisoformat(week)+dt.timedelta(days=day)).isoformat()
    start_minute=480+slot*30 if start_minute is None else start_minute
    start,end=clock_text(start_minute),clock_text(start_minute+duration)
    return any(w['day']==day and w['start']<=start and w['end']>=end for w in room_windows(room,date))


def room_conflicts(sessions,cat,week):
    rooms={r['id']:r for r in cat['rooms']}
    bad=[s for s in sessions if not teaching_day(cat,(dt.date.fromisoformat(week)+dt.timedelta(days=s['day'])).isoformat()) or (s['room'] in rooms and not room_available(rooms[s['room']],week,s['day'],s['slot'],session_duration(s,cat),session_start(s)))]
    for session in sessions:
        subject=next((s for s in cat['subjects'] if s['id']==session['subject']),None)
        group=next((g for g in cat['groups'] if session['audience']==g['id'] or session['audience'] in g['subgroups']),None)
        room=rooms.get(session['room'])
        if subject and group and room:
            size=group['size'] if session['audience']==group['id'] else (group['size']+len(group['subgroups'])-1)//len(group['subgroups'])
            if (room['capacity']<size or session['audience'] not in audiences_for(subject,group,cat,week)) and session not in bad:bad.append(session)
    counts={}
    for session in sessions:
        key=(session['subject'],session['audience']);counts[key]=counts.get(key,0)+session_duration(session,cat)
        sub=next((x for x in cat['subjects'] if x['id']==session['subject']),None)
        if sub and counts[key]>subject_week_minutes(sub,cat,week) and session not in bad:bad.append(session)
    splits={s['split_id'] for s in bad if s.get('split_id')}
    return [s for s in sessions if s in bad or s.get('split_id') in splits]


def session_start(session):return session.get('start_minute',480+session['slot']*30)


def session_duration(session,cat=None):
    subject=next((x for x in (cat or {}).get('subjects',[]) if x['id']==session.get('subject')),None)
    return session.get('duration_minutes',(subject or {}).get('session_minutes',90))


def validate(sessions, cat, week=None,allow_partial=False):
    errors=[]
    for index,s in enumerate(sessions):
        tag=f"Sesión {index+1}"
        subject=next((x for x in cat['subjects'] if x['id']==s.get('subject')),None)
        room=next((x for x in cat['rooms'] if x['id']==s.get('room')),None)
        teacher=next((x for x in cat['teachers'] if x['id']==s.get('teacher')),None)
        if not subject or not room or not teacher:
            errors.append(f'{tag}: asignatura, espacio o docente no válido.'); continue
        if type(s.get('day')) is not int or s['day'] not in range(5) or type(s.get('slot')) is not int or s['slot'] not in range(len(SLOTS)):
            errors.append(f'{tag}: franja no válida.'); continue
        duration=session_duration(s,cat);start=session_start(s);end=start+duration
        if type(duration) is not int or duration<30 or duration>420 or duration%30 or type(start) is not int or not 480<=start<900 or end>900:
            errors.append(f'{tag}: duración o inicio no válido.');continue
        if 'start_minute' in s and start%30 and not s.get('legacy_time'):
            errors.append(f'{tag}: el inicio debe ajustarse a intervalos de 30 minutos.');continue
        try: people=participants(s,cat)
        except ValueError as exc: errors.append(str(exc)); continue
        group=next(g for g in cat['groups'] if people <= set(g['subgroups']))
        size=group['size'] if len(people)>1 else (group['size']+len(group['subgroups'])-1)//len(group['subgroups'])
        if week and not teaching_day(cat,(dt.date.fromisoformat(week)+dt.timedelta(days=s['day'])).isoformat()):errors.append(f'{tag}: día no lectivo.')
        if week and not active(subject,week,s['day']): errors.append(f'{tag}: materia fuera de su periodo de impartición.')
        if s['audience'] not in audiences_for(subject,group,cat,week):errors.append(f'{tag}: el aforo requiere otra distribución de grupos o subgrupos.')
        if not room_available(room,week,s['day'],s['slot'],duration,start):errors.append(f'{tag}: espacio fuera de su disponibilidad horaria.')
        if subject.get('room_id') and s['room']!=subject['room_id']:errors.append(f'{tag}: la materia requiere otro espacio concreto.')
        if room['kind']!=subject['kind'] or room['capacity']<size: errors.append(f'{tag}: espacio incompatible o aforo insuficiente.')
        if subject['id'] not in teacher['subjects']: errors.append(f'{tag}: docente sin habilitación para esta materia.')
        if s['day'] not in teacher['days']: errors.append(f'{tag}: docente no disponible este día.')
        same=[x for x in sessions if x.get('teacher')==teacher['id']]
        if len(same)>teacher['max_weekly'] or sum(x.get('day')==s['day'] for x in same)>teacher['max_daily']: errors.append(f'{tag}: carga docente excedida.')
        for other in sessions[:index]:
            if s['day']!=other.get('day') or type(other.get('slot')) is not int:continue
            other_room=next((r for r in cat['rooms'] if r['id']==other.get('room')),None)
            other_start=session_start(other);other_end=other_start+session_duration(other,cat)
            try:shared=bool(people & participants(other,cat))
            except ValueError:shared=False
            overlap=start<other_end and other_start<end
            if overlap:
                if s['room']==other.get('room'):errors.append(f'{tag}: el espacio ya está ocupado.')
                if s['teacher']==other.get('teacher'):errors.append(f'{tag}: el docente tiene otra sesión simultánea.')
                if shared:errors.append(f'{tag}: cruce de alumnos (grupo/subgrupo).')
            elif other_room and room.get('location_id','main')!=other_room.get('location_id','main'):
                gap=max(start,other_start)-min(end,other_end)
                if gap<30 and (shared or s['teacher']==other.get('teacher')):
                    errors.append(f'{tag}: se necesitan 30 minutos para desplazarse entre ubicaciones.')
    bundles={}
    for session in sessions:
        if session.get('split_id'):bundles.setdefault(session['split_id'],[]).append(session)
    for bundle in bundles.values():
        if not allow_partial and len(bundle)!=bundle[0].get('split_size',2):errors.append('Desdoblamiento incompleto: reajusta el bloque completo.')
        stamps={(s['day'],session_start(s),session_duration(s,cat)) for s in bundle}
        locations={next((r.get('location_id','main') for r in cat['rooms'] if r['id']==s['room']),None) for s in bundle}
        if len(stamps)>1 or len(locations)>1:errors.append('El desdoblamiento debe compartir día, horario y ubicación.')
    if week:
        for sub in cat['subjects']:
            if 'total_hours' not in sub:continue
            quota=subject_week_minutes(sub,cat,week)
            for group in cat['groups']:
                for audience in audiences_for(sub,group,cat,week):
                    if sum(session_duration(s,cat) for s in sessions if s['subject']==sub['id'] and s['audience']==audience)>quota:
                        errors.append(sub['name']+': se supera la carga horaria prevista para esta semana.')
    return list(dict.fromkeys(errors))

def subject_hours(item,cat=None):
    weekly=item.get('weekly_hours',item.get('blocks',1)*1.5);total=item.get('total_hours')
    duration=item.get('session_minutes',90)
    if type(duration) is not int or not 30<=duration<=420 or duration%30:
        raise ValueError('La duración debe ser un múltiplo de 30 minutos, entre 30 y 420.')
    count=item.get('weekly_sessions')
    if count is not None:
        if type(count) is not int or not 1<=count<=70:raise ValueError('Indica entre 1 y 70 sesiones semanales.')
        weekly=count*duration/60
    for value,label in [(weekly,'Horas semanales'),(total,'Horas totales')]:
        if value is None and label=='Horas totales':continue
        if type(value) not in (int,float) or not 0<value<=10000 or abs(value*2-round(value*2))>1e-8:
            raise ValueError(label+': indica un múltiplo positivo de 0,5 horas.')
    if weekly>35:raise ValueError('Las horas semanales no pueden superar 35 horas.')
    if total is None:total=sum(active(item,w) for w in season_weeks(cat))*weekly if cat else weekly
    if total<=0:raise ValueError('El periodo debe incluir al menos una semana de formación.')
    return dict(weekly_hours=weekly,total_hours=total,session_minutes=duration,weekly_sessions=count if count is not None else (round(weekly*60)+duration-1)//duration,required_weeks=int(__import__('math').ceil(total/weekly)),blocks=(round(weekly*60)+duration-1)//duration)


def subject_week_minutes(subject,cat,week=None):
    weekly=round(subject.get('weekly_hours',subject.get('blocks',1)*1.5)*60)
    if week and not teaching_week(subject,cat,week):return 0
    if 'total_hours' not in subject or week is None:return weekly
    weeks=[w for w in season_weeks(cat) if teaching_week(subject,cat,w)]
    if week not in weeks:return 0
    return max(0,min(weekly,round(subject['total_hours']*60)-weeks.index(week)*weekly))


def split_minutes(minutes,duration):
    return [duration]*(minutes//duration)+([minutes%duration] if minutes%duration else [])


def subject_blocks(subject,cat,week=None):
    return len(split_minutes(subject_week_minutes(subject,cat,week),subject.get('session_minutes',90)))


def coverage_tasks(sessions,cat,week=None):
    tasks=[]
    for subject in cat['subjects']:
        quota=subject_week_minutes(subject,cat,week)
        for group in cat['groups']:
            for audience in audiences_for(subject,group,cat,week):
                done=sum(session_duration(s,cat) for s in sessions if s['subject']==subject['id'] and s['audience']==audience)
                tasks.extend((subject['id'],audience,duration) for duration in split_minutes(max(0,quota-done),subject.get('session_minutes',90)))
    return tasks


def requirements(cat,week=None):return [(s,a) for s,a,_ in coverage_tasks([],cat,week)]


def coverage(sessions,cat,week=None):return [(s,a) for s,a,_ in coverage_tasks(sessions,cat,week)]

def rotation_packs(tasks,cat):
    """Emparejar componentes técnicos de un grupo en una ubicación compartida."""
    remaining=list(tasks);packs=[]
    def group_of(audience):return next((g for g in cat['groups'] if audience in g['subgroups']),None)
    def locations(task):
        sub=next(s for s in cat['subjects'] if s['id']==task[0]);group=group_of(task[1])
        if not group or sub.get('align_splits',True) is False:return set()
        size=(group['size']+len(group['subgroups'])-1)//len(group['subgroups'])
        return {r.get('location_id','main') for r in cat['rooms'] if r['kind']==sub['kind'] and r['capacity']>=size and (not sub.get('room_id') or r['id']==sub['room_id'])}
    while remaining:
        first=remaining.pop(0);group=group_of(first[1]);common=locations(first);pack=[first]
        if common and group:
            for audience in group['subgroups']:
                if audience==first[1]:continue
                partner=next((t for t in remaining if t[1]==audience and t[0] not in [p[0] for p in pack] and t[2]==first[2] and common & locations(t)),None)
                if partner:
                    remaining.remove(partner);pack.append(partner);common &= locations(partner)
        # Solo un bloque que cubra todos los subgrupos se presenta como desdoblamiento.
        if group and len(pack)>1 and len(pack)!=len(group['subgroups']):
            remaining.extend(pack[1:]);pack=pack[:1]
        packs.append((pack,common if len(pack)>1 else None))
    return packs


def generate(cat, existing=None, seed=1, week=None):
    locked=[copy.deepcopy(s) for s in (existing or []) if s.get('locked')]
    if validate(locked,cat,week):raise ValueError('Las sesiones fijadas contienen conflictos.')
    ensure_pending_teachers(cat)
    tasks=coverage_tasks(locked,cat,week);rng=random.Random(seed);best=None
    packs=rotation_packs(tasks,cat)
    for attempt in range(40):
        sessions=copy.deepcopy(locked);ordered=list(packs);rng.shuffle(ordered)
        ordered.sort(key=lambda p:(len(p[0])==1,next(s for s in cat['subjects'] if s['id']==p[0][0][0])['kind']=='theory'))
        missing=[]
        for pack,locations in ordered:
            candidates=[];split_id='split-'+os.urandom(6).hex() if len(pack)>1 else None
            for day in range(5):
                for slot in range(len(SLOTS)):
                    for location in sorted(locations or {None},key=str):
                        def place(index,placed):
                            if index==len(pack):return placed
                            subject_id,audience,duration=pack[index];subject=next(s for s in cat['subjects'] if s['id']==subject_id)
                            teachers=sorted(cat['teachers'],key=lambda t:t['placeholder'])
                            for room in cat['rooms']:
                                if room['kind']!=subject['kind'] or (subject.get('room_id') and room['id']!=subject['room_id']) or (location is not None and room.get('location_id','main')!=location) or not room_available(room,week,day,slot,duration):continue
                                for teacher in teachers:
                                    if subject_id not in teacher['subjects'] or (teacher.get('automatic') and teacher.get('audience')!=audience):continue
                                    item=dict(id='s-'+os.urandom(6).hex(),subject=subject_id,audience=audience,day=day,slot=slot,start_minute=480+slot*30,duration_minutes=duration,room=room['id'],teacher=teacher['id'],locked=False)
                                    if split_id:item.update(split_id=split_id,split_size=len(pack))
                                    if validate(sessions+placed+[item],cat,week,allow_partial=True):continue
                                    result=place(index+1,placed+[item])
                                    if result is not None:return result
                            return None
                        placed=place(0,[])
                        if placed:
                            people=set().union(*(participants(s,cat) for s in placed))
                            daily=[s for s in sessions if s['day']==day and people & participants(s,cat)]
                            repeated=sum(s['subject'] in {p[0] for p in pack} for s in daily)
                            score=len(daily)*3+repeated*9+slot*.25+sum(2 for s in placed if next(t for t in cat['teachers'] if t['id']==s['teacher'])['placeholder'])+rng.random()*2
                            candidates.append((score,placed))
            if candidates:sessions.extend(min(candidates,key=lambda x:x[0])[1])
            else:missing.extend((subject,audience) for subject,audience,_ in pack)
        if best is None or len(missing)<len(best[1]):best=(sessions,missing)
        if not missing:break
    return best


def repair_sessions(data,cat,week):
    bad={s['id'] for s in room_conflicts(data['sessions'],cat,week)}
    preserved=[copy.deepcopy(s) for s in data['sessions'] if s['id'] not in bad]
    locks={s['id']:s.get('locked',False) for s in preserved}
    for s in preserved:s['locked']=True
    sessions,missing=generate(cat,existing=preserved,week=week)
    for s in sessions:
        if s['id'] in locks:s['locked']=locks[s['id']]
    return sessions,missing


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
                    cat=get_catalog(conn); week=course_week(cat,week); data=load_week(conn,week,cat)
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
                cat=get_catalog(conn); week=course_week(cat,week); data=load_week(conn,week,cat)
                if body.get('revision')!=data['revision']: return self.send_json({'error':'Otro usuario ha cambiado esta semana. Recarga antes de guardar.'},409)
                if action=='/api/assign':
                    session=next((s for s in data['sessions'] if s['id']==body.get('id')),None)
                    if not session: raise ValueError('Sesión no encontrada.')
                    session['teacher']=body.get('teacher')
                    message='Asignación docente actualizada'
                elif user['role']!='manager': return self.send_json({'error':'Solo la gestora puede editar o publicar la planificación.'},403)
                elif action=='/api/save':
                    session=body['session']
                    session.setdefault('duration_minutes',next((s.get('session_minutes',90) for s in cat['subjects'] if s['id']==session.get('subject')),90))
                    clean={key:session.get(key) for key in ['id','subject','audience','day','slot','room','teacher','locked','duration_minutes']}
                    previous=next((s for s in data['sessions'] if s['id']==clean['id']),None)
                    if previous and previous.get('split_id'):
                        clean.update(split_id=previous['split_id'],split_size=previous['split_size'])
                        for peer in data['sessions']:
                            if peer.get('split_id')==previous['split_id'] and peer['id']!=previous['id']:
                                peer.update(day=clean['day'],slot=clean['slot'],duration_minutes=clean['duration_minutes'],start_minute=480+clean['slot']*30)
                                peer.pop('legacy_time',None)
                    if previous and previous.get('legacy_time') and clean['slot']==previous['slot']:
                        clean.update(start_minute=previous['start_minute'],legacy_time=True)
                    if not clean['id']: clean['id']='s-'+os.urandom(6).hex()
                    data['sessions']=[s for s in data['sessions'] if s['id']!=clean['id']]+[clean]
                    message='Sesión guardada'
                elif action=='/api/delete':
                    target=next((s for s in data['sessions'] if s['id']==body['id']),None)
                    split=target.get('split_id') if target else None
                    data['sessions']=[s for s in data['sessions'] if s['id']!=body['id'] and (not split or s.get('split_id')!=split)]; message='Desdoblamiento eliminado' if split else 'Sesión eliminada'
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
                    prior_cat=copy.deepcopy(cat)
                    cat['season']=dict(name=name,start=start.isoformat(),end=end.isoformat())
                    if 'non_teaching_days' in body:
                        periods=body['non_teaching_days']
                        if not isinstance(periods,list) or len(periods)>100:raise ValueError('Indica hasta 100 periodos no lectivos.')
                        clean=[]
                        for item in periods:
                            a=dt.date.fromisoformat(item['start']);b=dt.date.fromisoformat(item['end'])
                            if a>b or (b-a).days>550:raise ValueError('Periodo no lectivo no válido.')
                            label=str(item.get('name','')).strip()[:100]
                            if not label:raise ValueError('Indica el motivo del periodo no lectivo.')
                            clean.append(dict(id=item.get('id') or os.urandom(6).hex(),name=label,start=a.isoformat(),end=b.isoformat(),enabled=item.get('enabled',True) is True))
                        cat['non_teaching_days']=clean
                    for target,raw in conn.execute('SELECT week,data FROM weeks').fetchall():
                        current=data if target==week else json.loads(raw)
                        if room_conflicts(current['sessions'],cat,target) or coverage(current['sessions'],cat,target)!=coverage(current['sessions'],prior_cat,target):
                            current['sessions'],current['missing']=repair_sessions(current,cat,target)
                            current['revision']+=1
                            current['updated']=dt.datetime.now().isoformat(timespec='seconds')
                            current['history'].insert(0,dict(at=current['updated'],user=user['name'],message='Curso y periodos no lectivos: borrador reajustado'))
                            if target!=week:save_week(conn,target,current)
                    cat['revision']=cat.get('revision',1)+1
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
                    if 'weekly_sessions' not in body and 'weekly_hours' not in body and (type(body.get('blocks')) is not int or not 1<=body['blocks']<=10): raise ValueError('Indica entre 1 y 10 bloques semanales.')
                    proposed={**sub,**body,'start':start.isoformat(),'end':end.isoformat()}
                    if 'weekly_sessions' not in body:proposed.pop('weekly_sessions',None)
                    if 'weekly_sessions' not in body and 'weekly_hours' not in body:proposed['weekly_hours']=body['blocks']*1.5
                    room_id=proposed.get('room_id') or None
                    if room_id and not any(r['id']==room_id and r['kind']==sub['kind'] for r in cat['rooms']):raise ValueError('Selecciona un espacio compatible con la materia.')
                    sub.update(start=start.isoformat(),end=end.isoformat(),room_id=room_id,**subject_hours(proposed,cat))
                    cat['revision']=cat.get('revision',1)+1
                    conn.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(cat,ensure_ascii=False),))
                    # Las fechas son globales: limpiar borradores, nunca versiones publicadas.
                    removed=0
                    for other_week,raw in conn.execute('SELECT week,data FROM weeks').fetchall():
                        other=data if other_week==week else json.loads(raw)
                        original_sessions=json.dumps(other['sessions'],sort_keys=True)
                        kept=[s for s in other['sessions'] if s['subject']!=sub['id'] or active(sub,other_week,s['day'])]
                        quota=subject_week_minutes(sub,cat,other_week);counts={};trimmed=[]
                        for session in kept:
                            if session['subject']==sub['id']:
                                audience=session['audience'];remaining=quota-counts.get(audience,0)
                                if remaining<=0:continue
                                session['duration_minutes']=min(session_duration(session,cat),remaining)
                                counts[audience]=counts.get(audience,0)+session['duration_minutes']
                            trimmed.append(session)
                        kept=trimmed
                        count=len(other['sessions'])-len(kept); removed+=count
                        if count or original_sessions!=json.dumps(kept,sort_keys=True):
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
