from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .const import DEFAULT_ROUNDS, DEFAULT_WAIT_MAX, DEFAULT_WAIT_MIN


@dataclass(frozen=True)
class User:
    student_id: str
    card_id: str
    entrance_machine_id: int
    exit_machine_id: int
    entrance_image: str
    exit_image: str
    name: str = ""
    rounds: int = DEFAULT_ROUNDS
    wait_time_min: int = DEFAULT_WAIT_MIN
    wait_time_max: int = DEFAULT_WAIT_MAX

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "User":
        student_id = str(data.get("student_id", "")).strip()
        if not student_id:
            raise ValueError("学号不能为空")
        card_id = str(data.get("card_id", "") or "").strip()
        if not card_id:
            card_id = hex(int(student_id))[2:].upper()
        return cls(
            student_id=student_id,
            card_id=card_id.upper(),
            entrance_machine_id=int(data["entrance_machine_id"]),
            exit_machine_id=int(data["exit_machine_id"]),
            entrance_image=Path(str(data.get("entrance_image") or "")).name,
            exit_image=Path(str(data.get("exit_image") or "")).name,
            name=str(data.get("name", "") or "").strip(),
            rounds=int(data.get("rounds", DEFAULT_ROUNDS)),
            wait_time_min=int(data.get("wait_time_min", DEFAULT_WAIT_MIN)),
            wait_time_max=int(data.get("wait_time_max", DEFAULT_WAIT_MAX)),
        ).validated()

    def validated(self) -> "User":
        if self.rounds <= 0:
            raise ValueError("rounds 必须大于 0")
        if self.wait_time_min < 0 or self.wait_time_max < 0:
            raise ValueError("等待时间不能为负")
        if self.wait_time_min > self.wait_time_max:
            raise ValueError("wait_time_min 必须 <= wait_time_max")
        if not self.entrance_image or not self.exit_image:
            raise ValueError("入口/出口图片不能为空")
        return self

    def display(self) -> str:
        return f"{self.name}({self.student_id})" if self.name else self.student_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "student_id": self.student_id,
            "name": self.name,
            "card_id": self.card_id,
            "entrance_machine_id": self.entrance_machine_id,
            "exit_machine_id": self.exit_machine_id,
            "entrance_image": self.entrance_image,
            "exit_image": self.exit_image,
            "rounds": self.rounds,
            "wait_time_min": self.wait_time_min,
            "wait_time_max": self.wait_time_max,
        }


@dataclass(frozen=True)
class CheckResponse:
    success: bool
    server_message: str
    count: Optional[int] = None


@dataclass(frozen=True)
class UserResult:
    index: int
    student_id: str
    success: bool
    message: str


@dataclass(frozen=True)
class BatchResult:
    results: List[UserResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def success_count(self) -> int:
        return sum(1 for result in self.results if result.success)

    @property
    def failure_count(self) -> int:
        return self.total - self.success_count
