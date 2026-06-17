"""review_queue：统一复核清单表，支持建表 + 插入 + 读 pending。"""
from review import queue


def test_queue_init_insert_pending(tmp_path):
    db = tmp_path / "review.db"
    queue.init_queue(db)
    queue.add_item(db, canonical_id="IND-PV-0001", source_link="http://a",
                   reason_type="unnamed_entity", field=None, current_value=None,
                   suggested_value=None, critic_confidence=None, evidence="S1 判 chinese_unnamed")
    rows = queue.pending(db)
    assert len(rows) == 1 and rows[0]["reason_type"] == "unnamed_entity"
    assert rows[0]["state"] == "pending"


def test_queue_accepts_exclude_suggested(tmp_path):
    """新 reason_type 'exclude_suggested' 合法可入队。"""
    from review import queue
    db = tmp_path / "review.db"; queue.init_queue(db)
    queue.add_item(db, canonical_id="1", source_link="http://a", reason_type="exclude_suggested",
                   field="status", current_value=None, suggested_value="exclude",
                   critic_confidence=0.7, evidence="存疑：可能是油气")
    rows = queue.pending(db)
    assert len(rows) == 1 and rows[0]["reason_type"] == "exclude_suggested"
