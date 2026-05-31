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
