import logging
import random
import time
from typing import Callable, Iterable, Optional

from .client import TDClient
from .const import TD_LIMIT
from .models import BatchResult, User, UserResult
from .store import Store

Sleeper = Callable[[int], None]


def run_user_pipeline(
    store: Store,
    client: TDClient,
    user: User,
    sleeper: Sleeper = None,
    logger: Optional[logging.Logger] = None,
) -> bool:
    sleeper = sleeper or _sleep
    logger = logger or logging.getLogger("buaatd")
    entrance_photo = store.image_path(user.entrance_image).read_bytes()
    exit_photo = store.image_path(user.exit_image).read_bytes()

    for round_idx in range(user.rounds):
        if _reached(store, user):
            logger.info("%s TD 已达 %s", user.student_id, TD_LIMIT)
            return True

        logger.info("%s 第 %s/%s 轮入口", user.student_id, round_idx + 1, user.rounds)
        entrance = client.check(user, user.entrance_machine_id)
        if entrance.count is not None:
            store.set_last_count(user.student_id, entrance.count)
        logger.info("%s", entrance.server_message)
        if not entrance.success:
            return False
        client.upload_photo(user.entrance_machine_id, entrance_photo)
        if _reached(store, user):
            logger.info("%s TD 已达 %s", user.student_id, TD_LIMIT)
            return True

        wait_s = random.randint(user.wait_time_min, user.wait_time_max)
        logger.info("等待 %ss 后出口", wait_s)
        sleeper(wait_s)

        logger.info("%s 第 %s/%s 轮出口", user.student_id, round_idx + 1, user.rounds)
        exit_response = client.check(user, user.exit_machine_id)
        if exit_response.count is not None:
            store.set_last_count(user.student_id, exit_response.count)
        logger.info("%s", exit_response.server_message)
        if not exit_response.success:
            return False
        client.upload_photo(user.exit_machine_id, exit_photo)

        if round_idx < user.rounds - 1:
            wait_s = random.randint(user.wait_time_min, user.wait_time_max)
            logger.info("等待 %ss 后下一轮", wait_s)
            sleeper(wait_s)

    return True


def run_all_users(
    store: Store,
    client: Optional[TDClient] = None,
    users: Optional[Iterable[User]] = None,
    sleeper: Sleeper = None,
    logger: Optional[logging.Logger] = None,
) -> BatchResult:
    logger = logger or logging.getLogger("buaatd")
    client = client or TDClient(store.load_config())
    user_list = list(users) if users is not None else store.list_users()
    results = []
    for index, user in enumerate(user_list, start=1):
        try:
            ok = run_user_pipeline(store, client, user, sleeper=sleeper, logger=logger)
            results.append(UserResult(index, user.student_id, ok, "成功" if ok else "失败"))
        except Exception as exc:
            logger.exception("%s 失败: %s", user.student_id, exc)
            results.append(UserResult(index, user.student_id, False, str(exc)))
    return BatchResult(results)


def _reached(store: Store, user: User) -> bool:
    count = store.get_last_count(user.student_id)
    return count is not None and count >= TD_LIMIT


def _sleep(seconds: int) -> None:
    for _ in range(max(0, seconds)):
        time.sleep(1)
