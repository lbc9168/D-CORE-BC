"""实体别名候选入口只读并仅导出需要审核的候选。"""

import json
import sqlite3

from s02b_entity_normalizer.entity_normalizer import generate_suggestions


def test_generate_suggestions_does_not_mutate_db(tmp_path):
    """生成建议前后实体值不变，登记册内双方均已解决的候选不导出。"""
    db = tmp_path / "entities.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE chinese_entities(implementing_entity TEXT)")
    conn.executemany(
        "INSERT INTO chinese_entities VALUES (?)",
        [("中国电建",), ("中国电力建设集团有限公司",), ("中国电建湖北公司",)],
    )
    conn.commit()
    before = conn.execute("SELECT implementing_entity FROM chinese_entities").fetchall()
    conn.close()

    hierarchy = tmp_path / "hierarchy.json"
    hierarchy.write_text(
        json.dumps(
            {
                "groups": [
                    {
                        "canonical_name_cn": "中国电力建设集团有限公司",
                        "aliases": ["中国电建"],
                        "subsidiaries": [],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "suggestions.csv"

    result = generate_suggestions(str(db), str(output), hierarchy)

    conn = sqlite3.connect(db)
    after = conn.execute("SELECT implementing_entity FROM chinese_entities").fetchall()
    conn.close()
    assert after == before
    assert result["suggestions"] >= 1
    text = output.read_text(encoding="utf-8-sig")
    assert "review_relationship" in text
    assert "中国电建湖北公司" in text
