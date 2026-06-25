"""Tests for security config env overrides."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core.security_config import get_security_config


class SecurityConfigEnvTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_security_config.cache_clear()

    def test_env_overrides_yaml(self) -> None:
        with patch.dict(os.environ, {"SECURITY_ENABLED": "false", "SECURITY_ENTROPY_THRESHOLD": "5.0"}):
            get_security_config.cache_clear()
            cfg = get_security_config().security
            self.assertFalse(cfg.enabled)
            self.assertEqual(cfg.entropy_threshold, 5.0)


if __name__ == "__main__":
    unittest.main()
