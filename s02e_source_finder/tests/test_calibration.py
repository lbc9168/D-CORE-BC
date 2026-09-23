"""Check paid search boundaries and quality scoring without external requests."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from s02e_source_finder.pilot import LabelSet
from s02e_source_finder.pilot_report import score, usage_summary
from s02e_source_finder.search import run, search


class CalibrationTests(unittest.TestCase):
    """Protect the few externally costly or misleading calibration failure paths."""

    def test_rate_limit_wait_and_result_limit(self):
        """A 429 waits at least 30 seconds and a larger response still yields five URLs."""
        rate = MagicMock(status_code=429, headers={"Retry-After": "1"})
        rate.__enter__.return_value = rate
        ok = MagicMock(status_code=200)
        ok.__enter__.return_value = ok
        ok.json.return_value = {"organic": [{"link": f"https://www.example.com/{n}"} for n in range(8)]}
        waits = []
        with patch("s02e_source_finder.search.requests.post", side_effect=[rate, ok]) as post:
            results, attempts = search("公开项目", "fake", sleeper=waits.append)
        self.assertEqual((len(results), attempts, post.call_count, waits), (5, 2, 2, [30]))

    def test_changed_query_cannot_replay_reserved_round(self):
        """An interrupted query keeps its event/round reservation even after query edits."""
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            selected = folder / "selected.json"
            context = {"project_id": "P1", "event_id": "E1", "project": {}, "event": {}, "entities": [], "source_articles": []}
            selected.write_text(json.dumps({"events": [{"context": context, "candidates": []}] * 30}), encoding="utf-8")
            (folder / "searches").mkdir()
            (folder / "searches" / "previous.reserved").write_text(json.dumps({"event_id": "E1", "round": 1, "query": "old"}), encoding="utf-8")
            with patch("s02e_source_finder.search.private_path", side_effect=lambda path, **kwargs: path), patch("s02e_source_finder.search.load_dotenv"), patch.dict("os.environ", {"SERPER_API_KEY": "fake"}), patch("s02e_source_finder.search.search") as paid:
                result = run(selected, execute=True, event_ids=["E1"], query_override="changed query")
            paid.assert_not_called()
            self.assertEqual(result["new_queries"], 0)

    def test_false_stage_and_identity_do_not_count_as_coverage(self):
        """Distinguish unsafe acceptance from a safe project-only downgrade."""
        labels = LabelSet(corpus_sha256="a", reviewer="test", created_at="now", labels={
            "a": {"decision": "project_reference", "reason": "no milestone", "evidence": []},
            "b": {"decision": "pending", "reason": "identity uncertain", "evidence": []},
            "c": {"decision": "event_reference", "reason": "event supported", "evidence": []}})
        results = [{"case_id": key, "project_id": key, "event_id": key, "decision": decision} for key, decision in [("a", "event_reference"), ("b", "project_reference"), ("c", "project_reference")]]
        result = score(results, labels)
        self.assertEqual((result["wrong_stage"], result["unsafe_accepts"], result["safe_projects"], result["safe_event_references"]), (1, 1, 1, 0))
        self.assertFalse(result["development_gate"])

    def test_missing_usage_is_not_reported_as_known_zero(self):
        """A billable failed response remains an explicit unknown-cost request."""
        result = usage_summary({"provider": "gemini", "attempted_calls": 1, "results": [{"model_called": True}]})
        self.assertEqual(result["usage_missing_requests"], 1)
        self.assertIsNone(result["no_cache_high_usd_per_call"])


if __name__ == "__main__":
    unittest.main()
