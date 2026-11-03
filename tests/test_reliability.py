import contextlib
import copy
import io
import json
import logging
import socket
import struct
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

from buaatd.const import BEIJING_TZ
from buaatd.models import CheckResponse
from buaatd.photo import DEFAULT_IMAGE_NAME, ensure_photos, import_photos, is_jpeg_file
from buaatd.scheduler import run_scheduler_once
from buaatd.store import Store
from buaatd.wizard import repair_user_assets, setup_user, setup_users


class FakeClient:
    def __init__(self):
        self.checks = []
        self.uploads = []

    def check(self, user, machine_id, timestamp=None, enforce_time_window=True):
        self.checks.append((machine_id, timestamp))
        return CheckResponse(True, "打卡成功，本学期锻炼次数：1", 1)

    def upload_photo(self, machine_id, photo, timestamp=None):
        self.uploads.append((machine_id, photo))
        return {"status": "success"}


class PhotoReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.store = Store(self.home)
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_new_user_without_photo_uses_default(self):
        user = setup_user(self.store, "22370001", "学院路", name="测试")
        self.assertEqual(user.entrance_image, DEFAULT_IMAGE_NAME)
        self.assertEqual(user.exit_image, DEFAULT_IMAGE_NAME)
        self.assertTrue(is_jpeg_file(self.store.image_path(DEFAULT_IMAGE_NAME)))
        self.assertGreater(self.store.image_path(DEFAULT_IMAGE_NAME).stat().st_size, 200)

    def test_custom_photo_is_used_and_preserved(self):
        custom = self.home / "自定义照片.jpg"
        ensure_photos(self.store.images_dir)
        default_path = self.store.image_path(DEFAULT_IMAGE_NAME)
        custom.write_bytes(default_path.read_bytes() + b"custom-photo")

        user = setup_user(
            self.store,
            "22370002",
            "沙河",
            photo_paths=[str(custom)],
        )
        self.assertNotEqual(user.entrance_image, DEFAULT_IMAGE_NAME)
        self.assertTrue(user.entrance_image.startswith("photo_"))
        self.assertEqual(user.entrance_image, user.exit_image)

        updated = setup_user(self.store, "22370002", "沙河", name="改名")
        self.assertEqual(updated.entrance_image, user.entrance_image)
        self.assertEqual(updated.entrance_machine_id, user.entrance_machine_id)

        reset = setup_user(
            self.store,
            "22370002",
            "沙河",
            name="改名",
            use_default_photos=True,
        )
        self.assertEqual(reset.entrance_image, DEFAULT_IMAGE_NAME)

    def test_imported_photos_have_unique_content_names(self):
        first = self.home / "one" / "photo.jpg"
        second = self.home / "two" / "photo.jpg"
        first.parent.mkdir()
        second.parent.mkdir()
        ensure_photos(self.store.images_dir)
        default_bytes = self.store.image_path(DEFAULT_IMAGE_NAME).read_bytes()
        first.write_bytes(default_bytes)
        second.write_bytes(default_bytes + b"different")

        names = import_photos(self.store.images_dir, [first, second])
        self.assertEqual(len(set(names)), 2)

    def test_batch_import_assigns_shared_photo(self):
        photo = self.home / "shared.jpg"
        ensure_photos(self.store.images_dir)
        photo.write_bytes(
            self.store.image_path(DEFAULT_IMAGE_NAME).read_bytes() + b"shared"
        )
        with contextlib.redirect_stdout(io.StringIO()):
            users = setup_users(
                self.store,
                [
                    {"student_id": "22370003", "name": "甲", "campus": "学院路"},
                    {"student_id": "22370004", "name": "乙", "campus": "沙河"},
                ],
                photo_paths=[str(photo)],
            )
        self.assertTrue(all(not user.entrance_image.startswith("default") for user in users))

    def test_repair_missing_photo_uses_default(self):
        user = setup_user(self.store, "22370005", "学院路")
        payload = copy.deepcopy(self.store.load_users())
        payload[user.student_id]["entrance_image"] = "missing.jpg"
        self.store.save_users(payload)

        messages = repair_user_assets(self.store)
        repaired = self.store.get_user_model(user.student_id)
        self.assertEqual(repaired.entrance_image, DEFAULT_IMAGE_NAME)
        self.assertTrue(messages)

    def test_repair_invalid_machine_uses_valid_campus_machine(self):
        user = setup_user(self.store, "22370007", "沙河")
        payload = copy.deepcopy(self.store.load_users())
        payload[user.student_id]["exit_machine_id"] = 999
        self.store.save_users(payload)

        repair_user_assets(self.store)
        repaired = self.store.get_user_model(user.student_id)
        valid_exit_ids = {
            machine["id"]
            for machine in self.store.load_config()["machine"]
            if machine["location"].startswith("北航沙河TD出口")
        }
        self.assertIn(repaired.exit_machine_id, valid_exit_ids)


class SchedulerFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.store.initialize()
        self.user = setup_user(self.store, "22370006", "学院路")

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_entrance_exit_cycle_uploads_default_photos(self):
        client = FakeClient()
        start = datetime(2026, 11, 3, 8, 0, tzinfo=BEIJING_TZ)

        run_scheduler_once(
            self.store,
            now=start,
            logger=None,
            client=client,
            wait_seconds=lambda _user: 0,
        )
        state = self.store.load_state()
        user_state = state["daily_runs"]["2026-11-03"]["users"][self.user.student_id]
        self.assertEqual(user_state["next_action"], "exit")
        self.assertEqual(client.checks[0][0], self.user.entrance_machine_id)

        run_scheduler_once(
            self.store,
            now=datetime(2026, 11, 3, 8, 0, 1, tzinfo=BEIJING_TZ),
            logger=None,
            client=client,
            wait_seconds=lambda _user: 0,
        )
        state = self.store.load_state()
        user_state = state["daily_runs"]["2026-11-03"]["users"][self.user.student_id]
        self.assertEqual(user_state["completed_rounds"], 1)
        self.assertEqual(user_state["next_action"], "entrance")
        self.assertEqual(
            [machine for machine, _ in client.checks],
            [self.user.entrance_machine_id, self.user.exit_machine_id],
        )
        self.assertEqual(len(client.uploads), 2)
        self.assertTrue(all(data.startswith(b"\xff\xd8\xff") for _, data in client.uploads))

        for second in range(2, 6):
            run_scheduler_once(
                self.store,
                now=datetime(2026, 11, 3, 8, 0, second, tzinfo=BEIJING_TZ),
                logger=None,
                client=client,
                wait_seconds=lambda _user: 0,
            )
        state = self.store.load_state()
        user_state = state["daily_runs"]["2026-11-03"]["users"][self.user.student_id]
        self.assertEqual(user_state["status"], "completed")
        self.assertEqual(user_state["completed_rounds"], 3)
        self.assertEqual(len(client.checks), 6)

        run_scheduler_once(
            self.store,
            now=datetime(2026, 11, 3, 11, 30, tzinfo=BEIJING_TZ),
            logger=None,
            client=client,
            wait_seconds=lambda _user: 0,
        )
        self.assertEqual(len(client.checks), 6)
        state = self.store.load_state()
        user_state = state["daily_runs"]["2026-11-03"]["users"][self.user.student_id]
        self.assertEqual(user_state["status"], "completed")
        self.assertEqual(user_state["completed_rounds"], 3)

    def test_real_socket_protocol_completes_three_rounds(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        listener.settimeout(1)
        port = listener.getsockname()[1]
        config = self.store.load_config()
        config["server"]["ip"] = "127.0.0.1"
        config["server"]["port"] = port
        config["server"]["timeout"] = 2
        self.store.save_config(config)

        request_types = []
        upload_bodies = []
        errors = []

        def recv_exact(connection, size):
            data = b""
            while len(data) < size:
                chunk = connection.recv(size - len(data))
                if not chunk:
                    raise ConnectionError("closed")
                data += chunk
            return data

        def serve():
            try:
                while len(request_types) < 12:
                    try:
                        connection, _ = listener.accept()
                    except socket.timeout:
                        continue
                    with connection:
                        length, request_type = struct.unpack(">lB", recv_exact(connection, 5))
                        body = recv_exact(connection, length)
                        request_types.append(request_type)
                        if request_type == 100:
                            upload_bodies.append(body)
                        response = (
                            {
                                "status": "success",
                                "srvresp": "打卡成功，本学期锻炼次数：1",
                            }
                            if request_type == 80
                            else {"status": "success"}
                        )
                        payload = json.dumps(response, ensure_ascii=False).encode("utf-8")
                        connection.sendall(struct.pack(">lB", len(payload), request_type) + payload)
            except Exception as exc:
                errors.append(exc)
            finally:
                listener.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        start = datetime(2026, 11, 3, 11, 30, tzinfo=BEIJING_TZ)
        for second in range(6):
            run_scheduler_once(
                self.store,
                now=datetime(
                    2026,
                    11,
                    3,
                    11,
                    30,
                    second,
                    tzinfo=BEIJING_TZ,
                ),
                wait_seconds=lambda _user: 0,
            )
        thread.join(timeout=3)

        self.assertFalse(errors)
        self.assertEqual(request_types, [80, 100] * 6)
        self.assertEqual(len(upload_bodies), 6)
        self.assertTrue(all(b"\xff\xd8\xff" in body for body in upload_bodies))
        user_state = self.store.load_state()["daily_runs"]["2026-11-03"]["users"][
            self.user.student_id
        ]
        self.assertEqual(user_state["completed_rounds"], 3)
        self.assertEqual(user_state["status"], "completed")

    def test_transient_network_error_retries_in_same_window(self):
        class TransientClient(FakeClient):
            def check(self, user, machine_id, timestamp=None, enforce_time_window=True):
                if not self.checks:
                    self.checks.append((machine_id, timestamp))
                    raise ConnectionError("temporary network error")
                return super().check(
                    user,
                    machine_id,
                    timestamp=timestamp,
                    enforce_time_window=enforce_time_window,
                )

        client = TransientClient()
        logger = logging.getLogger("test.buaatd.transient")
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        run_scheduler_once(
            self.store,
            now=datetime(2026, 11, 3, 15, 30, tzinfo=BEIJING_TZ),
            wait_seconds=lambda _user: 0,
            client=client,
            logger=logger,
        )
        state = self.store.load_state()
        user_state = state["daily_runs"]["2026-11-03"]["users"][self.user.student_id]
        self.assertEqual(user_state["status"], "waiting")
        self.assertEqual(user_state["retry_count"], 1)
        self.assertEqual(user_state["next_action"], "entrance")

        run_scheduler_once(
            self.store,
            now=datetime(2026, 11, 3, 15, 31, 1, tzinfo=BEIJING_TZ),
            wait_seconds=lambda _user: 0,
            client=client,
            logger=logger,
        )
        state = self.store.load_state()
        user_state = state["daily_runs"]["2026-11-03"]["users"][self.user.student_id]
        self.assertEqual(user_state["status"], "waiting")
        self.assertEqual(user_state["next_action"], "exit")
        self.assertEqual(user_state["retry_count"], 0)

    def test_rounds_are_spaced_then_continue_in_same_window(self):
        client = FakeClient()
        run_scheduler_once(
            self.store,
            now=datetime(2026, 11, 3, 7, 30, tzinfo=BEIJING_TZ),
            wait_seconds=lambda _user: 180,
            client=client,
        )
        state = self.store.load_state()
        user_state = state["daily_runs"]["2026-11-03"]["users"][self.user.student_id]
        self.assertEqual(user_state["next_action"], "exit")
        self.assertTrue(user_state["due_at"].endswith("07:33:00+08:00"))

        run_scheduler_once(
            self.store,
            now=datetime(2026, 11, 3, 7, 33, 1, tzinfo=BEIJING_TZ),
            wait_seconds=lambda _user: 180,
            client=client,
        )
        state = self.store.load_state()
        user_state = state["daily_runs"]["2026-11-03"]["users"][self.user.student_id]
        self.assertEqual(user_state["completed_rounds"], 1)
        self.assertEqual(user_state["next_action"], "entrance")
        self.assertTrue(user_state["due_at"].endswith("07:36:01+08:00"))

        run_scheduler_once(
            self.store,
            now=datetime(2026, 11, 3, 7, 36, 2, tzinfo=BEIJING_TZ),
            wait_seconds=lambda _user: 180,
            client=client,
        )
        state = self.store.load_state()
        user_state = state["daily_runs"]["2026-11-03"]["users"][self.user.student_id]
        self.assertEqual(user_state["completed_rounds"], 1)
        self.assertEqual(user_state["next_action"], "exit")
        self.assertEqual(len(client.checks), 3)


if __name__ == "__main__":
    unittest.main()
