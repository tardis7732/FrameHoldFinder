"""Run the test suite from any working directory."""

from pathlib import Path
import sys
import unittest


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository / "src"))
    suite = unittest.defaultTestLoader.discover(
        start_dir=str(repository / "tests"), pattern="test_*.py"
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
