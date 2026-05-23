"""S5d：_update_db 只用 implementing 映射改 implementing 列，不得误改 subsidiary_name。"""
import sqlite3

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


def test_update_db_does_not_touch_subsidiary_with_impl_map(tmp_path):
    db = tmp_path / "t.db"
    _mk_db(str(db))

    n_impl, n_sub = _update_db(str(db), {"中国电建": "中国电力建设集团"})

    c = sqlite3.connect(db)
    # 第一行 implementing 应被规范化
    assert c.execute(
        "SELECT implementing_entity FROM chinese_entities WHERE id=1"
    ).fetchone()[0] == "中国电力建设集团"
    # 第二行 subsidiary 不应被 implementing 映射改写（bug 修复点）
    assert c.execute(
        "SELECT subsidiary_name FROM chinese_entities WHERE id=2"
    ).fetchone()[0] == "中国电建"
    c.close()

    assert n_impl == 1
    assert n_sub == 0


def test_update_db_applies_sub_mapping_only_when_provided(tmp_path):
    db = tmp_path / "t.db"
    _mk_db(str(db))

    # 显式传 sub_mapping 时才改 subsidiary_name
    n_impl, n_sub = _update_db(
        str(db),
        {},
        sub_mapping={"中国电建": "中国电力建设集团"},
    )

    c = sqlite3.connect(db)
    # implementing 不动
    assert c.execute(
        "SELECT implementing_entity FROM chinese_entities WHERE id=1"
    ).fetchone()[0] == "中国电建"
    # subsidiary 被规范化
    assert c.execute(
        "SELECT subsidiary_name FROM chinese_entities WHERE id=2"
    ).fetchone()[0] == "中国电力建设集团"
    c.close()

    assert n_impl == 0
    assert n_sub == 1
