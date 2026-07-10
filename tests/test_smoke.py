import unittest

from backend.app import create_app


class SmokeTests(unittest.TestCase):
    def setUp(self):
        app = create_app()
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_homepage_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Mission Control", response.data)

    def test_mission_center_renders(self):
        response = self.client.get("/missions")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Mission Assignment Center", response.data)

    def test_futures_renders(self):
        response = self.client.get("/futures")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Futures Command", response.data)

    def test_workspace_sections_render(self):
        for path in ["/portfolio", "/account", "/journal", "/settings"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)

    def test_health_route(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])

    def test_status_route(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])


if __name__ == "__main__":
    unittest.main()
