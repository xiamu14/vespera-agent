from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic_ai.messages import ModelResponse, TextPart
from vespera_agent.inspector import usage_cost


class CostTests(unittest.IsolatedAsyncioTestCase):
    async def test_total_includes_previous_days_without_counting_inherited_messages_twice(self):
        now = datetime.now().astimezone()
        yesterday = now - timedelta(days=1)
        old = ModelResponse(parts=[TextPart("old")], model_name="test", run_id="old", timestamp=yesterday)
        current = ModelResponse(parts=[TextPart("new")], model_name="test", run_id="new", timestamp=now)
        runs = [SimpleNamespace(run_id="old", started_at=yesterday),
                SimpleNamespace(run_id="new", started_at=now)]
        async def snapshot(*, run_id, include_interrupted):
            return SimpleNamespace(messages=[old] if run_id == "old" else [old, current])
        store = SimpleNamespace(latest_snapshot=snapshot)
        with patch.object(ModelResponse, "cost", return_value=SimpleNamespace(total_price=Decimal("0.11"))):
            self.assertEqual(await usage_cost(store, runs), 0.22)
            self.assertEqual(await usage_cost(store, runs, now.date()), 0.11)
        with patch.object(ModelResponse, "cost", side_effect=ValueError("unknown price")):
            self.assertIsNone(await usage_cost(store, runs))
        self.assertEqual(await usage_cost(store, []), 0.0)
