import random
import sys
from pathlib import Path
from typing import Iterable

from .machines import campus_label, machine_pool, normalize_campus
from .models import User
from .paths import project_root
from .photo import ensure_photos, import_photos, is_jpeg_file
from .store import Store


def setup_user(
    store: Store,
    student_id: str,
    campus: str,
    name: str = "",
    photo_paths: list[str] | None = None,
    rounds: int | None = None,
    use_default_photos: bool = False,
    prepared_photos: list[str] | None = None,
) -> User:
    store.initialize()
    normalize_campus(campus)
    entrances = machine_pool(campus, entrance=True)
    exits = machine_pool(campus, entrance=False)
    if not entrances or not exits:
        raise ValueError(f"{campus_label(campus)} 机位配置缺失")

    existing = None
    try:
        existing = store.get_user_model(student_id.strip())
    except (KeyError, ValueError):
        pass

    default_images = ensure_photos(store.images_dir)
    imported_images = (
        import_photos(store.images_dir, [Path(path) for path in photo_paths])
        if photo_paths
        else list(prepared_photos or [])
    )
    entrance_ids = {int(machine["id"]) for machine in entrances}
    exit_ids = {int(machine["id"]) for machine in exits}
    keep_machines = (
        existing is not None
        and existing.entrance_machine_id in entrance_ids
        and existing.exit_machine_id in exit_ids
    )

    if imported_images:
        entrance_image = random.choice(imported_images)
        exit_image = random.choice(imported_images)
    elif (
        existing is not None
        and not use_default_photos
        and is_jpeg_file(store.image_path(existing.entrance_image))
        and is_jpeg_file(store.image_path(existing.exit_image))
    ):
        entrance_image = existing.entrance_image
        exit_image = existing.exit_image
    else:
        entrance_image = random.choice(default_images)
        exit_image = random.choice(default_images)

    user = User.from_dict(
        {
            "student_id": student_id.strip(),
            "name": (name or "").strip(),
            "card_id": "",
            "entrance_machine_id": (
                existing.entrance_machine_id
                if keep_machines
                else random.choice(entrances)["id"]
            ),
            "exit_machine_id": (
                existing.exit_machine_id
                if keep_machines
                else random.choice(exits)["id"]
            ),
            "entrance_image": entrance_image,
            "exit_image": exit_image,
            **({"rounds": rounds} if rounds is not None else {}),
        }
    )
    store.save_user(user)
    return user


def setup_users(store: Store, rows: Iterable[dict], photo_paths: list[str] | None = None) -> list[User]:
    store.initialize()
    shared_photos = (
        import_photos(store.images_dir, [Path(path) for path in photo_paths])
        if photo_paths
        else []
    )
    ensure_photos(store.images_dir)
    users = []
    for row in rows:
        user = setup_user(
            store,
            student_id=row["student_id"],
            campus=row["campus"],
            name=row.get("name", ""),
            prepared_photos=shared_photos or None,
        )
        users.append(user)
        if sys.stdout is not None:
            print(
                f"已添加 {user.display()} / {campus_label(row['campus'])} "
                f"入口={user.entrance_machine_id} 出口={user.exit_machine_id}"
            )
    return users


def repair_user_assets(store: Store) -> list[str]:
    """Repair missing photos and invalid machine assignments for existing users."""
    store.initialize()
    default_image = ensure_photos(store.images_dir)[0]
    users = store.load_users()
    config = store.load_config()
    machine_ids = {int(machine["id"]) for machine in config.get("machine", [])}
    shahe_entrances = {
        int(machine["id"]) for machine in machine_pool("沙河", entrance=True)
    }
    shahe_exits = {
        int(machine["id"]) for machine in machine_pool("沙河", entrance=False)
    }
    messages = []
    for student_id, raw in list(users.items()):
        data = dict(raw)
        changed = False
        for key, label in (("entrance_image", "入口"), ("exit_image", "出口")):
            image_name = Path(str(data.get(key) or "")).name
            if not image_name or not is_jpeg_file(store.image_path(image_name)):
                data[key] = default_image
                changed = True
                messages.append(f"{student_id} 的{label}照片已回退为默认图片")
            elif image_name != data.get(key):
                data[key] = image_name
                changed = True

        entrance_id = _safe_int(data.get("entrance_machine_id"))
        exit_id = _safe_int(data.get("exit_machine_id"))
        campus = (
            "沙河"
            if entrance_id in shahe_entrances or exit_id in shahe_exits
            else "学院路"
        )
        if entrance_id not in machine_ids:
            data["entrance_machine_id"] = random.choice(
                machine_pool(campus, entrance=True)
            )["id"]
            changed = True
            messages.append(f"{student_id} 的入口机位已自动修复")
        if exit_id not in machine_ids:
            data["exit_machine_id"] = random.choice(
                machine_pool(campus, entrance=False)
            )["id"]
            changed = True
            messages.append(f"{student_id} 的出口机位已自动修复")
        if changed:
            users[student_id] = data
    if messages:
        store.save_users(users)
    return messages


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def interactive_setup(store: Store) -> list[User]:
    print("=== 北航 TD 批量配置 ===")
    print("每人一行信息，全部录完后自动后台打到 32 次停")
    count_text = input("人数 [1]: ").strip() or "1"
    count = int(count_text)
    if count <= 0:
        raise ValueError("人数必须大于 0")
    rows = []
    for i in range(1, count + 1):
        print(f"--- 第 {i}/{count} 人 ---")
        student_id = input("学号: ").strip()
        if not student_id:
            raise ValueError("学号不能为空")
        name = input("姓名(可空): ").strip()
        print("校区: 1=学院路  2=沙河")
        campus = input("选择 [1]: ").strip() or "1"
        rows.append({"student_id": student_id, "name": name, "campus": campus})
    photo = input("公共打卡图片路径(可空,多张用;分隔): ").strip()
    paths = [p.strip() for p in photo.replace(",", ";").split(";") if p.strip()] if photo else None
    return setup_users(store, rows, photo_paths=paths)


def load_users_file(path: Path) -> list[dict]:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))
    rows = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace(",", " ").replace("\t", " ").split()
        if len(parts) < 2:
            raise ValueError(f"{path.name}:{line_no} 格式错误，需要: 学号 姓名 校区")
        if len(parts) == 2:
            student_id, campus = parts
            name = ""
        else:
            student_id, name, campus = parts[0], parts[1], parts[2]
            if len(parts) > 3:
                campus = parts[-1]
                name = " ".join(parts[1:-1])
        rows.append({"student_id": student_id, "name": name, "campus": campus})
    if not rows:
        raise ValueError(f"{path.name} 为空")
    return rows


def default_users_file() -> Path:
    return project_root() / "users.txt"
