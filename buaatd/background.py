import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .store import Store

_WINDOWS_NO_WINDOW = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
)

if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
else:
    _kernel32 = None


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
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | _WINDOWS_NO_WINDOW
        )
    process = subprocess.Popen(
        [_daemon_executable(), "-m", "buaatd", "daemon"],
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


def _daemon_executable() -> str:
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw)
    return sys.executable


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
    if _kernel32 is None:
        return False
    process_query_limited_information = 0x1000
    still_active = 259
    handle = _kernel32.OpenProcess(
        process_query_limited_information,
        False,
        int(pid),
    )
    if not handle:
        # Access denied still means the process exists but belongs to another user.
        return ctypes.get_last_error() == 5
    try:
        exit_code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        _kernel32.CloseHandle(handle)


def _terminate_windows(pid: int) -> None:
    if _kernel32 is None:
        raise RuntimeError("Windows process API unavailable")
    process_terminate = 0x0001
    synchronize = 0x00100000
    handle = _kernel32.OpenProcess(
        process_terminate | synchronize,
        False,
        int(pid),
    )
    if not handle:
        error = ctypes.get_last_error()
        if error == 5:
            raise PermissionError(f"Access denied PID={pid}")
        if error in (6, 87):
            raise ProcessLookupError(pid)
        raise OSError(error, f"OpenProcess failed for PID={pid}")
    try:
        if not _kernel32.TerminateProcess(handle, 1):
            error = ctypes.get_last_error()
            if error == 5:
                raise PermissionError(f"Access denied PID={pid}")
            raise OSError(error, f"TerminateProcess failed for PID={pid}")
    finally:
        _kernel32.CloseHandle(handle)
