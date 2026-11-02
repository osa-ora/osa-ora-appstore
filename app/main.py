from __future__ import annotations
import base64, copy, hashlib, hmac, json, os, secrets, signal, subprocess, sys, threading, time, traceback, shutil, shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import urllib.request
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "launcher.json"
APP_DIR = ROOT / "app"
RUNTIME_DIR = ROOT / "runtime"
PROCESS_FILE = RUNTIME_DIR / "app_processes.json"

def default_command(action: str) -> str:
    if os.name == 'nt':
        return f'run.bat {action}'
    return f'bash run.sh {action}'

DEFAULT_CONFIG = {"password": None, "theme": "dark", "apps": [
 {"id":"private-space","title":"My Private Space","icon":"/static/images/private-space.svg","path":"../my-private-space","start_command":default_command('start'),"stop_command":default_command('stop'),"status_command":default_command('status'),"url":"http://127.0.0.1:8766","hidden":False,"order":0},
 {"id":"iptv","title":"IPTV","icon":"/static/images/iptv.svg","path":"../iptv","start_command":default_command('start'),"stop_command":default_command('stop'),"status_command":default_command('status'),"url":"http://127.0.0.1:8767","hidden":False,"order":1},
 {"id":"demo-management","title":"Demo Management","icon":"/static/images/demo-management.svg","path":"../demo-management","start_command":default_command('start'),"stop_command":default_command('stop'),"status_command":default_command('status'),"url":"http://127.0.0.1:8768","hidden":False,"order":2},
 {"id":"book-ads-animator","title":"Book Ads Animator","icon":"/static/images/book-ads-animator.svg","path":"../book-ads-animator","start_command":default_command('start'),"stop_command":default_command('stop'),"status_command":default_command('status'),"url":"http://127.0.0.1:8769","hidden":False,"order":3}
]}
lock = threading.RLock()
state = {"hidden_unlocked": False, "unlock_until": 0.0}
shutdown_lock = threading.Lock()
shutdown_timer = None

def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_FILE.read_text())
        if not isinstance(data, dict):
            raise ValueError("Launcher configuration must be a JSON object")
    except Exception:
        data = copy.deepcopy(DEFAULT_CONFIG)
    data.setdefault("apps", [])
    data.setdefault("theme", "dark")
    data.setdefault("password", None)
    if not isinstance(data["apps"], list):
        data["apps"] = []
    # Migrate older single-script and split-script settings to generic commands.
    migrated = False
    for configured_app in data['apps']:
        if not isinstance(configured_app, dict):
            continue
        old_script = configured_app.pop('run_script', None)
        old_start = configured_app.pop('start_script', None)
        old_stop = configured_app.pop('stop_script', None)
        if not configured_app.get('start_command'):
            if old_script:
                script = str(old_script)
                prefix = f'"{script}"' if ' ' in script else script
                if script.lower().endswith('.sh') and os.name != 'nt':
                    prefix = f'bash "{script}"'
                configured_app['start_command'] = f'{prefix} start'
            elif old_start:
                configured_app['start_command'] = f'bash "{old_start}"' if str(old_start).endswith('.sh') and os.name != 'nt' else f'"{old_start}"'
            else:
                configured_app['start_command'] = default_command('start')
            migrated = True
        if 'stop_command' not in configured_app:
            if old_script:
                script = str(old_script)
                prefix = f'bash "{script}"' if script.lower().endswith('.sh') and os.name != 'nt' else f'"{script}"'
                configured_app['stop_command'] = f'{prefix} stop'
            elif old_stop:
                configured_app['stop_command'] = f'bash "{old_stop}"' if str(old_stop).endswith('.sh') and os.name != 'nt' else f'"{old_stop}"'
            else:
                configured_app['stop_command'] = ''
            migrated = True
        if 'status_command' not in configured_app:
            if old_script:
                script = str(old_script)
                prefix = f'bash "{script}"' if script.lower().endswith('.sh') and os.name != 'nt' else f'"{script}"'
                configured_app['status_command'] = f'{prefix} status'
            else:
                configured_app['status_command'] = ''
            migrated = True
    existing_ids = {item.get('id') for item in data['apps'] if isinstance(item, dict)}
    for default_app in DEFAULT_CONFIG['apps']:
        if default_app['id'] not in existing_ids:
            data['apps'].append(copy.deepcopy(default_app))
            migrated = True
    if migrated or not CONFIG_FILE.exists():
        save_config(data)
    return data

def save_config(data):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, CONFIG_FILE)

def password_hash(password: str, salt: bytes | None = None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 310000)
    return {"salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(digest).decode(), "iterations": 310000}

def verify_password(password, stored):
    try:
        salt = base64.b64decode(stored['salt'])
        expected = base64.b64decode(stored['hash'])
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, int(stored.get('iterations',310000)))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False

def app_path(app):
    p = Path(app.get('path','')).expanduser()
    return (ROOT / p).resolve() if not p.is_absolute() else p.resolve()

def probe(url):
    try:
        req = urllib.request.Request(url, method='GET', headers={'User-Agent':'OsaOraAppStore/1.0'})
        with urllib.request.urlopen(req, timeout=1.5) as response:
            return response.status < 500
    except Exception:
        return False

def status_for(app):
    path = app_path(app)
    if not path.exists():
        return 'unavailable'
    return 'running' if probe(app.get('url','')) else 'stopped'

def public_app(app, include_status=True):
    item = {k: app.get(k) for k in ('id','title','icon','path','start_command','stop_command','status_command','url','hidden','order')}
    if include_status:
        item['status'] = status_for(app)
    return item

app = FastAPI(title='Osa Ora App Store', version='1.0.0')
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:8780','http://localhost:8780'], allow_methods=['*'], allow_headers=['*'])
app.mount('/static', StaticFiles(directory=APP_DIR/'static'), name='static')

class PasswordBody(BaseModel): password: str = Field(min_length=8, max_length=256)
class UnlockBody(BaseModel): password: str
class AppBody(BaseModel):
    id: str | None = None
    title: str = Field(min_length=1, max_length=80)
    icon: str = '/static/images/default-app.svg'
    path: str = Field(min_length=1)
    start_command: str = Field(min_length=1, max_length=2000)
    stop_command: str = ''
    status_command: str = ''
    url: str = Field(min_length=1)
    hidden: bool = False
    order: int = 0
class OrderBody(BaseModel): ids: list[str]
class ThemeBody(BaseModel): theme: str

@app.get('/', response_class=HTMLResponse)
def home(): return (APP_DIR/'templates'/'index.html').read_text()

@app.get('/api/config')
def get_config():
    cfg=load_config()
    visible=sorted([a for a in cfg['apps'] if not a.get('hidden')], key=lambda x:x.get('order',0))
    return {"password_set": bool(cfg.get('password')), "theme":cfg.get('theme','dark'), "apps":[public_app(a) for a in visible]}

@app.get('/api/hidden')
def get_hidden():
    if not state['hidden_unlocked'] or time.time() > state['unlock_until']:
        state['hidden_unlocked']=False
        raise HTTPException(403,'Unlock required')
    cfg=load_config()
    return {"apps":[public_app(a) for a in sorted([a for a in cfg['apps'] if a.get('hidden')],key=lambda x:x.get('order',0))]}

@app.post('/api/password')
def set_password(body: PasswordBody):
    cfg=load_config()
    if cfg.get('password'):
        raise HTTPException(409,'Password already configured')
    cfg['password']=password_hash(body.password)
    save_config(cfg)
    return {"ok":True}

@app.post('/api/unlock')
def unlock(body: UnlockBody):
    cfg=load_config()
    if not cfg.get('password') or not verify_password(body.password,cfg['password']):
        raise HTTPException(401,'Incorrect password')
    state['hidden_unlocked']=True
    state['unlock_until']=time.time()+1800
    return {"ok":True}

@app.post('/api/lock')
def lock_hidden():
    state['hidden_unlocked']=False
    state['unlock_until']=0
    return {"ok":True}

@app.get('/api/settings')
def settings():
    cfg=load_config()
    return {"theme":cfg.get('theme','dark'),"apps":[public_app(a,False) for a in sorted(cfg['apps'],key=lambda x:x.get('order',0))]}

@app.post('/api/theme')
def set_theme(body: ThemeBody):
    if body.theme not in ('dark','light','midnight'): raise HTTPException(400,'Invalid theme')
    cfg=load_config(); cfg['theme']=body.theme; save_config(cfg)
    return {"ok":True}

@app.post('/api/apps')
def save_app(body: AppBody):
    parsed=urlparse(body.url)
    if parsed.scheme not in ('http','https') or not parsed.netloc: raise HTTPException(400,'Application URL must be an HTTP or HTTPS URL')
    cfg=load_config(); apps=cfg['apps']
    data=body.model_dump()
    if data['id']:
        found=next((a for a in apps if a['id']==data['id']),None)
        if not found: raise HTTPException(404,'Application not found')
        data['order'] = found.get('order', 0)
        found.update(data)
    else:
        data['id']=secrets.token_hex(6)
        data['order']=max([a.get('order',0) for a in apps],default=-1)+1
        apps.append(data)
    save_config(cfg)
    return {"ok":True,"app":public_app(data,False)}

@app.delete('/api/apps/{app_id}')
def delete_app(app_id: str):
    cfg=load_config(); old=len(cfg['apps']); cfg['apps']=[a for a in cfg['apps'] if a['id']!=app_id]
    if len(cfg['apps'])==old: raise HTTPException(404,'Application not found')
    save_config(cfg); return {"ok":True}

@app.post('/api/order')
def reorder(body: OrderBody):
    cfg=load_config(); mapping={a['id']:a for a in cfg['apps']}
    for i, app_id in enumerate(body.ids):
        if app_id in mapping: mapping[app_id]['order']=i
    save_config(cfg); return {"ok":True}

def _lifecycle_log_path(app_id: str) -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = ''.join(ch for ch in str(app_id) if ch.isalnum() or ch in ('-', '_')) or 'application'
    return RUNTIME_DIR / f'{safe_id}.lifecycle.log'


def _append_lifecycle_log(app_id: str, *, action: str, command: str, folder: Path,
                          started_at: float, returncode: int | None = None,
                          stdout: str = '', stderr: str = '', error: str = '',
                          shell: str = '', pid: int | None = None) -> Path:
    log_path = _lifecycle_log_path(app_id)
    duration = max(0.0, time.time() - started_at)
    lines = [
        '\n' + '=' * 88,
        f'Timestamp: {time.strftime("%Y-%m-%d %H:%M:%S %z")}',
        f'Application ID: {app_id}',
        f'Action: {action}',
        f'Application directory (cwd): {folder}',
        f'Configured command: {command!r}',
        f'Execution mode: shell=True; shell executable={shell or "platform default"}',
        f'PID: {pid if pid is not None else "not started"}',
        f'Duration seconds: {duration:.3f}',
        f'Exit code: {returncode if returncode is not None else "not available"}',
    ]
    if error:
        lines += ['--- ERROR / TRACEBACK ---', error]
    if stdout:
        lines += ['--- STDOUT ---', stdout.rstrip()]
    if stderr:
        lines += ['--- STDERR ---', stderr.rstrip()]
    with log_path.open('a', encoding='utf-8', errors='replace') as f:
        f.write('\n'.join(lines) + '\n')
    return log_path


def _shell_executable() -> str | None:
    if os.name == 'nt':
        return os.environ.get('COMSPEC') or shutil.which('cmd.exe')
    # Use Bash explicitly so command syntax is predictable across macOS/Linux.
    return shutil.which('bash') or ('/bin/bash' if Path('/bin/bash').exists() else '/bin/sh')


def _run_shell_command(app_id: str, action: str, command: str, folder: Path, timeout: int = 30):
    """Run the exact configured command with cwd set to the application folder.

    Do not build a `cd ... && ...` shell string: cwd is the safe and reliable
    equivalent, and paths containing spaces/quotes are handled by the OS.
    """
    started_at = time.time()
    shell = _shell_executable()
    try:
        kwargs = dict(cwd=str(folder), shell=True, capture_output=True, text=True,
                      timeout=timeout, check=False, errors='replace')
        if shell:
            kwargs['executable'] = shell
        result = subprocess.run(command, **kwargs)
        log_path = _append_lifecycle_log(
            app_id, action=action, command=command, folder=folder,
            started_at=started_at, returncode=result.returncode,
            stdout=result.stdout or '', stderr=result.stderr or '', shell=shell or '')
        result.lifecycle_log = str(log_path)
        return result
    except Exception as exc:
        detail = traceback.format_exc()
        log_path = _append_lifecycle_log(
            app_id, action=action, command=command, folder=folder,
            started_at=started_at, error=f'{type(exc).__name__}: {exc}\n{detail}',
            shell=shell or '')
        exc.lifecycle_log = str(log_path)
        raise

def _load_processes() -> dict:
    try:
        data = json.loads(PROCESS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_processes(data: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    temp = PROCESS_FILE.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2))
    os.replace(temp, PROCESS_FILE)

def _process_group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

def _terminate_tracked_process(app_id: str) -> str:
    processes = _load_processes()
    record = processes.get(app_id)
    if not record:
        return 'No launcher-tracked process was found; nothing was terminated.'
    pid = int(record.get('pid', 0) or 0)
    pgid = int(record.get('pgid', pid) or pid)
    if os.name == 'nt':
        if not pid:
            return 'No tracked process ID was found.'
        first = subprocess.run(['taskkill', '/PID', str(pid), '/T'], capture_output=True, text=True, timeout=10)
        if first.returncode != 0:
            second = subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, text=True, timeout=10)
            if second.returncode != 0 and 'not found' not in (second.stdout + second.stderr).lower():
                raise RuntimeError((second.stdout + second.stderr).strip() or 'Windows could not terminate the process tree.')
        message = 'Tracked process tree terminated.'
    else:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.time() + 4
        while time.time() < deadline:
            # Reap the direct shell child when it exits; otherwise its zombie
            # entry can make killpg(..., 0) look alive even though work stopped.
            if pid:
                try:
                    os.waitpid(pid, os.WNOHANG)
                except (ChildProcessError, ProcessLookupError):
                    pass
            if not _process_group_alive(pgid):
                break
            time.sleep(0.15)
        if _process_group_alive(pgid):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                if pid:
                    os.waitpid(pid, 0)
            except (ChildProcessError, ProcessLookupError, OSError):
                pass
            message = 'Tracked process group force-terminated after graceful shutdown timed out.'
        else:
            message = 'Tracked process group terminated gracefully.'
    processes.pop(app_id, None)
    _save_processes(processes)
    return message

@app.get('/api/apps/{app_id}/logs')
def get_app_lifecycle_logs(app_id: str, lines: int = 160):
    cfg = load_config()
    if not any(str(item.get('id')) == app_id for item in cfg['apps']):
        raise HTTPException(404, f'Application ID {app_id!r} was not found.')
    path = _lifecycle_log_path(app_id)
    if not path.exists():
        return {"app_id": app_id, "log_path": str(path), "content": "No lifecycle commands have been logged yet."}
    content = path.read_text(encoding='utf-8', errors='replace').splitlines()
    output_path = RUNTIME_DIR / f'{app_id}.log'
    output = output_path.read_text(encoding='utf-8', errors='replace').splitlines() if output_path.exists() else []
    combined = content + (['', '=' * 40, 'RAW START-COMMAND OUTPUT', f'Output file: {output_path}'] + output if output else [])
    return {"app_id": app_id, "log_path": str(path), "output_log_path": str(output_path),
            "content": '\n'.join(combined[-max(20, min(lines, 1000)):])}


@app.post('/api/apps/{app_id}/action/{action}')
def run_action(app_id: str, action: str):
    """Execute per-app start/stop/status commands; stop falls back to the tracked process tree."""
    if action not in ('start', 'stop', 'status'):
        raise HTTPException(400, 'Invalid action. Use start, stop, or status.')
    cfg = load_config()
    target = next((item for item in cfg['apps'] if str(item.get('id')) == app_id), None)
    if target is None:
        known = ', '.join(str(item.get('id')) for item in cfg['apps']) or '(none)'
        raise HTTPException(404, f'Application ID {app_id!r} was not found. Configured IDs: {known}')
    folder = app_path(target)
    if not folder.exists() or not folder.is_dir():
        message = f"Application folder does not exist or is not a directory: {folder}\nConfigured path: {target.get('path')}\nLauncher root: {ROOT}"
        log_path = _append_lifecycle_log(app_id, action=action, command=str(target.get(f'{action}_command') or ''),
                                        folder=folder, started_at=time.time(), error=message)
        raise HTTPException(400, f"{message}\nDetailed log: {log_path}")

    if action == 'status':
        command = str(target.get('status_command') or '').strip()
        if command:
            try:
                result = _run_shell_command(app_id, action, command, folder, timeout=20)
            except subprocess.TimeoutExpired as exc:
                log_path = getattr(exc, 'lifecycle_log', str(_lifecycle_log_path(app_id)))
                raise HTTPException(504, f'Status command timed out after 20 seconds. Detailed log: {log_path}')
            output = '\n'.join(x.strip() for x in (result.stdout, result.stderr) if x and x.strip())
            return {"ok": True, "action": "status", "running": result.returncode == 0,
                    "message": output[-2000:] or ('Status command reports running.' if result.returncode == 0 else f'Status command reports stopped (exit {result.returncode}).')}
        status_started = time.time()
        is_running = probe(str(target.get('url') or ''))
        log_path = _append_lifecycle_log(app_id, action='status (URL probe)', command=str(target.get('url') or ''),
                                        folder=folder, started_at=status_started, returncode=0 if is_running else 1,
                                        stdout='Application URL is reachable.' if is_running else 'Application URL is not reachable.')
        return {"ok": True, "action": "status", "running": is_running,
                "message": 'Application URL is reachable.' if is_running else 'Application URL is not reachable.',
                "log_path": str(log_path)}

    if action == 'start':
        command = str(target.get('start_command') or '').strip()
        if not command:
            raise HTTPException(400, 'No start command is configured. Add one in application settings.')
        # Avoid duplicate launches when either the configured URL is healthy or
        # a previously tracked process group is still alive.
        if probe(str(target.get('url') or '')):
            return {"ok": True, "action": "start", "message": 'Application is already reachable; start command was not run.', "running": True}
        tracked = _load_processes().get(app_id)
        if tracked:
            if os.name == 'nt':
                tracked_pid = int(tracked.get('pid', 0) or 0)
                alive = False
                if tracked_pid:
                    try:
                        os.kill(tracked_pid, 0); alive = True
                    except ProcessLookupError:
                        alive = False
                    except PermissionError:
                        alive = True
            else:
                alive = bool(tracked.get('pgid')) and _process_group_alive(int(tracked['pgid']))
            if alive:
                return {"ok": True, "action": "start", "message": f'A launcher-tracked process is already active (PID {tracked.get("pid")}).', "running": None}
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        log_path = RUNTIME_DIR / f'{app_id}.log'
        try:
            log_file = log_path.open('a', encoding='utf-8')
            started_at = time.time()
            shell = _shell_executable()
            log_file.write('\n' + '=' * 88 + '\n'
                           + f'Timestamp: {time.strftime("%Y-%m-%d %H:%M:%S %z")}\n'
                           + f'Application: {target.get("title", app_id)} ({app_id})\n'
                           + f'Working directory: {folder}\n'
                           + f'Configured command: {command!r}\n'
                           + f'Shell executable: {shell}\n' + '=' * 88 + '\n')
            log_file.flush()
            kwargs = {"cwd": str(folder), "shell": True, "stdin": subprocess.DEVNULL,
                      "stdout": log_file, "stderr": subprocess.STDOUT}
            if shell:
                kwargs['executable'] = shell
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            else:
                kwargs['start_new_session'] = True
            proc = subprocess.Popen(command, **kwargs)
            log_file.close()
            time.sleep(0.5)
            if proc.poll() is not None and proc.returncode not in (0, None):
                tail = log_path.read_text(errors='replace')[-5000:] if log_path.exists() else ''
                diagnostic = (f'Application: {target.get("title", app_id)}\n'
                              f'Working directory: {folder}\nConfigured command: {command!r}\n'
                              f'Shell executable: {shell}\nExit code: {proc.returncode}\n'
                              f'Output log: {log_path}\n\n{tail}')
                _append_lifecycle_log(app_id, action='start', command=command, folder=folder,
                                      started_at=started_at, returncode=proc.returncode,
                                      stderr=tail, shell=shell or '', pid=proc.pid)
                raise HTTPException(500, diagnostic)
            record = {"pid": proc.pid, "pgid": proc.pid if os.name != 'nt' else None,
                      "command": command, "configured_command": command,
                      "app_path": str(folder), "started_at": time.time(), "log": str(log_path),
                      "lifecycle_log": str(_lifecycle_log_path(app_id)), "shell": shell}
            processes = _load_processes(); processes[app_id] = record; _save_processes(processes)
            _append_lifecycle_log(app_id, action='start', command=command, folder=folder,
                                  started_at=started_at, shell=shell or '', pid=proc.pid)
            return {"ok": True, "action": "start", "message": f'Start command launched.\nWorking directory: {folder}\nCommand: {command}\nPID: {proc.pid}\nOutput: {log_path}', "running": None, "log_path": str(_lifecycle_log_path(app_id))}
        except HTTPException:
            raise
        except Exception as exc:
            detail = traceback.format_exc()
            log_path = _append_lifecycle_log(app_id, action='start', command=command, folder=folder,
                                             started_at=time.time(), error=f'{type(exc).__name__}: {exc}\n{detail}',
                                             shell=_shell_executable() or '')
            raise HTTPException(500, f'Could not launch start command.\nApplication: {target.get("title", app_id)}\nWorking directory: {folder}\nCommand: {command!r}\nShell: {_shell_executable()}\nError: {type(exc).__name__}: {exc}\nDetailed log: {log_path}')

    # Stop: use the user's custom stop command when present; otherwise kill the
    # process tree/group created by the launcher when the start command was run.
    command = str(target.get('stop_command') or '').strip()
    if command:
        try:
            result = _run_shell_command(app_id, action, command, folder, timeout=60)
        except subprocess.TimeoutExpired as exc:
            log_path = getattr(exc, 'lifecycle_log', str(_lifecycle_log_path(app_id)))
            raise HTTPException(504, f'Stop command timed out after 60 seconds. Detailed log: {log_path}')
        output = '\n'.join(x.strip() for x in (result.stdout, result.stderr) if x and x.strip())
        if result.returncode != 0:
            raise HTTPException(500, f'Stop command failed (exit {result.returncode}).\nWorking directory: {folder}\nCommand: {command!r}\nOutput: {output[-4000:]}\nDetailed log: {result.lifecycle_log}')
        processes = _load_processes(); processes.pop(app_id, None); _save_processes(processes)
        return {"ok": True, "action": "stop", "message": output[-2000:] or 'Custom stop command completed successfully.', "running": False, "log_path": result.lifecycle_log}
    try:
        stop_started = time.time()
        message = _terminate_tracked_process(app_id)
        log_path = _append_lifecycle_log(app_id, action='stop (tracked process fallback)', command='terminate tracked process group/tree',
                                        folder=folder, started_at=stop_started, returncode=0, stdout=message)
        return {"ok": True, "action": "stop", "message": message, "running": False, "log_path": str(log_path)}
    except subprocess.TimeoutExpired:
        raise HTTPException(504, 'Timed out while terminating the application process tree.')
    except Exception as exc:
        raise HTTPException(500, f'Could not terminate tracked application process: {exc}')


@app.post('/api/launcher/close')
def launcher_close():
    """Acknowledge the UI request, then terminate this launcher process."""
    global shutdown_timer
    def terminate_launcher():
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except ProcessLookupError:
            pass
    with shutdown_lock:
        if shutdown_timer is not None:
            shutdown_timer.cancel()
        # Give FastAPI enough time to return the acknowledgement to the browser.
        shutdown_timer = threading.Timer(1.0, terminate_launcher)
        shutdown_timer.daemon = True
        shutdown_timer.start()
    return {"ok": True, "message": "Shutdown accepted. The launcher server is terminating now."}
