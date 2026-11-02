from __future__ import annotations

import logging
import math
import random
import time
from datetime import datetime, timedelta
from typing import Callable, Optional, TYPE_CHECKING

from .const import BEIJING_TZ, TD_LIMIT
from .models import BatchResult, User, UserResult
from .store import Store
from .window import parse_window

if TYPE_CHECKING:
    from .client import TDClient

WaitSeconds = Callable[[User], int]


def run_scheduler_once(
    store: Store,
    now: Optional[datetime] = None,
    logger: Optional[logging.Logger] = None,
    client: Optional[TDClient] = None,
    wait_seconds: Optional[WaitSeconds] = None,
) -> BatchResult:
    now = _localize(now or datetime.now(BEIJING_TZ))
    logger = logger or logging.getLogger("buaatd")
    if client is None:
        from .client import TDClient

        client = TDClient(store.load_config())
    wait_seconds = wait_seconds or (lambda user: random.randint(user.wait_time_min, user.wait_time_max))
    settings = store.load_settings()
    windows = settings.get("schedule", {}).get("windows", [])
    users = store.list_users()
    state, day_key, user_states = _load_today_user_states(store, users, now)
    _remove_deleted_user_states(user_states, users)
    if not users:
        store.save_state(state)
        logger.info("无用户，跳过")
        return BatchResult([])

    active_window = _active_window(now, windows)
    if active_window is None:
        _discard_due_actions_outside_window(user_states, users, now, logger)
        store.save_state(state)
        logger.info("不在打卡窗口")
        return _status_batch(users, user_states)

    window_key, _, window_end = active_window
    window_deadline = now.replace(
        hour=window_end // 60,
        minute=window_end % 60,
        second=0,
        microsecond=0,
    ) + timedelta(minutes=1)
    poll_seconds = max(
        1, int(settings.get("schedule", {}).get("poll_seconds", 60))
    )
    max_wait_seconds = max(
        0,
        int((window_deadline - now).total_seconds()) - poll_seconds - 5,
    )

    results = []
    for index, user in enumerate(users, start=1):
        user_state = user_states[user.student_id]
        status = str(user_state.get("status", "pending"))
        if status == "completed":
            results.append(UserResult(index, user.student_id, True, "已完成"))
            continue
        if status == "error":
            message = str(user_state.get("last_error") or "未知错误")
            results.append(UserResult(index, user.student_id, False, message))
            continue
        completed_windows = [
            str(value) for value in user_state.get("completed_windows", [])
        ]
        if status == "window_done":
            if str(user_state.get("window_key") or "") == window_key:
                results.append(UserResult(index, user.student_id, True, "本时段已完成"))
                continue
            status = "pending"
            user_state.update(
                {
                    "status": "pending",
                    "next_action": "entrance",
                    "due_at": None,
                    "window_key": window_key,
                    "last_message": "新的时间窗开始",
                    "last_error": None,
                    "updated_at": _iso(now),
                }
            )
        elif status == "waiting" and str(user_state.get("window_key") or "") != window_key:
            _discard_current_round(user_state, user, now, "跨时段")
            user_state.update(
                {
                    "window_key": window_key,
                    "last_message": "上一轮已跨时段，本时段重新开始",
                }
            )
            status = "pending"
        if window_key in completed_windows:
            user_state.update(
                {
                    "status": "window_done",
                    "window_key": window_key,
                    "next_action": None,
                    "due_at": None,
                    "last_message": "本时段已完成",
                    "last_error": None,
                    "updated_at": _iso(now),
                }
            )
            results.append(UserResult(index, user.student_id, True, "本时段已完成"))
            continue
        if _reached_limit(store, user):
            message = f"TD 已达 {TD_LIMIT}"
            user_state.update(_completed_user_state(user, now, message))
            results.append(UserResult(index, user.student_id, True, message))
            continue

        action = str(user_state.get("next_action") or "entrance")
        due_at = _parse_due_at(user_state.get("due_at"))
        if due_at is not None and due_at > now:
            if action == "exit" and due_at >= window_deadline:
                _discard_current_round(user_state, user, now, "出口等待")
                user_state.update(
                    {
                        "window_key": window_key,
                        "last_message": "出口时间超出本时段，下个时段重试",
                    }
                )
                results.append(
                    UserResult(index, user.student_id, True, user_state["last_message"])
                )
                continue
            remaining = math.ceil((due_at - now).total_seconds())
            results.append(UserResult(index, user.student_id, True, f"等待 {remaining}s"))
            continue
        if action == "entrance":
            if max_wait_seconds <= 10:
                user_state.update(
                    {
                        "status": "pending",
                        "next_action": "entrance",
                        "due_at": None,
                        "window_key": None,
                        "last_message": "本时段剩余时间不足，下个时段再试",
                        "last_error": None,
                        "updated_at": _iso(now),
                    }
                )
                results.append(
                    UserResult(index, user.student_id, True, user_state["last_message"])
                )
                continue
            user_state["window_key"] = window_key

        try:
            ok, message = _perform_due_action(
                store,
                client,
                user,
                user_state,
                now,
                wait_seconds,
                logger,
                window_key,
                max_wait_seconds,
            )
            results.append(UserResult(index, user.student_id, ok, message))
        except Exception as exc:
            retry_count = int(user_state.get("retry_count", 0) or 0) + 1
            if _is_retryable(exc) and retry_count <= 5:
                retry_at = now + timedelta(seconds=poll_seconds)
                user_state.update(
                    {
                        "status": "waiting",
                        "next_action": action,
                        "window_key": window_key,
                        "due_at": _iso(retry_at),
                        "retry_count": retry_count,
                        "last_error": str(exc),
                        "last_message": (
                            f"网络异常，将在 {retry_at.strftime('%H:%M')} 自动重试"
                        ),
                        "updated_at": _iso(now),
                    }
                )
            else:
                user_state.update(
                    {
                        "status": "error",
                        "last_error": str(exc),
                        "last_message": "失败，启动后台时可重试",
                        "retry_count": retry_count,
                        "updated_at": _iso(now),
                    }
                )
            logger.exception("用户 %s 失败: %s", user.student_id, exc)
            results.append(UserResult(index, user.student_id, False, str(exc)))

    state.setdefault("daily_runs", {}).setdefault(day_key, {})["users"] = user_states
    current_counts = store.load_state().get("counts")
    if current_counts is not None:
        state["counts"] = current_counts
    store.save_state(state)
    return BatchResult(results)


def run_forever(
    store: Store,
    stop_requested: Optional[Callable[[], bool]] = None,
    logger: Optional[logging.Logger] = None,
) -> None:
    stop_requested = stop_requested or (lambda: False)
    while not stop_requested():
        run_scheduler_once(store, logger=logger)
        poll = int(store.load_settings().get("schedule", {}).get("poll_seconds", 60))
        for _ in range(max(1, poll)):
            if stop_requested():
                return
            time.sleep(1)


def clear_today_errors(store: Store, now: Optional[datetime] = None) -> None:
    now = _localize(now or datetime.now(BEIJING_TZ))
    day_key = now.date().isoformat()
    state = store.load_state()
    users = state.get("daily_runs", {}).get(day_key, {}).get("users", {})
    changed = False
    for user_state in users.values():
        if user_state.get("status") == "error":
            user_state["status"] = "pending"
            user_state["next_action"] = "entrance"
            user_state["due_at"] = None
            user_state["window_key"] = None
            user_state["retry_count"] = 0
            user_state["last_error"] = None
            user_state["last_message"] = "错误已清除"
            user_state["updated_at"] = _iso(now)
            changed = True
    if changed:
        store.save_state(state)


def format_today_status(store: Store, now: Optional[datetime] = None) -> list[str]:
    now = _localize(now or datetime.now(BEIJING_TZ))
    day_key = now.date().isoformat()
    users = store.load_state().get("daily_runs", {}).get(day_key, {}).get("users", {})
    if not users:
        return ["今日暂无状态"]
    roster = {u.student_id: u for u in store.list_users()}
    lines = ["今日进度:"]
    for student_id in sorted(users):
        user_state = users[student_id]
        status = str(user_state.get("status", "pending"))
        status_label = {
            "pending": "待执行",
            "waiting": "等待中",
            "window_done": "本时段完成",
            "completed": "今日完成",
            "error": "需要处理",
        }.get(status, status)
        completed = int(user_state.get("completed_rounds", 0))
        next_action = user_state.get("next_action") or "-"
        due_at = _parse_due_at(user_state.get("due_at"))
        detail = str(user_state.get("last_error") or user_state.get("last_message") or "")
        if due_at is not None and due_at > now:
            detail = f"剩余 {math.ceil((due_at - now).total_seconds())}s"
        count = store.get_last_count(student_id)
        count_text = f"TD={count}/{TD_LIMIT}" if count is not None else f"TD=?/{TD_LIMIT}"
        label = roster[student_id].display() if student_id in roster else student_id
        lines.append(f"  {label}: {status_label} 今日{completed}轮 next={next_action} {count_text} {detail}".rstrip())
    return lines


def _perform_due_action(
    store,
    client,
    user,
    user_state,
    now,
    wait_seconds,
    logger,
    window_key,
    max_wait_seconds,
):
    action = str(user_state.get("next_action") or "entrance")
    completed_rounds = int(user_state.get("completed_rounds", 0))
    round_no = completed_rounds + 1
    if completed_rounds >= user.rounds:
        user_state.update(_completed_user_state(user, now, f"今日已完成 {user.rounds}/{user.rounds}"))
        return True, "已完成"
    if action == "exit":
        return _perform_exit(
            store,
            client,
            user,
            user_state,
            now,
            logger,
            round_no,
            window_key,
        )
    return _perform_entrance(
        store,
        client,
        user,
        user_state,
        now,
        wait_seconds,
        logger,
        round_no,
        max_wait_seconds,
    )


def _perform_entrance(
    store,
    client,
    user,
    user_state,
    now,
    wait_seconds,
    logger,
    round_no,
    max_wait_seconds,
):
    logger.info("%s 第 %s/%s 轮入口", user.student_id, round_no, user.rounds)
    response = client.check(user, user.entrance_machine_id, timestamp=now.timestamp())
    if response.count is not None:
        store.set_last_count(user.student_id, response.count)
    logger.info("%s", response.server_message)
    if not response.success:
        if "非法时间" in response.server_message:
            _discard_current_round(user_state, user, now, "入口")
            return True, "非法时间，本轮作废"
        raise RuntimeError(response.server_message or "入口失败")
    client.upload_photo(user.entrance_machine_id, store.image_path(user.entrance_image).read_bytes(), timestamp=now.timestamp())
    if _reached_limit(store, user):
        message = f"TD 已达 {TD_LIMIT}"
        user_state.update(_completed_user_state(user, now, message))
        return True, message
    wait_s = min(
        max(0, int(wait_seconds(user))),
        max(0, int(max_wait_seconds)),
    )
    user_state.update(
        {
            "status": "waiting",
            "next_action": "exit",
            "due_at": _iso(now + timedelta(seconds=max(0, wait_s))),
            "last_message": f"入口完成，{wait_s}s 后出口",
            "last_error": None,
            "retry_count": 0,
            "updated_at": _iso(now),
        }
    )
    return True, user_state["last_message"]


def _perform_exit(
    store,
    client,
    user,
    user_state,
    now,
    logger,
    round_no,
    window_key,
):
    logger.info("%s 第 %s/%s 轮出口", user.student_id, round_no, user.rounds)
    response = client.check(user, user.exit_machine_id, timestamp=now.timestamp())
    if response.count is not None:
        store.set_last_count(user.student_id, response.count)
    logger.info("%s", response.server_message)
    if not response.success:
        if "非法时间" in response.server_message:
            _discard_current_round(user_state, user, now, "出口")
            return True, "非法时间，本轮作废"
        raise RuntimeError(response.server_message or "出口失败")
    client.upload_photo(user.exit_machine_id, store.image_path(user.exit_image).read_bytes(), timestamp=now.timestamp())
    completed_rounds = int(user_state.get("completed_rounds", 0)) + 1
    if completed_rounds >= user.rounds:
        message = f"今日已完成 {completed_rounds}/{user.rounds}"
        user_state.update(_completed_user_state(user, now, message, completed_rounds=completed_rounds))
        return True, message
    completed_windows = list(user_state.get("completed_windows", []))
    if window_key not in completed_windows:
        completed_windows.append(window_key)
    user_state.update(
        {
            "status": "window_done",
            "completed_rounds": completed_rounds,
            "completed_windows": completed_windows,
            "next_action": None,
            "due_at": None,
            "window_key": window_key,
            "retry_count": 0,
            "last_message": (
                f"本时段已完成，今日进度 {completed_rounds}/{user.rounds}"
            ),
            "last_error": None,
            "updated_at": _iso(now),
        }
    )
    return True, user_state["last_message"]


def _discard_current_round(user_state, user, now, action_label):
    round_no = int(user_state.get("completed_rounds", 0)) + 1
    user_state.update(
        {
            "status": "pending",
            "next_action": "entrance",
            "due_at": None,
            "window_key": None,
            "retry_count": 0,
            "last_message": f"第 {round_no}/{user.rounds} 轮{action_label}非法时间，本轮作废",
            "last_error": None,
            "updated_at": _iso(now),
        }
    )


def _load_today_user_states(store, users, now):
    day_key = now.date().isoformat()
    state = store.load_state()
    daily_runs = state.setdefault("daily_runs", {})
    day_state = daily_runs.setdefault(day_key, {"users": {}})
    user_states = day_state.setdefault("users", {})
    for user in users:
        user_states.setdefault(user.student_id, _new_user_state(now))
    return state, day_key, user_states


def _remove_deleted_user_states(user_states, users):
    active = {u.student_id for u in users}
    for student_id in list(user_states):
        if student_id not in active:
            user_states.pop(student_id, None)


def _discard_due_actions_outside_window(user_states, users, now, logger):
    for user in users:
        user_state = user_states[user.student_id]
        if user_state.get("status") != "waiting":
            continue
        user_state.update(
            {
                "status": "pending",
                "next_action": "entrance",
                "due_at": None,
                "window_key": None,
                "retry_count": 0,
                "last_message": "到期时已过窗口，下个窗口重来",
                "updated_at": _iso(now),
            }
        )
        logger.info("%s 到期时已过窗口", user.student_id)


def _status_batch(users, user_states):
    return BatchResult(
        [UserResult(i, u.student_id, user_states[u.student_id].get("status") != "error", str(user_states[u.student_id].get("status"))) for i, u in enumerate(users, 1)]
    )


def _new_user_state(now):
    return {
        "status": "pending",
        "completed_rounds": 0,
        "completed_windows": [],
        "next_action": "entrance",
        "due_at": None,
        "window_key": None,
        "retry_count": 0,
        "last_message": "等待入口",
        "last_error": None,
        "updated_at": _iso(now),
    }


def _completed_user_state(user, now, message, completed_rounds=None):
    return {
        "status": "completed",
        "completed_rounds": int(completed_rounds if completed_rounds is not None else user.rounds),
        "next_action": None,
        "due_at": None,
        "window_key": None,
        "retry_count": 0,
        "last_message": message,
        "last_error": None,
        "updated_at": _iso(now),
    }


def _reached_limit(store, user):
    count = store.get_last_count(user.student_id)
    return count is not None and count >= TD_LIMIT


def _active_window(now: datetime, windows) -> Optional[tuple[str, int, int]]:
    minutes = now.hour * 60 + now.minute
    for window in windows:
        start, end = parse_window(window)
        if start <= minutes <= end:
            return str(window), start, end
    return None


def _is_retryable(exc: Exception) -> bool:
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def _localize(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=BEIJING_TZ)
    return value.astimezone(BEIJING_TZ)


def _iso(value):
    return _localize(value).isoformat(timespec="seconds")


def _parse_due_at(value):
    if not value:
        return None
    return _localize(datetime.fromisoformat(str(value)))
