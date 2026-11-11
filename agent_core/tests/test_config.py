import os
import unittest
from unittest.mock import patch

from vespera_agent.config import daily_budget_usd


class ConfigTests(unittest.TestCase):
    def test_daily_budget_defaults_and_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(daily_budget_usd(), 1.0)
        with patch.dict(os.environ, {"VESPERA_DAILY_BUDGET_USD": "2.5"}):
            self.assertEqual(daily_budget_usd(), 2.5)

    def test_invalid_daily_budget_is_explicit(self):
        for value in ("", "0", "-1", "NaN", "inf", "abc"):
            with self.subTest(value=value), patch.dict(os.environ, {"VESPERA_DAILY_BUDGET_USD": value}):
                with self.assertRaisesRegex(ValueError, "VESPERA_DAILY_BUDGET_USD"):
                    daily_budget_usd()
