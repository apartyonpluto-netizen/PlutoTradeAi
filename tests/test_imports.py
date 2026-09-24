import importlib
import unittest


class ImportTests(unittest.TestCase):
    def test_core_imports(self):
        modules = [
            "backend.app",
            "backend.services.market_data_service",
            "backend.services.mission_assignment_service",
            "backend.scanner.registry",
            "backend.brains.registry",
        ]

        for module in modules:
            loaded = importlib.import_module(module)
            self.assertIsNotNone(loaded)


if __name__ == "__main__":
    unittest.main()
