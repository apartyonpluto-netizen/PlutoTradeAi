import unittest

from backend.app import create_app


class TemplateTests(unittest.TestCase):
    def setUp(self):
        app = create_app()
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_core_templates_render(self):
        pages = [
            "/",
            "/missions",
            "/scanners",
            "/brains",
            "/stock/SPY",
            "/futures",
        ]

        for path in pages:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, msg=path)
            self.assertIn(b"PlutoTrade AI", response.data)


if __name__ == "__main__":
    unittest.main()
