import json
import os
import signal
import subprocess
import sys
import time
from csv import reader as csv_reader
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .store import Store


@dataclass(frozen=True)
class StartResult:
    pid: int
    already_running: bool


@dataclass(frozen=True)
class StopResult:
    pid: Optional[int]
    stopped: bool
    message: str


@dataclass(frozen=True)
class StatusResult:
    running: bool
    pid: Optional[int]
    message: str


def start_daemon(store: Store) -> StartResult:
    pid = read_pid(store.pid_path)
    if pid is not None and is_running(pid):
        return StartResult(pid=pid, already_running=True)
    clear_pid(store.pid_path)
    store.home.mkdir(parents=True, exist_ok=True)
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    process = subprocess.Popen(
        [sys.executable, "-m", "buaatd", "daemon"],
        cwd=str(Path(__file__).resolve().parent.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        start_new_session=(os.name != "nt"),
        close_fds=True,
    )
    write_pid(store.pid_path, process.pid)
    return StartResult(pid=process.pid, already_running=False)


def stop_daemon(store: Store, timeout: float = 5.0) -> StopResult:
    pid = read_pid(store.pid_path)
    if pid is None:
        clear_pid(store.pid_path)
        return StopResult(None, False, "未运行")
    if not is_running(pid):
        clear_pid(store.pid_path)
        return StopResult(pid, False, f"进程已退出 PID={pid}")
    try:
        terminate(pid)
    except ProcessLookupError:
        clear_pid(store.pid_path)
        return StopResult(pid, False, f"进程不存在 PID={pid}")
    except PermissionError:
        return StopResult(pid, False, f"无权限停止 PID={pid}")
    except Exception as exc:
        return StopResult(pid, False, f"停止失败 PID={pid}: {exc}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_running(pid):
            clear_pid(store.pid_path)
            return StopResult(pid, True, f"已停止 PID={pid}")
        time.sleep(0.1)
    return StopResult(pid, True, f"已发送停止信号 PID={pid}")


def daemon_status(store: Store) -> StatusResult:
    pid = read_pid(store.pid_path)
    if pid is None:
        return StatusResult(False, None, "未运行")
    if is_running(pid):
        return StatusResult(True, pid, f"运行中 PID={pid}")
    clear_pid(store.pid_path)
    return StatusResult(False, pid, f"已退出，清理旧 PID={pid}")


def read_pid(pid_path: Path) -> Optional[int]:
    try:
        payload = json.loads(pid_path.read_text(encoding="utf-8"))
        pid = int(payload["pid"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return pid if pid > 0 else None


def write_pid(pid_path: Path, pid: int) -> None:
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(json.dumps({"pid": int(pid)}, ensure_ascii=False) + "\n", encoding="utf-8")


def clear_pid(pid_path: Path) -> None:
    try:
        pid_path.unlink()
    except FileNotFoundError:
        pass


def is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _running_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate(pid: int) -> None:
    if os.name == "nt":
        _terminate_windows(pid)
        return
    os.kill(pid, signal.SIGTERM)


def _running_windows(pid: int) -> bool:
    completed = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return False
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith('"'):
            continue
        row = next(csv_reader([line]), [])
        if len(row) >= 2:
            try:
                if int(row[1]) == pid:
                    return True
            except ValueError:
                continue
    return False


def _terminate_windows(pid: int) -> None:
    completed = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return
    detail = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())
    lowered = detail.lower()
    if any(t in lowered for t in ("not found", "no running instance", "no tasks are running")):
        raise ProcessLookupError(pid)
    if any(t in detail for t in ("找不到", "不存在")):
        raise ProcessLookupError(pid)
    if "access is denied" in lowered or any(t in detail for t in ("拒绝访问", "存取被拒")):
        raise PermissionError(detail or f"Access denied PID={pid}")
    raise RuntimeError(detail or f"taskkill exit={completed.returncode}")
