import string
import unittest

from buaatd.i18n import TRANSLATIONS, translate


def placeholders(template: str) -> set[str]:
    return {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(template)
        if field_name
    }


class TranslationCatalogTests(unittest.TestCase):
    def test_catalogs_have_matching_keys_and_placeholders(self):
        chinese = TRANSLATIONS["zh"]
        english = TRANSLATIONS["en"]
        self.assertEqual(set(chinese), set(english))
        for key in chinese:
            self.assertEqual(
                placeholders(chinese[key]),
                placeholders(english[key]),
                msg=f"Placeholder mismatch for translation key: {key}",
            )

    def test_language_switching_translates_user_facing_text(self):
        self.assertEqual(translate("zh", "start_auto"), "启动自动打卡")
        self.assertEqual(translate("en", "start_auto"), "Start automation")
        self.assertEqual(
            translate("en", "member_count", count=3),
            "3 members",
        )


if __name__ == "__main__":
    unittest.main()
