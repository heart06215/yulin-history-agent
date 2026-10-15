"""Launcher checks do not start workers or invoke model providers."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("yulin_dev", ROOT / "scripts/dev.py")
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


@pytest.mark.parametrize("value", ["invalid", "0", "65536", "-1"])
def test_invalid_port_is_rejected(monkeypatch, value):
    monkeypatch.setenv("WEB_PORT", value)
    with pytest.raises(SystemExit, match="WEB_PORT"):
        dev.requested_port("WEB_PORT", 5173)


def test_busy_explicit_port_does_not_touch_other_processes(monkeypatch):
    monkeypatch.setattr(dev, "port_free", lambda port: port == 5174)
    assert dev.choose_port(5173, False) == 5174
    with pytest.raises(SystemExit, match="occupied"):
        dev.choose_port(5173, True)


def test_lock_is_exclusive_and_released(tmp_path, monkeypatch):
    path = tmp_path / "instance.lock"
    monkeypatch.setattr(dev, "LOCK_PATH", path)
    lock = dev.acquire_instance()
    assert lock is not None
    command = (
        "import importlib.util, pathlib, sys; "
        "s=importlib.util.spec_from_file_location('dev',sys.argv[1]); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "m.LOCK_PATH=pathlib.Path(sys.argv[2]); "
        "lock=m.acquire_instance(); print('busy' if lock is None else 'free')"
    )
    args = [sys.executable, "-c", command, str(ROOT / "scripts/dev.py"), str(path)]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert result.stdout.strip() == "busy"
    finally:
        lock.close()
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert result.stdout.strip() == "free"


def test_reuse_only_ready_local_instance(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setattr(dev, "STATE_PATH", state)
    urls = []
    monkeypatch.setattr(dev, "ready", lambda url: urls.append(url) or True)
    state.write_text(json.dumps({"web_port": 5174, "api_port": 8766}), encoding="utf-8")
    assert dev.existing_url() == "http://127.0.0.1:5174/"
    assert urls == ["http://127.0.0.1:5174/", "http://127.0.0.1:8766/api/v1/health"]
    monkeypatch.setattr(dev, "ready", lambda url: False)
    assert dev.existing_url() is None
    state.write_text(json.dumps({"web_port": "https://example.com", "api_port": 8766}), encoding="utf-8")
    assert dev.existing_url() is None
    state.write_text("broken JSON", encoding="utf-8")
    assert dev.existing_url() is None


def test_batch_encoding_is_cmd_safe():
    contents = (ROOT / "启动.bat").read_bytes()
    contents.decode("ascii")
    assert contents.count(b"\n") == contents.count(b"\r\n")
    assert b"DisableDelayedExpansion" in contents


@pytest.mark.parametrize("alive,ready,expected", [(True, True, "reuse"), (True, False, "wait"), (False, True, "stale")])
def test_existing_vinext_is_checked_before_another_api(tmp_path, monkeypatch, alive, ready, expected):
    lock_dir = tmp_path / "apps/web/.vinext/dev"
    lock_dir.mkdir(parents=True)
    (lock_dir / "lock.json").write_text(json.dumps({"port": 5173, "pid": 123, "cwd": str(tmp_path / "apps/web")}), encoding="utf-8")
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0 if alive else 1))
    monkeypatch.setattr(dev, "ready", lambda url: ready)
    if expected == "wait":
        with pytest.raises(SystemExit):
            dev.existing_frontend()
    else:
        assert dev.existing_frontend() == ("http://127.0.0.1:5173/" if expected == "reuse" else None)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CMD launcher")
@pytest.mark.parametrize("port,expected", [("5173", 0), ("invalid", 1)])
def test_batch_check_from_another_directory(tmp_path, port, expected):
    result = subprocess.run(
        f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /c call "{ROOT / "启动.bat"}" --check',
        cwd=tmp_path,
        env={**os.environ, "WEB_PORT": port, "API_PORT": "8765", "YULIN_NO_PAUSE": "1"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    if expected:
        assert "WEB_PORT" in result.stderr
        assert "[ERROR]" in result.stdout
    else:
        assert "Setup OK" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CMD quoting")
def test_batch_in_project_path_with_spaces(tmp_path):
    project = tmp_path / "project with spaces"
    (project / ".venv/Scripts").mkdir(parents=True)
    (project / "apps/web/node_modules/vinext").mkdir(parents=True)
    (project / "scripts").mkdir()
    shutil.copyfile(ROOT / ".venv/Scripts/python.exe", project / ".venv/Scripts/python.exe")
    shutil.copyfile(ROOT / ".venv/pyvenv.cfg", project / ".venv/pyvenv.cfg")
    shutil.copyfile(ROOT / "启动.bat", project / "启动.bat")
    (project / "scripts/dev.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "assert Path.cwd() == Path(__file__).resolve().parents[1]\n"
        "assert '--check' in sys.argv and sys.flags.utf8_mode\nprint('Setup OK')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /c call "{project / "启动.bat"}" --check',
        cwd=tmp_path,
        env={**os.environ, "YULIN_NO_PAUSE": "1"},
        capture_output=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Setup OK" in result.stdout
