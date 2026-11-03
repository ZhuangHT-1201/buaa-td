import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from buaatd.gui import Dashboard, MemberDialog
from buaatd.photo import DEFAULT_IMAGE_NAME, ensure_photos
from buaatd.store import Store


class MemberDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_home = os.environ.get("BUAATD_HOME")
        os.environ["BUAATD_HOME"] = self.temp.name
        self.store = Store(Path(self.temp.name))
        try:
            self.app = Dashboard(self.store)
            self.app.withdraw()
            self.app.update()
        except Exception as exc:
            self.temp.cleanup()
            self.skipTest(f"Tk UI unavailable: {exc}")

    def tearDown(self):
        if hasattr(self, "app"):
            self.app._close_console()
        if self.previous_home is None:
            os.environ.pop("BUAATD_HOME", None)
        else:
            os.environ["BUAATD_HOME"] = self.previous_home
        self.temp.cleanup()

    def test_add_without_photo_uses_default(self):
        dialog = MemberDialog(self.app, self.store)
        dialog.update()
        dialog.sid_var.set("22374001")
        dialog.name_var.set("Default Photo")
        dialog.campus_var.set("学院路")
        dialog._save()
        user = self.store.get_user_model("22374001")
        self.assertEqual(user.entrance_image, DEFAULT_IMAGE_NAME)
        self.assertEqual(user.exit_image, DEFAULT_IMAGE_NAME)

    def test_add_with_photo_and_edit_preserves_it(self):
        ensure_photos(self.store.images_dir)
        source = Path(self.temp.name) / "custom.jpg"
        source.write_bytes(
            self.store.image_path(DEFAULT_IMAGE_NAME).read_bytes() + b"custom"
        )
        dialog = MemberDialog(self.app, self.store)
        dialog.update()
        dialog.sid_var.set("22374002")
        dialog.name_var.set("Custom Photo")
        dialog.campus_var.set("沙河")
        dialog.selected_photo = str(source)
        dialog._save()

        user = self.store.get_user_model("22374002")
        self.assertTrue(user.entrance_image.startswith("photo_"))
        edit = MemberDialog(self.app, self.store, existing=user)
        edit.update()
        edit.name_var.set("Edited")
        edit._save()
        edited = self.store.get_user_model("22374002")
        self.assertEqual(edited.name, "Edited")
        self.assertEqual(edited.entrance_image, user.entrance_image)
        self.assertEqual(edited.entrance_machine_id, user.entrance_machine_id)

    def test_language_switch_rebuilds_and_persists(self):
        self.assertEqual(self.app.language, "zh")
        self.app._toggle_language()
        self.app.update()
        self.assertEqual(self.app.language, "en")
        self.assertEqual(self.app.title(), "BUAA TD Console")
        self.assertEqual(self.app.language_button.cget("text"), "中文")
        self.assertEqual(
            self.store.load_settings()["ui"]["language"],
            "en",
        )

        dialog = MemberDialog(self.app, self.store)
        dialog.update()
        self.assertEqual(dialog.title(), "Add member")
        self.assertEqual(
            list(dialog.campus_values),
            ["Xueyuanlu", "Shahe"],
        )
        dialog.sid_var.set("22374003")
        dialog.name_var.set("English UI")
        dialog.campus_var.set("Shahe")
        dialog._save()
        user = self.store.get_user_model("22374003")
        shahe_entrances = set(range(8, 11))
        shahe_exits = set(range(11, 14))
        self.assertIn(user.entrance_machine_id, shahe_entrances)
        self.assertIn(user.exit_machine_id, shahe_exits)

    def test_unchanged_refresh_does_not_rebuild_member_table(self):
        self.app.refresh()
        self.app.update()
        if self.app._refresh_after_id is not None:
            self.app.after_cancel(self.app._refresh_after_id)
            self.app._refresh_after_id = None
        with patch.object(
            self.app.tree,
            "delete",
            side_effect=AssertionError("tree should stay unchanged"),
        ):
            self.app.refresh()


if __name__ == "__main__":
    unittest.main()
