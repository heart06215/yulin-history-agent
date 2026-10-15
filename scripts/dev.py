"""Launch only this project's API and frontend; Ctrl-C stops both."""

import argparse, json, os, shutil, signal, socket, subprocess, sys, time, webbrowser
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = sys.platform == "win32"
LOCK_PATH = ROOT / ".local-dev.lock"
STATE_PATH = ROOT / ".local-dev.json"


def acquire_instance():
    """Keep one API worker per checkout, without touching unrelated processes."""
    lock = LOCK_PATH.open("a+b")
    try:
        # Windows byte locks also forbid reading the locked byte.
        if LOCK_PATH.stat().st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if IS_WINDOWS:
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return None
    return lock


def existing_frontend():
    """Recognize an older Vinext launch before starting another API consumer."""
    try:
        info = json.loads((ROOT / "apps/web/.vinext/dev/lock.json").read_text(encoding="utf-8"))
        port, pid = info["port"], info["pid"]
        if type(port) is not int or not 1 <= port <= 65535 or type(pid) is not int or pid <= 0:
            return None
        if Path(info["cwd"]).resolve() != (ROOT / "apps/web").resolve():
            return None
        # Python os.kill(pid, 0) is unsafe on Windows; Node implements a true liveness check.
        alive = subprocess.run(
            [shutil.which("node"), "-e", "try { process.kill(Number(process.argv[1]), 0); } catch { process.exit(1); }", str(pid)],
            capture_output=True,
            timeout=5,
        )
        if alive.returncode:
            return None
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return None
    url = f"http://127.0.0.1:{port}/"
    if ready(url) and ready(url + "api/v1/health"):
        return url
    raise SystemExit("已有项目前端正在启动，或后端未就绪。请稍后重试；若持续失败，请在原启动窗口按 Ctrl-C 停止后再启动。")


def existing_url():
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        port = state["web_port"]
        api = state["api_port"]
        if type(port) is not int or type(api) is not int or not (1 <= port <= 65535 and 1 <= api <= 65535):
            return None
        url = f"http://127.0.0.1:{port}/"
        if ready(url) and ready(f"http://127.0.0.1:{api}/api/v1/health"):
            return url
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def venv_python():
    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
        ROOT / ".venv" / "bin" / "python3",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise SystemExit("Run the setup instructions in README.md first.")


def port_free(port):
    with socket.socket() as sock:
        if not IS_WINDOWS:
            # Windows SO_REUSEADDR permits rebinding an occupied port, which would defeat this check.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def choose_port(preferred, explicit):
    if explicit:
        if not port_free(preferred):
            raise SystemExit(
                f"Port {preferred} was set explicitly (API_PORT/WEB_PORT) but is occupied."
            )
        return preferred
    port = preferred
    while port < min(preferred + 50, 65536):
        if port_free(port):
            return port
        port += 1
    raise SystemExit(f"No free port found between {preferred} and {port - 1}.")


def requested_port(name, default):
    raw = os.environ.get(name, "")
    if not raw:
        return default, False
    if not raw.isdigit() or not 1 <= int(raw) <= 65535:
        raise SystemExit(f"{name} must be an integer between 1 and 65535.")
    return int(raw), True


def frontend_command():
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        raise SystemExit("Node.js/npm was not found on PATH. Install Node.js 22.13+ first.")
    version = subprocess.check_output([node, "--version"], text=True).strip().lstrip("v")
    if tuple(int(part) for part in version.split(".")[:2]) < (22, 13):
        raise SystemExit(f"Node.js {version} is too old. This frontend requires 22.13+.")
    # Calling the JS entry directly avoids .cmd association and quoting issues
    # in Windows CreateProcess, including installation paths with spaces.
    cli = Path(node).parent / "node_modules/npm/bin/npm-cli.js"
    if cli.is_file():
        return [node, str(cli), "run", "dev"]
    return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", npm, "run", "dev"] if IS_WINDOWS else [npm, "run", "dev"]


def ready(url):
    try:
        with urlopen(url, timeout=0.4) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--open", action="store_true", help="Open the exact frontend URL after both services are ready.")
    parser.add_argument("--no-open", action="store_true", help="Start services without opening a browser.")
    parser.add_argument("--check", action="store_true", help="Validate the local setup without starting services.")
    args = parser.parse_args()
    args.open = args.open and not args.no_open
    raw_api, explicit_api = requested_port("API_PORT", 8765)
    raw_web, explicit_web = requested_port("WEB_PORT", 5173)
    if raw_api == raw_web:
        raise SystemExit("API_PORT and WEB_PORT must be different.")
    python = str(venv_python())
    web_command = frontend_command()
    dependencies = subprocess.run([python, "-c", "import fastapi,uvicorn,pydantic_settings,pypdfium2,PIL"], capture_output=True, text=True)
    if dependencies.returncode:
        print(dependencies.stderr, file=sys.stderr)
        raise SystemExit("Backend dependencies are missing. Run .venv/Scripts/python -m pip install -r backend/requirements.txt.")
    if not (ROOT / "apps/web/node_modules/vinext").exists():
        raise SystemExit("Frontend dependencies are missing. Run npm --prefix apps/web ci first.")
    if args.check:
        print("Setup OK: Python dependencies, Node.js version and frontend dependencies verified.")
        return 0
    prior_url = existing_frontend()
    if prior_url:
        if explicit_web and prior_url != f"http://127.0.0.1:{raw_web}/":
            raise SystemExit("已有项目使用不同 WEB_PORT。请先在原启动窗口停止服务，再使用新端口启动。")
        print(f"项目已在运行，直接打开现有网页：{prior_url}", flush=True)
        if args.open:
            webbrowser.open(prior_url)
        return 0
    lock = acquire_instance()
    if lock is None:
        url = existing_url()
        if url:
            print(f"项目已在运行，直接打开现有网页：{url}", flush=True)
            if args.open:
                webbrowser.open(url)
            return 0
        raise SystemExit("项目正在启动或停止，请稍后再试。不会重复启动数据库工作进程。")
    try:
        api_port = choose_port(raw_api, explicit_api)
        web_port = choose_port(raw_web, explicit_web)
        if api_port == web_port:
            if explicit_web:
                raise SystemExit("API_PORT and WEB_PORT must be different.")
            web_port = choose_port(web_port + 1, False)
    except (SystemExit, OSError):
        lock.close()
        raise
    switched = [
        name
        for name, chosen, default in (("API", api_port, 8765), ("WEB", web_port, 5173))
        if chosen != default
    ]
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "backend"),
        "API_PORT": str(api_port),
        "WEB_PORT": str(web_port),
        "WEB_PLATFORM": os.environ.get("WEB_PLATFORM", "node"),
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
    }
    children = []
    exit_code = 0
    try:
        STATE_PATH.write_text(json.dumps({"api_port": api_port, "web_port": web_port}), encoding="utf-8")
        children.append(
            subprocess.Popen(
                [
                    python,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api_port),
                ],
                cwd=ROOT,
                env=env,
                start_new_session=not IS_WINDOWS,
            )
        )
        children.append(
            subprocess.Popen(
                web_command,
                cwd=ROOT / "apps/web",
                env=env,
                start_new_session=not IS_WINDOWS,
            )
        )
        print(
            f"正在启动，请等待网页就绪。\n网页：http://127.0.0.1:{web_port}/\n接口：http://127.0.0.1:{api_port}/docs\n按 Ctrl-C 停止前后端。",
            flush=True,
        )
        if switched:
            print(
                "Default port(s) were busy, so the next free port was chosen automatically. "
                "Other running programs were not touched.",
                flush=True,
            )
        online = False
        started = time.monotonic()
        while all(p.poll() is None for p in children):
            if not online and ready(f"http://127.0.0.1:{api_port}/api/v1/health") and ready(f"http://127.0.0.1:{web_port}/"):
                online = True
                print(f"启动成功：http://127.0.0.1:{web_port}/", flush=True)
                if args.open:
                    webbrowser.open(f"http://127.0.0.1:{web_port}/")
            if not online and time.monotonic() - started > 180:
                print("启动超时：前端或后端未就绪，请查看上方错误。", file=sys.stderr, flush=True)
                exit_code = 1
                break
            time.sleep(0.5)
        failed = next((child for child in children if child.poll() is not None), None)
        if failed is not None and not exit_code:
            exit_code = failed.returncode or 1
            print(f"A service stopped unexpectedly (exit {exit_code}). See the error above.", file=sys.stderr)
    except KeyboardInterrupt:
        pass
    finally:
        for p in children:
            if p.poll() is None:
                stop(p)
        STATE_PATH.unlink(missing_ok=True)
        lock.close()
    return exit_code


def stop(p):
    if IS_WINDOWS:
        # npm spawns node children; terminate the whole tree.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(p.pid)],
            capture_output=True,
            check=False,
        )
    else:
        os.killpg(p.pid, signal.SIGTERM)
    try:
        p.wait(timeout=8)
    except subprocess.TimeoutExpired:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(p.pid)],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(p.pid, signal.SIGKILL)


if __name__ == "__main__":
    sys.exit(main())
