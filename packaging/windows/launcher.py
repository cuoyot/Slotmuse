"""Arranque portable: solo interfaz local, datos junto a la aplicacion."""
import argparse
import errno
from pathlib import Path
import sys
import threading
import traceback
import webbrowser

ROOT = Path(__file__).resolve().parent
# El Python embebido usa una ruta de importacion aislada.
sys.path.insert(0, str(ROOT))
import server


def create_http(port):
    try:
        return server.ThreadingHTTPServer(('127.0.0.1', port), server.Handler)
    except OSError as error:
        if port and (error.errno == errno.EADDRINUSE or getattr(error, 'winerror', None) == 10048):
            return server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        raise


def main():
    parser = argparse.ArgumentParser(description='Academia - demo local')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('El puerto debe estar entre 0 y 65535.')
    server.DB = ROOT / 'data' / 'academia.db'
    with server.connect():
        pass
    http = create_http(args.port)
    url = 'http://127.0.0.1:' + str(http.server_port)
    print('\nACADEMIA - DEMO PORTATIL\n', flush=True)
    print('Aplicacion: ' + url, flush=True)
    print('Datos: ' + str(server.DB), flush=True)
    print('\nManten esta ventana abierta mientras pruebas la aplicacion.', flush=True)
    print('Para terminar, cierra esta ventana o pulsa Ctrl+C.\n', flush=True)
    if not args.no_browser:
        timer = threading.Timer(0.5, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        print('\nAplicacion detenida.')
    finally:
        http.server_close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        details = traceback.format_exc()
        print(details, file=sys.stderr)
        try:
            (ROOT / 'error-inicio.txt').write_text(details, encoding='utf-8')
            print('Se ha guardado el detalle en error-inicio.txt.', file=sys.stderr)
        except OSError:
            pass
        sys.exit(1)
