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


def test_exclude_high_conf_autoexcludes(tmp_path):
    """verdict=exclude 且 conf>=1.0 + 注入 exclude_fn → 自动置 excluded，不入队。"""
    from review import apply, critic, queue
    rq = tmp_path / "review.db"; queue.init_queue(rq)
    calls = []
    def fake_exclude(canonical_id, reason): calls.append((canonical_id, reason))
    decisions = apply.route(
        canonical_id="IND-PV-0001", source_link="http://a",
        verdicts=[critic.Verdict(field="status", verdict="exclude", suggested_value=None,
                                 confidence=1.0, evidence="油气管道，非发电")],
        review_db=rq, update_field=lambda *a, **k: None, exclude_fn=fake_exclude)
    assert calls == [("IND-PV-0001", "油气管道，非发电")]
    assert decisions["auto_excluded"] == 1
    assert queue.pending(rq) == []


def test_exclude_low_conf_goes_to_queue(tmp_path):
    """verdict=exclude 且 conf<1.0 → 入队 exclude_suggested，不调 exclude_fn。"""
    from review import apply, critic, queue
    rq = tmp_path / "review.db"; queue.init_queue(rq)
    calls = []
    apply.route(
        canonical_id="IND-PV-0001", source_link="http://a",
        verdicts=[critic.Verdict(field="status", verdict="exclude", suggested_value=None,
                                 confidence=0.7, evidence="存疑")],
        review_db=rq, update_field=lambda *a, **k: None,
        exclude_fn=lambda *a, **k: calls.append(a))
    assert calls == []
    rows = queue.pending(rq)
    assert len(rows) == 1 and rows[0]["reason_type"] == "exclude_suggested"


def test_exclude_without_exclude_fn_falls_back_to_queue(tmp_path):
    """未注入 exclude_fn 时，即使高置信也只入队（防误改库）。"""
    from review import apply, critic, queue
    rq = tmp_path / "review.db"; queue.init_queue(rq)
    decisions = apply.route(
        canonical_id="X", source_link="http://a",
        verdicts=[critic.Verdict(field="status", verdict="exclude", confidence=1.0, evidence="e")],
        review_db=rq, update_field=lambda *a, **k: None)  # 无 exclude_fn
    assert decisions["auto_excluded"] == 0
    assert queue.pending(rq)[0]["reason_type"] == "exclude_suggested"
