"""Comprobaciones del artefacto portable con el Python del equipo anfitrion."""
import hashlib
import json
from pathlib import Path
import queue
import re
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import urlopen
import zipfile

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'dist'/'Academia-demo-Windows11-x64-v0.1.zip'

@unittest.skipUnless(PACKAGE.exists(), 'Construir antes el ZIP Windows')
class WindowsPackage(unittest.TestCase):
    def test_archive_integrity_and_manifest(self):
        with zipfile.ZipFile(PACKAGE) as archive:
            self.assertIsNone(archive.testzip())
            prefix='Academia-demo-Windows11-x64/'
            manifest=json.loads(archive.read(prefix+'PAQUETE.json'))
            for name,digest in manifest['files'].items():
                self.assertEqual(hashlib.sha256(archive.read(prefix+name)).hexdigest(),digest,name)
            executable=archive.read(prefix+'runtime/python.exe')
            self.assertEqual(executable[:2],b'MZ')
            pe=struct.unpack_from('<I',executable,0x3c)[0]
            self.assertEqual(executable[pe:pe+4],b'PE\0\0')
            self.assertEqual(struct.unpack_from('<H',executable,pe+4)[0],0x8664)
            for name in ['runtime/_sqlite3.pyd','runtime/sqlite3.dll','runtime/LICENSE.txt','INICIAR.cmd','launcher.py','static/app.js']:
                self.assertIn(prefix+name,archive.namelist())
    def test_launcher_isolated_path_and_database(self):
        with tempfile.TemporaryDirectory(prefix='Academia prueba ñ ') as tmp:
            with zipfile.ZipFile(PACKAGE) as archive:archive.extractall(tmp)
            folder=Path(tmp)/'Academia-demo-Windows11-x64'
            with sqlite3.connect(folder/'data/academia.db') as conn:
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM weeks').fetchone()[0],41)
            # -I reproduce la ausencia del directorio del script en sys.path
            # del runtime embebido. Los binarios Windows se verifican aparte.
            process=subprocess.Popen([sys.executable,'-I',str(folder/'launcher.py'),'--no-browser','--port','0'],cwd=tmp,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            output=queue.Queue()
            def read():
                for line in process.stdout:output.put(line)
            threading.Thread(target=read,daemon=True).start()
            try:
                url=None
                for _ in range(15):
                    line=output.get(timeout=10)
                    match=re.search(r'http://127\.0\.0\.1:\d+',line)
                    if match:url=match.group();break
                self.assertIsNotNone(url)
                with urlopen(url+'/api/state?week=2026-11-09',timeout=10) as response:
                    state=json.load(response)
                self.assertIsNotNone(state['data']['published'])
                self.assertEqual(len(state['season_summary']),41)
                with urlopen(url+'/',timeout=5) as response:
                    self.assertIn('Planificación docente',response.read().decode())
            finally:
                process.terminate()
                process.wait(timeout=10)
                process.stdout.close()

if __name__=='__main__':unittest.main()
