"""Offline development verification using seen evidence, never an independent blind test."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from s02e_source_finder import acceptance
from s02e_source_finder.contracts import Page
from s02e_source_finder.sample import ROOT, file_hash
from s02e_source_finder.structured import Extraction
from s02e_source_finder.structured_client import ExtractionError

DATA = ROOT / "s02e_source_finder/data"
OLD = DATA / "acceptance-v1"


class DevelopmentFlowTests(unittest.TestCase):
    def test_mixed_batch_real_evidence_projects_only_qualified_link_and_replays(self):
        """Successful search consumption survives transport, page and format failures."""
        inputs = json.loads((OLD / "inputs-v1.json").read_text(encoding="utf-8"))
        original = json.loads((OLD / "34763d715803fa57341ef6f3/report.json").read_text(encoding="utf-8"))
        good, wrong = original["results"][:2]
        event = good["event_id"]
        broken_url = "https://www.spic.com.cn/development-unavailable.html"
        malformed_url = "https://www.spic.com.cn/development-format.html"
        pages = {r["url"]: Page.model_validate(r["page"]) for r in (good, wrong)}
        pages[broken_url] = pages[good["url"]].model_copy(update={"url": broken_url, "final_url": broken_url, "problem": "network_temporarily_unavailable"})
        pages[malformed_url] = pages[good["url"]].model_copy(update={"url": malformed_url, "final_url": malformed_url, "title": "本地格式失败模拟"})
        # Correct article goes last, so each earlier candidate failure must be isolated.
        order = [broken_url, malformed_url, wrong["url"], good["url"]]
        protected = [Path(p) for p in inputs["source_hashes"]]
        protected += sorted((ROOT / "s04_web_visualization/public/data").rglob("*.*"))
        protected += [ROOT / "s04_web_visualization/src/dev/reference-sample.json"]
        before = {str(p): file_hash(p) for p in protected if p.is_file()}
        def search(request):
            if request.get("event_id") == event:
                return {"grounding": {"groundingChunks": [{"web": {"uri": url}} for url in order]}}
            return {"error": "ProxyError"} if request.get("event_id") == list(inputs["contexts"])[1] else {"grounding": {"groundingChunks": []}}
        def extract(payload, provider):
            title = payload["candidate_title"]
            if title == "本地格式失败模拟":
                raise ExtractionError("invalid_structured_output", {"provider": provider, "raw_response": "{malformed-local-fixture"})
            row = good if title == good["title"] else wrong
            return Extraction.model_validate(row["extraction"]), {"fixture": "existing_structured_response", "external_calls": 0}
        with tempfile.TemporaryDirectory(prefix="development-flow-", dir=DATA) as directory:
            # Replay uses frozen contexts, not today's mutable production databases.
            # Keep the drift gate active against the actual local test corpus.
            corpus = Path(directory) / "frozen-contexts.json"
            corpus.write_text(json.dumps(inputs["contexts"], ensure_ascii=False), encoding="utf-8")
            inputs["source_hashes"] = {str(corpus): file_hash(corpus)}
            inputs.update(seeds=[], max_search_calls=20, max_model_calls=4)
            path = Path(directory) / "inputs.json"
            path.write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
            with patch("requests.sessions.Session.request", side_effect=AssertionError("external network forbidden")) as network, \
                 patch.object(acceptance.grounding, "search_request", side_effect=lambda context, card, round_number: {"event_id": context.event_id}), \
                 patch.object(acceptance.grounding, "search", side_effect=search) as discovery, \
                 patch.object(acceptance, "resolve_citation", side_effect=lambda url, fetcher: url), \
                 patch.object(acceptance.PublicFetcher, "fetch", side_effect=lambda url: pages[url]), \
                 patch("s02e_source_finder.source_loop.extract", side_effect=extract) as extraction:
                result = acceptance.run(path, OLD / "publisher-registry-v1.json", execute=True)
                report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
                by_url = {r["url"]: r for r in report["results"]}
                self.assertEqual(set(by_url), set(order))
                self.assertEqual(by_url[good["url"]]["decision"], "event_reference")
                self.assertEqual(by_url[wrong["url"]]["decision"], "rejected")
                self.assertEqual(by_url[broken_url]["decision"], "pending")
                self.assertEqual(by_url[malformed_url]["decision"], "pending")
                refs = next(r for r in report["events_result"] if r["event_id"] == event)["external_references"]
                self.assertEqual([r["url"] for r in refs], [good["url"]])
                self.assertTrue(report["complete"])
                counts = discovery.call_count, extraction.call_count
                replay = acceptance.run(path, OLD / "publisher-registry-v1.json", execute=True)
                self.assertEqual((discovery.call_count, extraction.call_count), counts)
                network.assert_not_called()
            after = {str(p): file_hash(p) for p in protected if p.is_file()}
            self.assertEqual(before, after)
            output = DATA / "development-validation-v1"
            output.mkdir(exist_ok=True)
            (output / "mixed-batch-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            sample = json.loads((ROOT / "s04_web_visualization/src/dev/reference-sample.json").read_text(encoding="utf-8"))
            sample.update(run_id="development-validation-v1", results=report["results"])
            sample["project"]["external_references"] = refs
            (output / "reference-sample.json").write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
            evidence = {"classification": "development verification; 13 candidates already seen", "external_api_calls": 0, "mixed_batch_decisions": {url: row["decision"] for url, row in by_url.items()}, "qualified_urls": [r["url"] for r in refs], "replay_no_new_calls": True, "complete": report["complete"], "before": before, "after": after, "unchanged": before == after}
            (output / "verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
