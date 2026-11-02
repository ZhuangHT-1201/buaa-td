import argparse
import os
import signal
import sys
from pathlib import Path
from typing import List, Optional

from .background import daemon_status, start_daemon, stop_daemon
from .client import TDClient
from .const import TD_LIMIT
from .logutil import setup_logging
from .machines import campus_label
from .runner import run_all_users
from .scheduler import clear_today_errors, format_today_status, run_forever
from .store import Store
from .wizard import (
    default_users_file,
    interactive_setup,
    load_users_file,
    repair_user_assets,
    setup_user,
    setup_users,
)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if not hasattr(args, "func"):
            parser.print_help()
            return 2
        return int(args.func(args) or 0)
    except Exception as exc:
        print(f"error: {exc}")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="buaatd")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("setup", help="批量配置并后台启动，自动打满")
    p.add_argument("--file", help="用户列表文件，默认 users.txt")
    p.add_argument("--id", action="append", default=[], help="可重复: --id 学号")
    p.add_argument("--name", action="append", default=[], help="与 --id 一一对应")
    p.add_argument("--campus", action="append", default=[], help="与 --id 一一对应，默认学院路")
    p.add_argument("--photo", action="append", default=[])
    p.add_argument("--once", action="store_true")
    p.add_argument("--no-start", action="store_true")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("start", help="后台启动已有用户")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("stop", help="停止后台")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("status", help="查看状态")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("once", help="立刻给所有人打一轮")
    p.set_defaults(func=cmd_once)

    p = sub.add_parser("count", help="查询 TD 次数")
    p.add_argument("student_id", nargs="?")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_count)

    p = sub.add_parser("add", help="追加一人")
    p.add_argument("student_id")
    p.add_argument("--name", default="")
    p.add_argument("--campus", default="学院路")
    p.add_argument("--photo", action="append", default=[])
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="列出已配置用户")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("gui", help="打开可视化控制台")
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("daemon", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_daemon)

    return parser


def cmd_setup(args) -> int:
    store = Store()
    store.initialize()

    if args.id:
        campuses = args.campus or []
        names = args.name or []
        rows = []
        for i, student_id in enumerate(args.id):
            rows.append(
                {
                    "student_id": student_id,
                    "name": names[i] if i < len(names) else "",
                    "campus": campuses[i] if i < len(campuses) else (campuses[-1] if campuses else "学院路"),
                }
            )
        setup_users(store, rows, photo_paths=args.photo or None)
    else:
        file_path = Path(args.file) if args.file else default_users_file()
        if file_path.exists():
            print(f"读取 {file_path}")
            rows = load_users_file(file_path)
            setup_users(store, rows, photo_paths=args.photo or None)
        elif store.list_users() and _ask_reuse():
            print(f"沿用现有 {len(store.list_users())} 人")
        else:
            interactive_setup(store)

    _print_roster(store)
    if args.once:
        return cmd_once(args)
    if args.no_start:
        return 0
    return cmd_start(args)


def cmd_start(args) -> int:
    store = Store()
    store.initialize()
    users = store.list_users()
    if not users:
        print("没有用户，先运行 一键开始.bat 或 python -m buaatd setup")
        return 1
    for message in repair_user_assets(store):
        print(f"自动修复: {message}")
    clear_today_errors(store)
    result = start_daemon(store)
    if result.already_running:
        print(f"后台已在运行 PID={result.pid}")
    else:
        print(f"后台已启动 PID={result.pid}")
    print(f"共 {len(users)} 人，每人每天最多 3 次，满 {TD_LIMIT} 次自动跳过")
    print("窗口: 07:30-10:00 / 11:30-14:00 / 15:30-20:00")
    print("电脑保持开机联网即可，打满不用管")
    print(f"日志: {store.logs_dir}")
    return 0


def cmd_stop(args) -> int:
    store = Store()
    result = stop_daemon(store)
    print(result.message)
    return 0


def cmd_status(args) -> int:
    store = Store()
    store.initialize()
    bg = daemon_status(store)
    print(bg.message)
    _print_roster(store)
    for line in format_today_status(store):
        print(line)
    return 0


def cmd_once(args) -> int:
    store = Store()
    store.initialize()
    logger = setup_logging(store, stream=True)
    batch = run_all_users(store, logger=logger)
    print(f"完成 total={batch.total} ok={batch.success_count} fail={batch.failure_count}")
    for item in batch.results:
        print(f"  {item.student_id}: {'OK' if item.success else 'FAIL'} {item.message}")
    return 0 if batch.failure_count == 0 else 1


def cmd_count(args) -> int:
    store = Store()
    store.initialize()
    users = store.list_users()
    if args.student_id:
        users = [u for u in users if u.student_id == args.student_id]
        if not users:
            users = [store.get_user_model(args.student_id)]
    if not users:
        print("没有用户")
        return 1
    if not args.refresh:
        for user in users:
            print(f"{user.display()}: {store.get_last_count(user.student_id)}")
        return 0
    client = TDClient(store.load_config())
    for user in users:
        count = client.query_count(user)
        store.set_last_count(user.student_id, count)
        print(f"{user.display()}: {count}")
    return 0


def cmd_add(args) -> int:
    store = Store()
    store.initialize()
    user = setup_user(
        store,
        args.student_id,
        args.campus,
        name=args.name,
        photo_paths=args.photo or None,
    )
    print(f"已添加 {user.display()} / {campus_label(args.campus)}")
    return 0


def cmd_list(args) -> int:
    store = Store()
    store.initialize()
    _print_roster(store)
    return 0


def cmd_gui(args) -> int:
    from .gui import main as gui_main

    return gui_main()


def cmd_daemon(args) -> int:
    store = Store()
    store.initialize()
    logger = setup_logging(store, stream=False)
    for message in repair_user_assets(store):
        logger.info("自动修复: %s", message)
    stop = {"flag": False}

    def _handle(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _handle)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _handle)
    logger.info("daemon started pid=%s", os.getpid())
    run_forever(store, stop_requested=lambda: stop["flag"], logger=logger)
    logger.info("daemon stopped")
    return 0


def _print_roster(store: Store) -> None:
    users = store.list_users()
    if not users:
        print("当前无人")
        return
    print(f"用户列表 ({len(users)}):")
    for user in users:
        count = store.get_last_count(user.student_id)
        count_text = f"{count}/{TD_LIMIT}" if count is not None else f"?/{TD_LIMIT}"
        print(f"  {user.display()}  TD={count_text}  入{user.entrance_machine_id}/出{user.exit_machine_id}")


def _ask_reuse() -> bool:
    if not sys.stdin.isatty():
        return True
    ans = input("检测到已有配置，直接启动? [Y/n]: ").strip().lower()
    return ans in ("", "y", "yes")


if __name__ == "__main__":
    raise SystemExit(main())
