"""Pilot ledger tests protect budget reservations, immutability and zero-call previews."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from s02e_source_finder.contracts import EventContext, Page
from s02e_source_finder.pilot import PilotCase, PilotCorpus, PilotStore, evaluate, prepare


class StoreTests(unittest.TestCase):
    """Exercise real SQLite transactions rather than mocking persistence."""

    def test_reservation_is_durable_and_budget_is_not_reset_on_resume(self):
        """A crashed paid attempt remains reserved and cannot be called twice."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pilot.sqlite"
            store = PilotStore(path)
            store.begin("run", "corpus", "rules", "deepseek", 1)
            self.assertTrue(store.reserve("run", "case1", requested=True))
            store.close()
            store = PilotStore(path)
            try:
                store.begin("run", "corpus", "rules", "deepseek", 1)
                self.assertFalse(store.reserve("run", "case1", requested=True))
                with self.assertRaisesRegex(ValueError, "budget_exhausted"):
                    store.reserve("run", "case2", requested=True)
                self.assertTrue(store.reserve("run", "case2", requested=False))
                store.finish("run", "case2", {"decision": "pending"})
                with self.assertRaisesRegex(ValueError, "already_final"):
                    store.finish("run", "case2", {"decision": "event_reference"})
                with self.assertRaisesRegex(ValueError, "contract_changed"):
                    store.begin("run", "corpus", "newrules", "deepseek", 20)
            finally:
                store.close()

    def test_zero_budget_has_no_partial_reservation(self):
        """Budget rejection rolls back the transaction so a later operation still works."""
        with tempfile.TemporaryDirectory() as directory:
            store = PilotStore(Path(directory) / "pilot.sqlite")
            try:
                store.begin("run", "corpus", "rules", "gemini", 0)
                with self.assertRaisesRegex(ValueError, "budget_exhausted"):
                    store.reserve("run", "paid", requested=True)
                self.assertEqual(store.conn.execute("SELECT count(*) FROM attempts").fetchone()[0], 0)
                self.assertTrue(store.reserve("run", "404", requested=False))
            finally:
                store.close()


class PreviewTests(unittest.TestCase):
    """Offline paths may validate files but never create results or call providers."""

    def test_evaluation_preview_has_no_network_or_database_writes(self):
        """Read a frozen synthetic case while all side-effect entry points are mocked."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corpus.json"
            context = EventContext(project_id="EGY-ES-0001", event_id="EGY-ES-0001-01", project={}, event={}, entities=[], source_articles=[])
            page = Page(url="https://www.ceec.net.cn/a", final_url="https://www.ceec.net.cn/a", checked_at="2026-09-06T00:00:00+00:00", problem="http_404")
            case = PilotCase(case_id="one", domain="main", split="development", context=context, candidate={"url": page.url}, page=page)
            corpus = PilotCorpus(selected_sha256="frozen", created_at=page.checked_at, event_count=1, cases=[case], events_without_candidates=[], source_hashes={"fixture": "hash"})
            path.write_text(corpus.model_dump_json(), encoding="utf-8")
            with patch("s02e_source_finder.pilot.private_path", side_effect=lambda value, **kwargs: value), patch("s02e_source_finder.pilot.assert_sources"), patch("s02e_source_finder.pilot.judge") as model, patch("s02e_source_finder.pilot.PilotStore") as db:
                result = evaluate(path, provider="deepseek", max_calls=0)
                model.assert_not_called()
                db.assert_not_called()
                self.assertEqual(result["readable"], 0)
                self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_preparation_rejects_unbounded_input(self):
        """The pilot cannot silently turn into a full-data fetch job."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selected.json"
            path.write_text(json.dumps({"events": [{}] * 31}), encoding="utf-8")
            with patch("s02e_source_finder.pilot.private_path", side_effect=lambda value, **kwargs: value):
                with self.assertRaisesRegex(ValueError, "exactly_30"):
                    prepare(path)


if __name__ == "__main__":
    unittest.main()
