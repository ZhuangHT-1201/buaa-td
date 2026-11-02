import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .const import DEFAULT_POLL_SECONDS, DEFAULT_WINDOWS
from .machines import default_config
from .models import User
from .paths import get_data_home
from .photo import is_jpeg_file


class Store:
    def __init__(self, home: Optional[Path] = None):
        self.home = (home or get_data_home()).expanduser().resolve()
        self.images_dir = self.home / "images"
        self.logs_dir = self.home / "logs"
        self.config_path = self.home / "config.json"
        self.users_path = self.home / "users.json"
        self.settings_path = self.home / "settings.json"
        self.state_path = self.home / "state.json"
        self.pid_path = self.home / "buaatd.pid"

    def initialize(self) -> None:
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.save_config(default_config())
        if not self.users_path.exists():
            self.save_users({})
        if not self.settings_path.exists():
            self.save_settings(
                {
                    "schedule": {
                        "poll_seconds": DEFAULT_POLL_SECONDS,
                        "windows": list(DEFAULT_WINDOWS),
                    },
                    "ui": {"language": "zh"},
                }
            )
        if not self.state_path.exists():
            self.save_state({"schedule": {"last_attempt_date": None}, "counts": {}, "daily_runs": {}})

    def load_config(self) -> Dict[str, Any]:
        return _read_json(self.config_path)

    def save_config(self, config: Dict[str, Any]) -> None:
        _write_json(self.config_path, config)

    def load_users(self) -> Dict[str, Dict[str, Any]]:
        payload = _read_json(self.users_path) if self.users_path.exists() else {"users": {}}
        return dict(payload.get("users", {}))

    def save_users(self, users: Dict[str, Dict[str, Any]]) -> None:
        _write_json(self.users_path, {"users": users})

    def list_users(self) -> List[User]:
        return [User.from_dict(data) for data in self.load_users().values()]

    def get_user(self, student_id: str) -> Optional[Dict[str, Any]]:
        return self.load_users().get(str(student_id))

    def get_user_model(self, student_id: str) -> User:
        data = self.get_user(student_id)
        if data is None:
            raise KeyError(f"用户不存在: {student_id}")
        return User.from_dict(data)

    def save_user(self, user: User) -> None:
        self.validate_user(user)
        users = self.load_users()
        users[user.student_id] = user.to_dict()
        self.save_users(users)

    def delete_user(self, student_id: str) -> bool:
        users = self.load_users()
        existed = users.pop(str(student_id), None) is not None
        self.save_users(users)
        return existed

    def validate_user(self, user: User) -> None:
        machine_ids = {int(m["id"]) for m in self.load_config().get("machine", [])}
        if user.entrance_machine_id not in machine_ids:
            raise ValueError(f"入口机器不存在: {user.entrance_machine_id}")
        if user.exit_machine_id not in machine_ids:
            raise ValueError(f"出口机器不存在: {user.exit_machine_id}")
        for image in (user.entrance_image, user.exit_image):
            path = self.images_dir / image
            if not path.exists():
                raise FileNotFoundError(f"图片不存在: {image}")
            if not is_jpeg_file(path):
                raise ValueError(f"图片不是有效 JPEG: {image}")

    def list_images(self) -> List[str]:
        if not self.images_dir.exists():
            return []
        return sorted(p.name for p in self.images_dir.iterdir() if p.is_file())

    def image_path(self, name: str) -> Path:
        return self.images_dir / name

    def load_settings(self) -> Dict[str, Any]:
        return _read_json(self.settings_path) if self.settings_path.exists() else {
            "schedule": {
                "poll_seconds": DEFAULT_POLL_SECONDS,
                "windows": list(DEFAULT_WINDOWS),
            },
            "ui": {"language": "zh"},
        }

    def save_settings(self, settings: Dict[str, Any]) -> None:
        _write_json(self.settings_path, settings)

    def load_state(self) -> Dict[str, Any]:
        return _read_json(self.state_path) if self.state_path.exists() else {
            "schedule": {"last_attempt_date": None},
            "counts": {},
            "daily_runs": {},
        }

    def save_state(self, state: Dict[str, Any]) -> None:
        _write_json(self.state_path, state)

    def set_last_count(self, student_id: str, count: int, when: Optional[datetime] = None) -> None:
        state = self.load_state()
        counts = state.setdefault("counts", {})
        counts[str(student_id)] = {
            "count": int(count),
            "updated_at": (when or datetime.now()).isoformat(timespec="seconds"),
        }
        self.save_state(state)

    def get_last_count(self, student_id: str) -> Optional[int]:
        info = self.load_state().get("counts", {}).get(str(student_id))
        if not info:
            return None
        return int(info["count"])


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
