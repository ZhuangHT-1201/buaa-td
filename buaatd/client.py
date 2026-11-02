import json
import re
import socket
import struct
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from .const import BEIJING_TZ, DEFAULT_SOCKET_TIMEOUT, DEFAULT_WINDOWS
from .models import CheckResponse, User
from .photo import is_jpeg_bytes
from .window import in_any_window

COUNT_RE = re.compile(r"本学期锻炼次数\s*[:：]\s*(\d+)")


class TDClient:
    def __init__(self, config: Dict[str, Any], socket_factory: Callable[..., socket.socket] = socket.socket):
        self.config = config
        self.server = config["server"]["ip"]
        self.port = int(config["server"]["port"])
        self.timeout = float(config.get("server", {}).get("timeout", DEFAULT_SOCKET_TIMEOUT))
        self.type = config["type"]
        self.schoolno = config["schoolno"]
        self.eventno = config["eventno"]
        self.machine = {int(m["id"]): m for m in config["machine"]}
        self.socket_factory = socket_factory

    def request(self, data: bytes, request_type: int) -> Dict[str, Any]:
        header = struct.pack(">lB", len(data), request_type)
        with self.socket_factory(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            try:
                sock.connect((self.server, self.port))
            except (ConnectionRefusedError, TimeoutError, OSError) as exc:
                raise ConnectionError(f"无法连接 TD 服务器 {self.server}:{self.port}: {exc}") from exc
            sock.sendall(header + data)
            response_header = _recv_exact(sock, 5)
            length, code = struct.unpack(">lB", response_header)
            if code != request_type:
                raise ValueError(f"响应类型不匹配: {code}")
            if length <= 0:
                raise ValueError("空响应")
            response = _recv_exact(sock, length)
            return json.loads(response.decode("utf-8"))

    def check(
        self,
        user: User,
        machine_id: int,
        timestamp: Optional[float] = None,
        enforce_time_window: bool = True,
    ) -> CheckResponse:
        if timestamp is None:
            timestamp = time.time()
        if enforce_time_window and not _in_window(timestamp):
            dt_bj = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(BEIJING_TZ)
            return CheckResponse(False, f"非法时间，已取消。当前时间：{dt_bj.strftime('%Y-%m-%d %H:%M:%S')}", None)

        payload = self.build_check_data(user, machine_id, timestamp)
        resp = self.request(json.dumps(payload).encode("utf-8"), 80)
        if resp["status"] != "success":
            raise ValueError(f"打卡请求失败: {resp['status']}")
        server_message = clean_server_message(resp.get("srvresp") or "")
        return CheckResponse(
            success="成功" in (resp.get("srvresp") or ""),
            server_message=server_message,
            count=extract_exercise_count(server_message),
        )

    def upload_photo(self, machine_id: int, photo: bytes, timestamp: Optional[float] = None) -> Dict[str, Any]:
        if not is_jpeg_bytes(photo):
            raise ValueError("打卡照片不是有效 JPEG，请更换照片或使用默认照片")
        if timestamp is None:
            timestamp = time.time()
        machine = self.machine[machine_id]
        ts_ms = str(int(timestamp * 1000))
        photo_data = f"{machine['machinesn']}_{ts_ms}".encode("utf-8") + photo
        resp = self.request(photo_data, 100)
        if resp["status"] != "success":
            raise ValueError(f"照片上传失败: {resp['status']}")
        return resp

    def query_count(self, user: User, machine_id: Optional[int] = None) -> int:
        response = self.check(user, machine_id or user.entrance_machine_id, enforce_time_window=False)
        if response.count is None:
            raise ValueError(f"服务器消息中没有锻炼次数: {response.server_message}")
        return response.count

    def build_check_data(self, user: User, machine_id: int, timestamp: float) -> Dict[str, Any]:
        machine = self.machine[machine_id]
        return {
            "cardno": user.card_id.upper(),
            "userno": user.student_id.upper(),
            "timestamp": str(int(timestamp * 1000)),
            "type": self.type,
            "eventno": self.eventno,
            "ln": str(machine["id"]),
            "sn": machine["machinesn"],
            "schoolno": self.schoolno,
        }


def extract_exercise_count(message: str) -> Optional[int]:
    match = COUNT_RE.search(message)
    return int(match.group(1)) if match else None


def clean_server_message(message: str) -> str:
    return message.strip().replace("\n \n", "\n").replace("\n", ", ")


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("socket closed")
        data += chunk
    return data


def _in_window(timestamp: float) -> bool:
    dt_bj = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(BEIJING_TZ)
    return in_any_window(dt_bj, DEFAULT_WINDOWS)
