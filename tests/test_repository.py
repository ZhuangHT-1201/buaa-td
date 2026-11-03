import unittest

from buaatd.paths import project_root


class RepositoryLayoutTests(unittest.TestCase):
    def test_only_one_batch_launcher_is_present(self):
        launchers = sorted(path.name for path in project_root().glob("*.bat"))
        self.assertEqual(launchers, ["一键启动.bat"])


if __name__ == "__main__":
    unittest.main()
