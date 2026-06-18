from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.modules.setdefault("pystray", types.SimpleNamespace())

import launcher


@unittest.skipUnless(sys.platform == "win32", "Windows process path regression")
class FrozenApplicationRootTests(unittest.TestCase):
    def test_ignores_corrupt_sys_executable(self) -> None:
        with patch.object(sys, "executable", "\u1d902"):
            root = launcher.frozen_application_root()

        self.assertTrue(root.is_absolute())
        self.assertEqual(root, Path(root))
        self.assertTrue(root.name)


if __name__ == "__main__":
    unittest.main()
