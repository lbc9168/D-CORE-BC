"""Exercise concurrent discovery reservations and bounded failure handling without external APIs."""

import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from s02e_source_finder.acceptance import search_round
from s02e_source_finder.pilot import PilotStore


class AcceptanceSearchTests(unittest.TestCase):
    """Keep SQLite in the coordinator while four provider calls are in flight."""

    def test_four_concurrent_calls_are_reserved_once_and_replay_without_calls(self):
        """Reach four workers together and verify that a replay consumes no new reservations."""
        barrier = threading.Barrier(4)

        def fake_search(request):
            """Wait for all four workers to prove actual concurrent dispatch."""
            barrier.wait(timeout=5)
            return {"value": request["value"]}

        jobs = [{"case_id": str(i), "request": {"value": i}} for i in range(4)]
        with tempfile.TemporaryDirectory() as directory, closing(PilotStore(Path(directory) / "calls.sqlite")) as store:
            store.begin("run", "inputs", "rules", "gemini_google_search", 4)
            with patch("s02e_source_finder.acceptance.grounding.search", side_effect=fake_search) as paid:
                results = search_round(jobs, store, "run", Path(directory), 4)
            self.assertEqual(paid.call_count, 4)
            self.assertEqual(len(results), 4)
            with patch("s02e_source_finder.acceptance.grounding.search") as paid:
                self.assertEqual(search_round(jobs, store, "run", Path(directory), 4), results)
            paid.assert_not_called()
            self.assertEqual(store.conn.execute("SELECT sum(requested) FROM attempts").fetchone()[0], 4)

    def test_provider_failure_records_inflight_calls_and_stops_next_batch(self):
        """Keep all four already dispatched outcomes and do not launch the remaining jobs."""
        jobs = [{"case_id": str(i), "request": {"value": i}} for i in range(8)]

        def fake_search(request):
            """Return one provider error among the initial four requests."""
            return {"error": "provider_http_429"} if request["value"] == 1 else {"value": request["value"]}

        with tempfile.TemporaryDirectory() as directory, closing(PilotStore(Path(directory) / "calls.sqlite")) as store:
            store.begin("run", "inputs", "rules", "gemini_google_search", 8)
            with patch("s02e_source_finder.acceptance.search_safely", side_effect=fake_search) as paid:
                results = search_round(jobs, store, "run", Path(directory), 4)
            self.assertEqual(paid.call_count, 4)
            self.assertEqual(len(results), 8)
            self.assertEqual(results[-1]["result"]["error"], "search_service_stopped")
            self.assertEqual(store.conn.execute("SELECT count(*) FROM attempts WHERE status='reserved'").fetchone()[0], 0)

    def test_partial_batch_budget_finishes_reserved_requests(self):
        """Finish two affordable requests and report the cap without leaving false reservations."""
        jobs = [{"case_id": str(i), "request": {"value": i}} for i in range(4)]
        with tempfile.TemporaryDirectory() as directory, closing(PilotStore(Path(directory) / "calls.sqlite")) as store:
            store.begin("run", "inputs", "rules", "gemini_google_search", 2)
            with patch("s02e_source_finder.acceptance.search_safely", return_value={"ok": True}) as paid:
                results = search_round(jobs, store, "run", Path(directory), 4)
            self.assertEqual(paid.call_count, 2)
            self.assertEqual(results[2]["result"]["error"], "search_budget_exhausted")
            self.assertEqual(store.conn.execute("SELECT count(*) FROM attempts WHERE status='reserved'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
