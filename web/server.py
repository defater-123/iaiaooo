#!/usr/bin/env python3
"""
VPS Manager — веб-сервер для управления сервером через GitHub Actions.

Полностью заменяет Telegram-бота: вместо бота поднимается красивый
веб-сайт (статика из web/) + JSON API, через который пользователь:
  - активирует код доступа, полученный из логов GitHub Actions;
  - управляет файловой структурой (просмотр, создание, редактирование,
    удаление, скачивание, загрузка файлов);
  - выполняет команды в терминале с потоковым выводом (SSE);
  - следит за статистикой сервера (CPU / RAM / Disk / Uptime).

Запуск:            python3 web/server.py --port 8080
Регистрация кода:  python3 web/server.py --add-code XXXX:IP:PASSWORD
"""

import argparse
import base64
import json
import logging
import mimetypes
import os
import posixpath
import queue
import secrets
import shutil
import string
import subprocess
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

# ==================== НАСТРОЙКА ====================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("vps-web")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, "web")
SAVES_DIR = os.path.join(BASE_DIR, "Saves")
SESSION_FILE = os.path.join(SAVES_DIR, "sessions.json")
TOKENS_FILE = os.path.join(SAVES_DIR, "tokens.json")

CODE_EXPIRE_MINUTES = 10          # время жизни неиспользованного кода
SESSION_TTL_HOURS = 6             # длительность доступа после активации
CLEANUP_INTERVAL = 300

os.makedirs(SAVES_DIR, exist_ok=True)

# Пользовательские файлы живут внутри папки Action-раннера
FILE_ROOT = os.path.abspath(os.environ.get("VPS_FILE_ROOT", os.getcwd()))


def now_iso():
    return datetime.now().isoformat()


# ==================== GIT ====================

def git_commit_and_push(rel_path: str) -> bool:
    """Коммит и пуш изменённого файла в репозиторий (если это git-репо)."""
    try:
        if not os.path.isdir(os.path.join(BASE_DIR, ".git")):
            return True
        subprocess.run(['git', 'config', '--global', 'user.email', 'vps-web@github.com'],
                       cwd=BASE_DIR, capture_output=True, check=False)
        subprocess.run(['git', 'config', '--global', 'user.name', 'VPS Web'],
                       cwd=BASE_DIR, capture_output=True, check=False)
        status = subprocess.run(['git', 'status', '--porcelain', rel_path],
                                cwd=BASE_DIR, capture_output=True, text=True)
        if not status.stdout:
            return True
        subprocess.run(['git', 'add', rel_path], cwd=BASE_DIR, capture_output=True, check=False)
        subprocess.run(['git', 'commit', '-m', f'Сохранить {os.path.basename(rel_path)} [skip ci]'],
                       cwd=BASE_DIR, capture_output=True, check=False)
        subprocess.run(['git', 'push'], cwd=BASE_DIR, capture_output=True, check=False)
        return True
    except Exception as e:
        logger.error(f"Ошибка git: {e}")
        return False


def save_sessions():
    try:
        with open(SESSION_FILE, 'w') as f:
            json.dump({'sessions': SESSIONS}, f, indent=2)
        rel = os.path.relpath(SESSION_FILE, BASE_DIR)
        git_commit_and_push(rel)
    except Exception as e:
        logger.error(f"Ошибка сохранения сессий: {e}")


def load_tokens():
    try:
        if os.path.exists(TOKENS_FILE):
            with open(TOKENS_FILE) as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки токенов: {e}")
    return {}


def save_tokens(tokens: dict):
    try:
        with open(TOKENS_FILE, 'w') as f:
            json.dump(tokens, f, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения токенов: {e}")


# ==================== СЕССИИ / ДОСТУП ====================
_lock = threading.Lock()

try:
    with open(SESSION_FILE) as f:
        SESSIONS = json.load(f).get('sessions', {})
except Exception:
    SESSIONS = {}

TOKENS = load_tokens()  # token -> {"sid": ..., "created": ...}


def add_code(code: str, ip: str, password: str) -> bool:
    with _lock:
        if code in SESSIONS:
            return False
        SESSIONS[code] = {
            'ip': ip,
            'password': password,
            'token': '',
            'created': now_iso(),
            'expires': (datetime.now() + timedelta(minutes=CODE_EXPIRE_MINUTES)).isoformat(),
            'activated_at': '',
        }
        save_sessions()
        logger.info(f"Код зарегистрирован: {code}")
        return True


def activate_code(code: str):
    """Активация кода из сайта. Возвращает {token, ip, password} или None."""
    with _lock:
        data = SESSIONS.get(code)
        if not data:
            return None
        if datetime.now() > datetime.fromisoformat(data['expires']):
            del SESSIONS[code]
            save_sessions()
            return None
        token = secrets.token_urlsafe(32)
        data['token'] = token
        data['activated_at'] = now_iso()
        data['expires'] = (datetime.now() + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
        TOKENS[token] = {'sid': code, 'created': now_iso()}
        save_tokens(TOKENS)
        save_sessions()
        logger.info(f"Код {code} активирован с сайта")
        return {'token': token, 'ip': data['ip'], 'password': data['password']}


def session_by_token(token: str):
    tid = TOKENS.get(token)
    if not tid:
        return None
    data = SESSIONS.get(tid['sid'])
    if not data or not data.get('token') or data['token'] != token:
        return None
    if datetime.now() > datetime.fromisoformat(data['expires']):
        return None
    return {'sid': tid['sid'], **data}


def revoke_token(token: str) -> bool:
    with _lock:
        tid = TOKENS.pop(token, None)
        if not tid:
            return False
        SESSIONS.pop(tid['sid'], None)
        save_tokens(TOKENS)
        save_sessions()
        return True


def cleanup_expired():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        with _lock:
            changed = False
            for sid in list(SESSIONS.keys()):
                try:
                    if datetime.now() > datetime.fromisoformat(SESSIONS[sid]['expires']):
                        tok = SESSIONS[sid].get('token')
                        if tok and tok in TOKENS:
                            TOKENS.pop(tok)
                        del SESSIONS[sid]
                        changed = True
                except Exception:
                    pass
            if changed:
                save_tokens(TOKENS)
                save_sessions()


# ==================== СИСТЕМНАЯ СТАТИСТИКА ====================

def run_shell(cmd: str, timeout: int = 10) -> str:
    try:
        return subprocess.getoutput(cmd)
    except Exception:
        return ""


def get_stats() -> dict:
    cpu = run_shell("top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1") or "0"
    mem_total, mem_used = "", ""
    free_out = run_shell("free -m | awk 'NR==2 {print $2\" \"$3}'").split()
    if len(free_out) == 2:
        mem_total, mem_used = free_out
    disk_pct = run_shell("df -h / | awk 'NR==2 {print $5}' | tr -d '%'") or "0"
    disk_h = run_shell("df -h / | awk 'NR==2 {print $2\" \"$3}'").split()
    uptime_s = run_shell("awk '{print int($1/60)}' /proc/uptime") or "0"
    try:
        public_ip = subprocess.run(['curl', '-s', '--max-time', '3', 'ifconfig.me'],
                                   capture_output=True, text=True).stdout.strip()
    except Exception:
        public_ip = ""
    return {
        'cpu': f"{cpu}%",
        'ram': f"{mem_used}/{mem_total} MB" if mem_total else "н/д",
        'ram_used': mem_used, 'ram_total': mem_total,
        'disk': f"{disk_pct}%",
        'disk_total': disk_h[0] if len(disk_h) == 2 else "н/д",
        'disk_used': disk_h[1] if len(disk_h) == 2 else "н/д",
        'uptime_min': int(uptime_s) if uptime_s.isdigit() else 0,
        'hostname': run_shell("hostname"),
        'os': run_shell(". /etc/os-release 2>/dev/null && echo $PRETTY_NAME || uname -s"),
        'kernel': run_shell("uname -r"),
        'cores': run_shell("nproc"),
        'ip': public_ip,
        'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ==================== ФАЙЛОВАЯ СИСТЕМА ====================

def safe_path(path: str) -> str:
    """Разрешаем только пути внутри FILE_ROOT (защита от path traversal)."""
    path = unquote(path or '.')
    if not posixpath.isabs(path):
        path = posixpath.join(FILE_ROOT, path)
    norm = os.path.normpath(path)
    if not (norm == FILE_ROOT or norm.startswith(FILE_ROOT + os.sep)):
        raise PermissionError("Путь за пределами сервера")
    return norm


def list_dir(path: str):
    d = safe_path(path)
    items = []
    for name in sorted(os.listdir(d), key=lambda n: (not os.path.isdir(os.path.join(d, n)), n.lower())):
        full = os.path.join(d, name)
        st = os.stat(full)
        items.append({
            'name': name,
            'is_dir': os.path.isdir(full),
            'size': st.st_size,
            'modified': datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            'permissions': oct(st.st_mode)[-3:],
        })
    return {'path': d, 'files': items}


def read_file(path: str):
    p = safe_path(path)
    with open(p, 'rb') as f:
        content = f.read(2 * 1024 * 1024)  # до 2 МБ для редактора
    return {'name': os.path.basename(p), 'content': content.decode('utf-8', errors='replace')}


def write_file(path: str, content: str):
    p = safe_path(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(content)
    try:
        rel = os.path.relpath(p, BASE_DIR)
        if not rel.startswith('..'):
            git_commit_and_push(rel)
    except Exception:
        pass
    return {'success': True, 'path': p}


def delete_path(path: str):
    p = safe_path(path)
    if p == FILE_ROOT:
        raise PermissionError("Нельзя удалить корень")
    if os.path.isdir(p):
        shutil.rmtree(p)
    else:
        os.remove(p)
    return {'success': True}


def make_entry(path: str, is_dir: bool):
    p = safe_path(path)
    if is_dir:
        os.makedirs(p, exist_ok=True)
    else:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'a').close()
    return {'success': True}


def move_entry(src: str, dst: str):
    s, d = safe_path(src), safe_path(dst)
    shutil.move(s, d)
    return {'success': True}


# ==================== ТЕРМИНАЛ (стриминг) ====================

def start_stream(command: str, cwd: str):
    q = queue.Queue()

    def worker():
        try:
            proc = subprocess.Popen(
                command, shell=True, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=0
            )
            for raw in iter(proc.stdout.readline, b''):
                q.put(('out', raw.decode('utf-8', errors='replace')))
            code = proc.wait()
            q.put(('exit', str(code)))
        except Exception as e:
            q.put(('err', str(e)))
            q.put(('exit', '-1'))
        finally:
            q.put(('done', ''))

    threading.Thread(target=worker, daemon=True).start()
    return q


# ==================== HTTP HANDLER ====================

class Handler(BaseHTTPRequestHandler):
    server_version = "VPSWeb/1.1"

    # ---------- утилиты ----------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get('Content-Length', 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            return {}

    def _token(self, qs=None):
        auth = self.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            return auth[7:].strip()
        # EventSource не умеет заголовки — разрешаем токен через query
        if qs and 'auth' in qs:
            return qs['auth'][0]
        return ''

    def _session(self, qs=None):
        return session_by_token(self._token(qs))

    def log_message(self, fmt, *args):
        logger.info("%s %s" % (self.address_string(), fmt % args))

    # ---------- маршрутизация ----------
    def do_GET(self):
        parsed = urlparse(self.path)
        route, qs = parsed.path, parse_qs(parsed.query)

        if route.startswith('/api/'):
            return self.api_get(route, qs)
        return self.serve_static(parsed.path)

    def do_POST(self):
        route = urlparse(self.path).path
        if route.startswith('/api/'):
            return self.api_post(route)
        self._json({'error': 'Not found'}, 404)

    # ---------- статика (сайт) ----------
    def serve_static(self, path):
        if path == '/':
            path = '/index.html'
        # разрешаем только web/
        target = os.path.normpath(os.path.join(WEB_DIR, path.lstrip('/')))
        if not target.startswith(WEB_DIR) or not os.path.isfile(target):
            target = os.path.join(WEB_DIR, 'index.html')
            if not os.path.isfile(target):
                return self._json({'error': 'Site not found'}, 404)
        ctype = mimetypes.guess_type(target)[0] or 'application/octet-stream'
        if ctype.startswith('text/'):
            ctype += '; charset=utf-8'
        with open(target, 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- GET API ----------
    def api_get(self, route, qs):
        if route == '/api/config':
            return self._json({'ok': True})

        sess = self._session(qs)
        if route == '/api/session':
            if not sess:
                return self._json({'trusted': False})
            return self._json({
                'trusted': True,
                'ip': sess['ip'],
                'password': sess['password'],
                'expires': sess['expires'],
                'activated_at': sess['activated_at'],
                'ssh': f"ssh runner@{sess['ip']}",
            })

        if not sess:
            return self._json({'error': 'unauthorized'}, 401)

        if route == '/api/stats':
            st = get_stats()
            st['expires'] = sess['expires']
            return self._json(st)

        if route == '/api/files':
            try:
                return self._json(list_dir(qs.get('path', ['.'])[0]))
            except PermissionError as e:
                return self._json({'error': str(e)}, 403)
            except Exception as e:
                return self._json({'error': str(e)}, 400)

        if route == '/api/read':
            try:
                return self._json(read_file(qs.get('path', [''])[0]))
            except Exception as e:
                return self._json({'error': str(e)}, 400)

        if route == '/api/download':
            try:
                p = safe_path(qs.get('path', [''])[0])
                with open(p, 'rb') as f:
                    data = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Disposition',
                                 f'attachment; filename="{os.path.basename(p)}"')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self._json({'error': str(e)}, 400)
            return

        if route == '/api/stream':
            stream_id = qs.get('id', [''])[0]
            q = STREAMS.get(stream_id)
            if q is None:
                return self._json({'error': 'stream not found'}, 404)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            try:
                while True:
                    try:
                        kind, text = q.get(timeout=20)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        continue
                    payload = json.dumps({'type': kind, 'text': text}, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode('utf-8'))
                    if kind == 'done':
                        STREAMS.pop(stream_id, None)
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        self._json({'error': 'Not found'}, 404)

    # ---------- POST API ----------
    def api_post(self, route):
        body = self._body()

        if route == '/api/activate':
            code = (body.get('code') or '').strip().upper()
            if not code:
                return self._json({'error': 'Введите код доступа'}, 400)
            result = activate_code(code)
            if not result:
                return self._json({'error': 'Неверный или просроченный код'}, 401)
            return self._json(result)

        sess = self._session()
        if not sess:
            return self._json({'error': 'unauthorized'}, 401)

        if route == '/api/revoke':
            revoke_token(self._token())
            return self._json({'success': True})

        if route == '/api/mkdir':
            try:
                base = safe_path(body.get('path', '.'))
                return self._json(make_entry(os.path.join(base, body['name']), True))
            except Exception as e:
                return self._json({'error': str(e)}, 400)

        if route == '/api/touch':
            try:
                base = safe_path(body.get('path', '.'))
                return self._json(make_entry(os.path.join(base, body['name']), False))
            except Exception as e:
                return self._json({'error': str(e)}, 400)

        if route == '/api/write':
            try:
                return self._json(write_file(body.get('path', ''), body.get('content', '')))
            except Exception as e:
                return self._json({'error': str(e)}, 400)

        if route == '/api/delete':
            try:
                return self._json(delete_path(body.get('path', '')))
            except Exception as e:
                return self._json({'error': str(e)}, 400)

        if route == '/api/move':
            try:
                return self._json(move_entry(body['from'], body['to']))
            except Exception as e:
                return self._json({'error': str(e)}, 400)

        if route == '/api/upload':
            try:
                base = safe_path(body.get('path', '.'))
                name = os.path.basename(body.get('name', ''))
                if not name:
                    raise ValueError('Нет имени файла')
                data = base64.b64decode(body.get('content', ''))
                with open(os.path.join(base, name), 'wb') as f:
                    f.write(data)
                return self._json({'success': True})
            except Exception as e:
                return self._json({'error': str(e)}, 400)

        if route == '/api/exec':
            command = (body.get('command') or '').strip()
            if not command:
                return self._json({'error': 'Пустая команда'}, 400)
            try:
                proc = subprocess.run(command, shell=True, cwd=FILE_ROOT,
                                      capture_output=True, timeout=180)
                out = (proc.stdout or b'') + (proc.stderr or b'')
                return self._json({'out': out.decode('utf-8', errors='replace'),
                                   'code': proc.returncode})
            except subprocess.TimeoutExpired:
                return self._json({'error': 'Таймаут выполнения (180с)'}, 504)
            except Exception as e:
                return self._json({'error': str(e)}, 400)

        if route == '/api/command':
            command = (body.get('command') or '').strip()
            if not command:
                return self._json({'error': 'Пустая команда'}, 400)
            stream_id = secrets.token_hex(8)
            STREAMS[stream_id] = start_stream(command, FILE_ROOT)
            return self._json({'id': stream_id})

        self._json({'error': 'Not found'}, 404)


STREAMS = {}


# ==================== ЗАПУСК ====================

def main():
    parser = argparse.ArgumentParser(description="VPS Manager Web Server")
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8080)))
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--add-code', help='Добавить код: CODE:IP:PASSWORD')
    args = parser.parse_args()

    if args.add_code:
        parts = args.add_code.split(':')
        if len(parts) != 3:
            print("Формат: CODE:IP:PASSWORD")
            return 1
        ok = add_code(parts[0].upper(), parts[1], parts[2])
        print(f"Код {parts[0].upper()} {'добавлен' if ok else 'уже существует'}!")
        return 0 if ok else 1

    threading.Thread(target=cleanup_expired, daemon=True).start()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    logger.info("=" * 50)
    logger.info(f"🌐 VPS Manager запущен: http://{args.host}:{args.port}")
    logger.info(f"📁 Корень файлов: {FILE_ROOT}")
    logger.info(f"💾 Данные: {SAVES_DIR}")
    logger.info("=" * 50)
    httpd.serve_forever()


if __name__ == '__main__':
    main()
