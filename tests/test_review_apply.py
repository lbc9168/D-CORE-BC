"""apply：verdict 分流——字段高置信→改库；status挑战/存疑→入 review_queue 不改库。"""
from review import apply, critic, queue

def _v(field, verdict, sv, conf):
    return critic.Verdict(field=field, verdict=verdict, suggested_value=sv, confidence=conf, evidence="e")

def test_high_conf_field_fix_autocorrects(tmp_path):
    calls = []
    def fake_update(canonical_id, field, new_value, **kw): calls.append((canonical_id, field, new_value))
    rq = tmp_path / "review.db"; queue.init_queue(rq)
    decisions = apply.route(canonical_id="IND-PV-0001", source_link="http://a",
                            verdicts=[_v("category", "wrong", "风电", 0.9)],
                            review_db=rq, update_field=fake_update, conf_threshold=0.8)
    assert calls == [("IND-PV-0001", "category", "风电")]
    assert decisions["auto_corrected"] == 1 and queue.pending(rq) == []

def test_status_challenge_goes_to_queue_not_autodelete(tmp_path):
    rq = tmp_path / "review.db"; queue.init_queue(rq)
    called = []
    apply.route(canonical_id="IND-PV-0001", source_link="http://a",
                verdicts=[critic.Verdict(field="status", verdict="unsupported", suggested_value="irrelevant", confidence=0.95, evidence="油气")],
                review_db=rq, update_field=lambda *a, **k: called.append(a), conf_threshold=0.8)
    assert called == []  # 不自动改/删
    rows = queue.pending(rq)
    assert len(rows) == 1 and rows[0]["reason_type"] == "status_challenge"

def test_low_conf_field_goes_to_queue(tmp_path):
    rq = tmp_path / "review.db"; queue.init_queue(rq)
    apply.route(canonical_id="IND-PV-0001", source_link="http://a",
                verdicts=[_v("category", "wrong", "风电", 0.5)],
                review_db=rq, update_field=lambda *a, **k: None, conf_threshold=0.8)
    rows = queue.pending(rq)
    assert len(rows) == 1 and rows[0]["reason_type"] == "low_confidence_field"
