import unittest

import backend.app as app_module


class RenderStartupTests(unittest.TestCase):
    def test_app_object_exists(self):
        self.assertIsNotNone(app_module.app)

    def test_app_factory_starts(self):
        app = app_module.create_app()
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
