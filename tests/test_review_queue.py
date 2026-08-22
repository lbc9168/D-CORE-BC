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


def test_queue_accepts_entity_identity(tmp_path):
    """未命中中企登记表的项目可进入 entity_identity 人工复核队列。"""
    db = tmp_path / "review.db"
    queue.init_queue(db)
    queue.add_item(
        db,
        canonical_id="123",
        source_link="http://example.com/entity",
        reason_type="entity_identity",
        field="implementing_entity",
        current_value="某未登记企业",
        suggested_value=None,
        critic_confidence=None,
        evidence="中企名称未命中受控登记表",
    )
    rows = queue.pending(db)
    assert len(rows) == 1 and rows[0]["reason_type"] == "entity_identity"


def test_queue_accepts_entity_relation(tmp_path):
    """身份已知但项目—企业—角色证据不足时可进入 entity_relation 复核队列。"""
    db = tmp_path / "review.db"
    queue.init_queue(db)
    queue.add_item(
        db,
        canonical_id="124",
        source_link="http://example.com/relation",
        reason_type="entity_relation",
        field="involvement_type",
        current_value="投融资+EPC",
        suggested_value="Unknown",
        critic_confidence=None,
        evidence="没有通过目标项目—具体企业—参与角色证据绑定",
    )
    rows = queue.pending(db)
    assert len(rows) == 1 and rows[0]["reason_type"] == "entity_relation"
