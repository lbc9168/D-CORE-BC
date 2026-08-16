"""旧式实体模糊映射必须拒绝写库。"""
import sqlite3

import pytest

from s02b_entity_normalizer.entity_normalizer import _update_db


def _mk_db(path):
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE chinese_entities("
        "id INTEGER PRIMARY KEY, implementing_entity TEXT, subsidiary_name TEXT)"
    )
    c.execute(
        "INSERT INTO chinese_entities(implementing_entity, subsidiary_name) "
        "VALUES('中国电建','某子公司')"
    )
    c.execute(
        "INSERT INTO chinese_entities(implementing_entity, subsidiary_name) "
        "VALUES('某母公司','中国电建')"
    )
    c.commit()
    c.close()


def test_update_db_rejects_implementing_mapping(tmp_path):
    db = tmp_path / "t.db"
    _mk_db(str(db))

    with pytest.raises(RuntimeError, match="已禁用"):
        _update_db(str(db), {"中国电建": "中国电力建设集团"})

    c = sqlite3.connect(db)
    # 拒绝后两个字段都必须保持原值。
    assert c.execute(
        "SELECT implementing_entity FROM chinese_entities WHERE id=1"
    ).fetchone()[0] == "中国电建"
    assert c.execute(
        "SELECT subsidiary_name FROM chinese_entities WHERE id=2"
    ).fetchone()[0] == "中国电建"
    c.close()

def test_update_db_rejects_subsidiary_mapping(tmp_path):
    db = tmp_path / "t.db"
    _mk_db(str(db))

    with pytest.raises(RuntimeError, match="已禁用"):
        _update_db(str(db), {}, sub_mapping={"中国电建": "中国电力建设集团"})

    c = sqlite3.connect(db)
    assert c.execute(
        "SELECT implementing_entity FROM chinese_entities WHERE id=1"
    ).fetchone()[0] == "中国电建"
    assert c.execute(
        "SELECT subsidiary_name FROM chinese_entities WHERE id=2"
    ).fetchone()[0] == "中国电建"
    c.close()
