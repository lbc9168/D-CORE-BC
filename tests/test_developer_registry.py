"""developer_registry.json 数据契约测试：结构、必填、别名全册唯一。"""
import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = _ROOT / "developer_registry.json"
# 与 s02b_entity_normalizer.developer_normalizer.DEV_SEP_RE 同字面：业主拆串分隔符
_DEV_SEP_RE = re.compile(r"[;；、，,/]")


def _load():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_registry_file_exists_and_wellformed():
    data = _load()
    assert set(data.keys()) == {"_meta", "companies"}
    assert data["_meta"]["version"] == 1


def test_companies_required_fields():
    for c in _load()["companies"]:
        assert c["canonical_cn"].strip(), c
        assert c["canonical_en"].strip(), c
        assert isinstance(c["aliases"], list), c
        assert isinstance(c["is_chinese"], bool), c
        assert "parent_cn" in c, c


def test_aliases_unique_across_registry():
    seen = {}
    for c in _load()["companies"]:
        # canonical_cn 本身视作自身 alias 参与唯一性
        for name in [c["canonical_cn"], *c["aliases"]]:
            key = name.strip()
            assert key not in seen, f"别名冲突: {key!r} 同时属于 {seen.get(key)} 与 {c['canonical_cn']}"
            seen[key] = c["canonical_cn"]


def test_aliases_no_separator_chars():
    """别名不得含拆串分隔符（含分隔符的括注/旧称类别名在 2B-3 永不可达，纯死数据）。"""
    for c in _load()["companies"]:
        for alias in c["aliases"]:
            assert not _DEV_SEP_RE.search(alias), (
                f"别名含分隔符不可达: {alias!r}（属于 {c['canonical_cn']}）"
            )
