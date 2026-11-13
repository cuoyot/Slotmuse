#!/usr/bin/env python3
"""Crear una distribucion Windows portable sin herramientas de compilacion."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server

RUNTIME_URL = 'https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip'
RUNTIME_SHA256 = '90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159'
NAME = 'Academia-demo-Windows11-x64'


def build(runtime, output):
    actual = hashlib.sha256(runtime.read_bytes()).hexdigest()
    if actual != RUNTIME_SHA256:
        raise ValueError('El runtime no coincide con el SHA-256 oficial de Python 3.14.10 x64.')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='academia-package-') as temporary:
        folder = Path(temporary) / NAME
        folder.mkdir()
        for name in ['server.py', 'README.md']:
            shutil.copy2(ROOT / name, folder / name)
        shutil.copytree(ROOT / 'static', folder / 'static')
        for path in (ROOT / 'packaging' / 'windows').iterdir():
            if path.is_file():
                if path.suffix in ['.cmd', '.txt']:
                    (folder / path.name).write_bytes(path.read_text().replace('\r\n','\n').replace('\n','\r\n').encode('utf-8'))
                else:
                    shutil.copy2(path, folder / path.name)
        with zipfile.ZipFile(runtime) as archive:
            if archive.testzip():
                raise ValueError('Runtime corrupto.')
            for member in archive.namelist():
                if Path(member).is_absolute() or '..' in Path(member).parts:
                    raise ValueError('Ruta no permitida en el runtime.')
            archive.extractall(folder / 'runtime')
        original_db = server.DB
        try:
            server.DB = folder / 'data' / 'academia.db'
            with server.connect() as conn:
                cat = server.get_catalog(conn)
                conn.execute('UPDATE settings SET data=? WHERE id=1', (json.dumps(cat, ensure_ascii=False),))
                for week in server.season_weeks(cat):
                    data = server.new_week(cat, week)
                    if week == '2026-11-09':
                        for session in data['sessions']:
                            if session['teacher'] == 'p2':
                                session['teacher'] = 't4'
                        data['published'] = dict(sessions=data['sessions'], at='2026-11-13T09:00:00')
                    server.save_week(conn, week, data)
                if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Base de datos inicial no valida.')
        finally:
            server.DB = original_db
        manifest = dict(version='0.1', platform='Windows 11 x64', runtime='CPython 3.14.10 embeddable amd64',
                        runtime_url=RUNTIME_URL, runtime_sha256=actual,
                        runtime_source='https://www.python.org/downloads/release/python-31410/',
                        data='Datos ficticios nuevos; no se copia la base de datos de desarrollo.',
                        files={str(p.relative_to(folder)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sorted(folder.rglob('*')) if p.is_file()})
        (folder / 'PAQUETE.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        target = output / (NAME + '-v0.1.zip')
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(folder.rglob('*')):
                if path.is_file():
                    archive.write(path, str(path.relative_to(folder.parent)))
        with zipfile.ZipFile(target) as archive:
            if archive.testzip():
                raise ValueError('El paquete final esta corrupto.')
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        target.with_suffix('.zip.sha256').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
        print(str(target))
        print(f'{target.stat().st_size / 1024 / 1024:.1f} MiB - SHA-256 {digest}')
        return target


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'dist')
    args=parser.parse_args()
    build(args.runtime, args.output)
