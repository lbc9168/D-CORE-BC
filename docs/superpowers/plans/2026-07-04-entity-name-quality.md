# 实体名质量专项 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立公司名缓存卫生工具、s01b 翻译入口守卫（ASCII 直通 + developer 拆串 + 垃圾键拒写）、s02b developer 归一化、主库/projects.db 英文名确定性回填——四个 CLI/改动全部 dry-run 默认、apply 前备份，为后续"分层核实 campaign"提供承接机制。

**Architecture:** ① `cache_fix`（s01b tools）：规则扫描垃圾键/值 + corrections CSV upsert，campaign 产出的核实结果将来也走同一 corrections 通道；② s01b `_resolve_entity_en` 加 ASCII 直通与垃圾键拒写，新增 `_resolve_developer_en` 拆串翻译，两处 developer 调用点接入；③ s02b 新模块 `developer_normalizer.py` 复用 entity_normalizer 的三信号+LLM+Union-Find 归一 `project_events.project_developer`（部件级）；④ s02b 新模块 `en_backfill.py` 用修复后缓存确定性重算主库与 projects.db 的三对英文名列（miss 不动只报告，成为 campaign 候选清单）。**不重建 s02a**——一切原地更新。

**Tech Stack:** Python 3.10-3.13, pytest（uv run）, sqlite3, rapidfuzz（s02b 已有），无新第三方依赖。

## Global Constraints

- **工作目录**：`E:\Projects\BRI_RE_Database_v2\.claude\worktrees\entity-name-quality`（分支 `feat/entity-name-quality`，基于 main `c326d23`）。所有编辑/测试/提交都在此 worktree。
- **测试命令**：s01b → `cd <worktree>\s01b_project_extractor && uv run pytest tests/<file> -v`；s02b → `cd <worktree>\s02b_entity_normalizer && uv run pytest tests/<file> -v`（s02b 的 pytest 配置由 Task 3 补齐；Task 3 之前不要在 s02b 跑 pytest）。
- **绝对安全规则**：任何任务不得对真实缓存（`supporting_docs/company_name_cache.json` 主工作区副本）或真实数据库执行 **--apply**；实现任务（Task 1-4）只允许 tmp_path 测试数据。真实数据的 dry-run/preview 只在 Task 5 由收尾步骤执行（只读 + 产报告）。
- **导出口径既定为「缩写为主」**：`_resolve_entity_en` 的 en_abbr-or-en 返回逻辑**不改**；本计划一切英文名解析沿用「en_abbr 优先、否则 en」。
- **CLAUDE.md 编码规则**：禁 `except: pass`（指定类型 + `logging.error` 上下文）；批量写库事务 + `executemany`；`print()` 仅限 CLI 顶层人机提示；运行时输出走 logging。
- **Windows GBK**：CLI 顶部按既有模式 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`；不往 console print 公司名（写 CSV/日志）。
- **提交风格**：中文一句话 `feat(s01b)/feat(s02b)/docs(...)`，每 Task 至少一个 commit。
- **精准修改**：`project_extractor_tool.py` 只动本计划点名的函数/调用点；`entity_normalizer.py` **一行不改**（developer 归一是新文件，只 import 复用）。
- **分层核实 campaign 不在本计划内**：campaign（web 核实 ~115 家高频实体）由控制器在本计划合并后另行执行，产出 corrections CSV 喂给 Task 1 的 `cache_fix --corrections` 通道。

**背景材料**（供参考）：缓存现状 4402 条（40 verified / 4362 AI 未核）、178 条含分隔符垃圾键、5 条垃圾值、4 个已知张冠李戴错译；`project_events.project_developer` 3492 个不重复值、866 条多公司串；主库 implementing_entity 已被 s02b 收敛到 701 家。既有先例：`scripts/oneoff/output/_developer_en_backfill.py`（cache-first + ascii 直通 + .bak + 事务）。

---

### Task 1: 缓存卫生工具 `cache_fix`

**Files:**
- Create: `s01b_project_extractor/src/s01b_project_extractor/tools/cache_hygiene.py`
- Create: `supporting_docs/cache_corrections/2026-07-04-hard-errors.csv`
- Modify: `s01b_project_extractor/pyproject.toml`（`[project.scripts]` 加一行）
- Test: `s01b_project_extractor/tests/test_cache_hygiene.py`

**Interfaces:**
- Produces（Task 5 与未来 campaign 依赖）:
  - `scan_cache(cache: dict) -> list[dict]` — 垃圾条目清单，每项 `{"key", "rule", "en", "note"}`
  - `load_corrections(csv_path: str) -> list[dict]` — corrections 行 `{"cn","en","en_abbr","verified","source"}`
  - `plan_corrections(cache: dict, corrections: list[dict]) -> list[dict]` — would-change 行 `{"cn","old_en","new_en","new_abbr","is_new"}`
  - `apply_changes(cache_path: str, remove_keys: list[str], corrections: list[dict]) -> dict` — 备份后写回，返回新 cache
  - CLI 入口 `cache_fix`

- [ ] **Step 1: 写失败测试**

创建 `s01b_project_extractor/tests/test_cache_hygiene.py`：

```python
"""缓存卫生：垃圾键/值规则扫描、corrections upsert、备份写回。"""
import json
from pathlib import Path
import pytest
from s01b_project_extractor.tools.cache_hygiene import (
    apply_changes, load_corrections, plan_corrections, scan_cache,
)


def _cache():
    return {
        "中国电建": {"en": "Power Construction Corporation of China (PowerChina)",
                     "en_abbr": "PowerChina", "verified": True, "source": "official"},
        "阳光电源股份有限公司": {"en": "Sungrow Power Supply Co., Ltd.", "en_abbr": None,
                                 "verified": False, "source": "ai"},
        "中国能源建设集团党委副书记、总经理陈观福": {"en": "CEEC", "en_abbr": None,
                                                     "verified": False, "source": "ai"},   # R1 分隔符+R2 头衔
        "N/A": {"en": "N/A", "en_abbr": None, "verified": False, "source": "ai"},           # R3 占位
        "四家全球知名公司": {"en": "**1. 腾讯控股有限公司 (Tencent)**\n**2. ...", "en_abbr": None,
                             "verified": False, "source": "ai"},                            # R3 泛指 + R6 噪声值
        "未指明": {"en": "未指明", "en_abbr": None, "verified": False, "source": "ai"},      # R3 + R5 值含中文
        "这是一个超过三十个字符的假想公司名称用来测试超长键规则触发不触发": {                 # R4 超长键
            "en": "X Co", "en_abbr": None, "verified": False, "source": "ai"},
        "中国企业与挪威Scatec公司": {"en": "[中国企业] & Scatec ASA", "en_abbr": None,
                                     "verified": False, "source": "ai"},                    # R5 值含中文
    }


def test_scan_flags_junk_rules():
    findings = scan_cache(_cache())
    flagged = {f["key"] for f in findings}
    assert "中国能源建设集团党委副书记、总经理陈观福" in flagged
    assert "N/A" in flagged
    assert "四家全球知名公司" in flagged
    assert "未指明" in flagged
    assert "这是一个超过三十个字符的假想公司名称用来测试超长键规则触发不触发" in flagged
    assert "中国企业与挪威Scatec公司" in flagged


def test_scan_never_flags_clean_or_verified():
    findings = scan_cache(_cache())
    flagged = {f["key"] for f in findings}
    assert "中国电建" not in flagged, "verified 条目绝不列入清理"
    assert "阳光电源股份有限公司" not in flagged, "干净 ai 条目不误报"


def test_corrections_roundtrip(tmp_path):
    csv_path = tmp_path / "corr.csv"
    csv_path.write_text(
        'cn,en,en_abbr,verified,source\n'
        '上能电气,"Sineng Electric Co., Ltd.",Sineng Electric,false,correction-20260704\n'
        '中国电建,"Power Construction Corporation of China (PowerChina)",PowerChina,true,official\n',
        encoding="utf-8-sig")
    corr = load_corrections(str(csv_path))
    assert corr[0]["cn"] == "上能电气" and corr[0]["en"].endswith("Ltd.")
    plan = plan_corrections(_cache(), corr)
    # 上能电气是新键 → is_new；中国电建与现值一致 → 不在 would-change 里
    assert any(p["cn"] == "上能电气" and p["is_new"] for p in plan)
    assert not any(p["cn"] == "中国电建" for p in plan)


def test_apply_removes_upserts_and_backs_up(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps({"_comment": "meta", **_cache()}, ensure_ascii=False),
                          encoding="utf-8")
    corr = [{"cn": "上能电气", "en": "Sineng Electric Co., Ltd.",
             "en_abbr": "Sineng Electric", "verified": False, "source": "correction-20260704"}]
    new_cache = apply_changes(str(cache_path), remove_keys=["N/A", "未指明"], corrections=corr)
    assert "N/A" not in new_cache and "未指明" not in new_cache
    assert new_cache["上能电气"]["en_abbr"] == "Sineng Electric"
    on_disk = json.loads(cache_path.read_text(encoding="utf-8"))
    assert on_disk["_comment"] == "meta", "_ 开头元字段必须保留"
    assert "上能电气" in on_disk and "N/A" not in on_disk
    assert list(tmp_path.glob("cache.json.bak.*")), "apply 前必须备份"


def test_apply_never_removes_verified(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps(_cache(), ensure_ascii=False), encoding="utf-8")
    new_cache = apply_changes(str(cache_path), remove_keys=["中国电建"], corrections=[])
    assert "中国电建" in new_cache, "verified 键即使被点名也不删（防误操作）"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd E:\Projects\BRI_RE_Database_v2\.claude\worktrees\entity-name-quality\s01b_project_extractor && uv run pytest tests/test_cache_hygiene.py -v`
Expected: FAIL — `ModuleNotFoundError: cache_hygiene`

- [ ] **Step 3: 实现模块**

创建 `s01b_project_extractor/src/s01b_project_extractor/tools/cache_hygiene.py`：

```python
"""缓存卫生工具：规则扫描 company_name_cache.json 垃圾键/值 + corrections CSV upsert。

dry-run 默认（产报告 CSV 不写缓存）；--apply 先备份 .bak 再写回。
未来"分层核实 campaign"的核实结果也走 --corrections 同一通道写回。
用法：uv run cache_fix [--cache PATH] [--corrections CSV] [--findings CSV] [--apply] [--out CSV]
"""
import argparse
import csv
import json
import logging
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "cp950"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger(__name__)

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
DEFAULT_CACHE = str(_ROOT / "supporting_docs" / "company_name_cache.json")

_SEP_RE = re.compile(r"[;；、，,/]")
_CJK_RE = re.compile(r"[一-鿿]")
_TITLE_WORDS = ["董事长", "总经理", "书记", "副总裁", "党委", "首席"]
_GENERIC_WORDS = ["知名", "数家", "多家", "该公司", "未指明", "全球公司"]
_PLACEHOLDER_KEYS = {"n/a", "na", "-", "unknown", "未知", "不详"}
_MAX_KEY_LEN = 30
_MAX_EN_LEN = 120


def scan_cache(cache: dict) -> list[dict]:
    """规则扫描垃圾条目：返回 [{key, rule, en, note}]；verified=true 条目绝不列入。"""
    findings: list[dict] = []

    def _add(key, rule, en, note):
        findings.append({"key": key, "rule": rule, "en": en, "note": note})

    for key, val in cache.items():
        if key.startswith("_"):
            continue
        entry = val if isinstance(val, dict) else {"en": str(val)}
        if entry.get("verified"):
            continue  # 人工核实条目豁免
        en = str(entry.get("en") or "")
        if _SEP_RE.search(key):
            _add(key, "R1_key_separator", en, "键含分隔符（多公司串/头衔串）")
        elif any(w in key for w in _TITLE_WORDS):
            _add(key, "R2_key_person_title", en, "键含人名头衔词")
        elif key.strip().lower() in _PLACEHOLDER_KEYS or any(w in key for w in _GENERIC_WORDS):
            _add(key, "R3_key_placeholder", en, "键为占位/泛指短语")
        elif len(key) > _MAX_KEY_LEN:
            _add(key, "R4_key_too_long", en, f"键长 {len(key)} > {_MAX_KEY_LEN}")
        elif _CJK_RE.search(en):
            _add(key, "R5_value_cjk", en, "英文值含中文（翻译失败被固化）")
        elif "\n" in en or len(en) > _MAX_EN_LEN or en.startswith("[") or not en.strip():
            _add(key, "R6_value_noise", en, "英文值含换行/超长/括注噪声/为空")
    return findings


def load_corrections(csv_path: str) -> list[dict]:
    """读 corrections CSV（列 cn,en,en_abbr,verified,source）→ 行 dict 列表；空 cn 行跳过。"""
    rows: list[dict] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            cn = str(r.get("cn") or "").strip()
            if not cn:
                continue
            rows.append({
                "cn": cn,
                "en": str(r.get("en") or "").strip(),
                "en_abbr": str(r.get("en_abbr") or "").strip() or None,
                "verified": str(r.get("verified") or "").strip().lower() == "true",
                "source": str(r.get("source") or "correction").strip(),
            })
    return rows


def plan_corrections(cache: dict, corrections: list[dict]) -> list[dict]:
    """corrections 与现缓存比对 → would-change 行（新键或 en/en_abbr 有变化才列出）。"""
    plan: list[dict] = []
    for c in corrections:
        cur = cache.get(c["cn"])
        cur = cur if isinstance(cur, dict) else ({"en": str(cur)} if cur else None)
        if cur is None:
            plan.append({"cn": c["cn"], "old_en": "", "new_en": c["en"],
                         "new_abbr": c["en_abbr"] or "", "is_new": True})
        elif (cur.get("en") or "") != c["en"] or (cur.get("en_abbr") or None) != c["en_abbr"]:
            plan.append({"cn": c["cn"], "old_en": str(cur.get("en") or ""), "new_en": c["en"],
                         "new_abbr": c["en_abbr"] or "", "is_new": False})
    return plan


def apply_changes(cache_path: str, remove_keys: list[str], corrections: list[dict]) -> dict:
    """备份 .bak 后写回：删除 remove_keys（verified 键豁免不删）+ upsert corrections；保留 _ 元字段。"""
    raw = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    bak = cache_path + datetime.now().strftime(".bak.%Y%m%d_%H%M%S")
    shutil.copy2(cache_path, bak)
    logger.info("缓存已备份 %s", bak)

    removed = 0
    for key in remove_keys:
        entry = raw.get(key)
        if entry is None:
            continue
        if isinstance(entry, dict) and entry.get("verified"):
            logger.error("拒绝删除 verified 键（防误操作）: %s", key)
            continue
        del raw[key]
        removed += 1
    for c in corrections:
        raw[c["cn"]] = {"en": c["en"], "en_abbr": c["en_abbr"],
                        "verified": c["verified"], "source": c["source"]}
    Path(cache_path).write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("缓存写回：删除 %d 键、upsert %d 条", removed, len(corrections))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def main() -> None:
    """CLI：默认 dry-run（扫描+corrections 计划 → 报告 CSV）；--apply 时按 --findings 清单删除 + corrections 写回。"""
    parser = argparse.ArgumentParser(description="company_name_cache.json 卫生工具")
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--corrections", default=None, help="corrections CSV（cn,en,en_abbr,verified,source）")
    parser.add_argument("--findings", default=None, help="apply 时要删除的键清单 CSV（复看后的 scan 报告）")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--out", default=None, help="dry-run 报告 CSV 路径")
    args = parser.parse_args()

    if not Path(args.cache).exists():
        parser.error(f"缓存文件不存在: {args.cache}")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = args.out or str(Path(args.cache).parent / f"cache_hygiene_{ts}.csv")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(str(Path(out_csv).with_suffix(".log")), encoding="utf-8"),
                  logging.StreamHandler()],
    )

    raw = json.loads(Path(args.cache).read_text(encoding="utf-8"))
    cache = {k: v for k, v in raw.items() if not k.startswith("_")}
    corrections = load_corrections(args.corrections) if args.corrections else []

    if not args.apply:
        findings = scan_cache(cache)
        plan = plan_corrections(cache, corrections)
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["kind", "key_or_cn", "rule_or_isnew", "current_en", "new_en", "note"])
            for x in findings:
                w.writerow(["remove_candidate", x["key"], x["rule"], x["en"], "", x["note"]])
            for p in plan:
                w.writerow(["correction", p["cn"], "new" if p["is_new"] else "update",
                            p["old_en"], p["new_en"], p["new_abbr"]])
        print(f">>> dry-run：清理候选 {len(findings)} 键、corrections 变更 {len(plan)} 条，报告: {out_csv}")
        return

    remove_keys: list[str] = []
    if args.findings:
        with open(args.findings, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if str(r.get("kind") or "") == "remove_candidate":
                    remove_keys.append(str(r.get("key_or_cn") or ""))
    apply_changes(args.cache, remove_keys, corrections)
    print(f">>> 已 apply：删除 {len(remove_keys)} 键（verified 豁免除外）、upsert {len(corrections)} 条")


if __name__ == "__main__":
    main()
```

创建种子 corrections `supporting_docs/cache_corrections/2026-07-04-hard-errors.csv`（4 个已知张冠李戴错译，verified=false 留待 campaign 确认，apply 前用户复看）：

```csv
cn,en,en_abbr,verified,source
上能电气,"Sineng Electric Co., Ltd.",Sineng Electric,false,correction-20260704
泰豪国际,Tellhow International Engineering Co.,Tellhow,false,correction-20260704
中国水电,Sinohydro Corporation,Sinohydro,false,correction-20260704
中地海外,"CGCOC Group Co., Ltd.",CGCOC,false,correction-20260704
```

`s01b_project_extractor/pyproject.toml` `[project.scripts]` 追加：

```toml
cache_fix              = "s01b_project_extractor.tools.cache_hygiene:main"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_cache_hygiene.py -v`
Expected: 5 个 PASS。另跑 smoke：`uv run cache_fix --help` 打印 usage 退出 0。

- [ ] **Step 5: Commit**

```bash
cd E:\Projects\BRI_RE_Database_v2\.claude\worktrees\entity-name-quality
git add s01b_project_extractor/src/s01b_project_extractor/tools/cache_hygiene.py s01b_project_extractor/tests/test_cache_hygiene.py s01b_project_extractor/pyproject.toml supporting_docs/cache_corrections/2026-07-04-hard-errors.csv
git commit -m "feat(s01b): cache_fix 缓存卫生 CLI——垃圾键/值规则扫描 + corrections 通道（dry-run 默认）"
```

---

### Task 2: s01b 翻译入口守卫 + developer 拆串

**Files:**
- Modify: `s01b_project_extractor/src/s01b_project_extractor/tools/project_extractor_tool.py`（`_resolve_entity_en` 函数、其后新增 `_resolve_developer_en`、两处 developer 调用点、文件顶部 import）
- Test: `s01b_project_extractor/tests/test_resolve_developer_en.py`

**Interfaces:**
- Consumes: 既有 `_resolve_entity_en(name_cn, client)`、`_load_name_cache()`、`_save_name_cache(cache)`。
- Produces: `_resolve_developer_en(dev_str, client) -> str`（拆串翻译，"; " 拼回）；`_resolve_entity_en` 新行为——ASCII 直通、多公司串/超长键不写缓存。

- [ ] **Step 1: 写失败测试**

创建 `s01b_project_extractor/tests/test_resolve_developer_en.py`：

```python
"""s01b 翻译入口：ASCII 直通、developer 拆串、垃圾键拒写缓存。"""
import pandas as pd
import sqlite3
from s01b_project_extractor.tools import project_extractor_tool as pet


class _FakeResp:
    text = "Whatever Co"


class _FakeModels:
    def __init__(self):
        self.calls = 0
    def generate_content(self, **kwargs):
        self.calls += 1
        return _FakeResp()


class _FakeClient:
    def __init__(self):
        self.models = _FakeModels()


def test_ascii_passthrough_no_cache_no_client(monkeypatch):
    """无中文字符 → 原样返回：不查缓存、不调翻译、不写缓存。client=None 证明无 API 调用。"""
    saved = []
    monkeypatch.setattr(pet, "_load_name_cache", lambda: {})
    monkeypatch.setattr(pet, "_save_name_cache", lambda c: saved.append(1))
    assert pet._resolve_entity_en("ACWA Power", None) == "ACWA Power"
    assert saved == []


def test_developer_split_and_rejoin(monkeypatch):
    """多公司串拆开逐家解析，以 '; ' 拼回（缓存命中，零 API 调用）。"""
    cache = {"中国电建": {"en": "PowerChina", "en_abbr": None},
             "阳光电源": {"en": "Sungrow", "en_abbr": None}}
    monkeypatch.setattr(pet, "_load_name_cache", lambda: cache)
    assert pet._resolve_developer_en("中国电建;阳光电源", None) == "PowerChina; Sungrow"
    assert pet._resolve_developer_en("中国电建、阳光电源", None) == "PowerChina; Sungrow"


def test_developer_single_name_delegates(monkeypatch):
    cache = {"中国电建": {"en": "PowerChina", "en_abbr": None}}
    monkeypatch.setattr(pet, "_load_name_cache", lambda: cache)
    assert pet._resolve_developer_en("中国电建", None) == "PowerChina"
    assert pet._resolve_developer_en("", None) == ""


def test_separator_string_never_cached(monkeypatch):
    """含分隔符的串（上游漏拆）翻译结果照常返回，但不得写入缓存。"""
    saved = []
    monkeypatch.setattr(pet, "_load_name_cache", lambda: {})
    monkeypatch.setattr(pet, "_save_name_cache", lambda c: saved.append(dict(c)))
    client = _FakeClient()
    out = pet._resolve_entity_en("中国电建;阳光电源", client)
    assert out == "Whatever Co"
    assert saved == [], "多公司串不得固化进缓存"


def test_toolong_key_never_cached(monkeypatch):
    saved = []
    monkeypatch.setattr(pet, "_load_name_cache", lambda: {})
    monkeypatch.setattr(pet, "_save_name_cache", lambda c: saved.append(dict(c)))
    long_name = "中" * 31
    out = pet._resolve_entity_en(long_name, _FakeClient())
    assert out == "Whatever Co"
    assert saved == []


def test_normal_name_still_cached(monkeypatch):
    """正常中文公司名照旧写缓存（守卫不误伤主路径）。"""
    saved = []
    monkeypatch.setattr(pet, "_load_name_cache", lambda: {})
    monkeypatch.setattr(pet, "_save_name_cache", lambda c: saved.append(dict(c)))
    out = pet._resolve_entity_en("测试能源公司", _FakeClient())
    assert out == "Whatever Co"
    assert len(saved) == 1 and "测试能源公司" in saved[0]


def test_write_path_uses_developer_split(tmp_path, monkeypatch):
    """集成：_process_df 写库后 project_developer_en 是拆串翻译的 '; ' 拼接。"""
    cache = {"中国电建": {"en": "PowerChina", "en_abbr": None},
             "阳光电源": {"en": "Sungrow", "en_abbr": None},
             "某矿业公司": {"en": "Some Mining Co", "en_abbr": None}}
    monkeypatch.setattr(pet, "_load_name_cache", lambda: cache)
    monkeypatch.setattr(pet, "_resolve_is_central_soe", lambda *a, **k: 0)

    def fake_extract_one(title, content, date, *, client, model="x"):
        return {"analysis": {"project_subject": "s", "reasoning": "r"},
                "status": "valid_project",
                "projects": [{
                    "project_summary": "", "in_scope": True, "out_of_scope_reason": "",
                    "common_info": {"Project Name": "肯尼亚50MW光伏项目", "Category": "光伏",
                                    "Capacity (MW)": "50", "Capacity Details": "",
                                    "Country": "肯尼亚", "Project Stage": "在建",
                                    "Project Developer": "中国电建;阳光电源"},
                    "chinese_entities": [{"Implementing Chinese Entity": "中国电建",
                                          "Type of involvement of Chinese Entities": "EPC总承包"}],
                }]}
    monkeypatch.setattr(pet, "extract_one", fake_extract_one)

    df = pd.DataFrame([{"原文链接": "http://x1", "文章标题": "标题",
                        "正文内容": "正文含发电 50MW 内容", "发布日期": "2026-01-01"}])
    tool = pet.ProjectExtractorTool()
    tool._process_df(df, batch_id="b1", ts="20260704_t", client=None,
                     country_db={}, projects_db_path=str(tmp_path / "p.db"),
                     review_db_path=str(tmp_path / "r.db"))
    conn = sqlite3.connect(str(tmp_path / "p.db"))
    dev_en = conn.execute("SELECT project_developer_en FROM extracted_projects").fetchone()[0]
    conn.close()
    assert dev_en == "PowerChina; Sungrow"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_resolve_developer_en.py -v`
Expected: `_resolve_developer_en` AttributeError + ascii/拒写用例 FAIL（现行为会查缓存/写缓存/整串翻译）。

- [ ] **Step 3: 实现**

`project_extractor_tool.py` 四处修改：

(a) 文件顶部 import 区（`import json` 附近）加 `import re`（若已有则跳过）。

(b) 在 `_normalize_implementing_entity` 函数之前加模块常量：

```python
# developer 多公司串分隔符 与 中文字符检测（翻译入口守卫用）
_DEV_SEP_RE = re.compile(r"[;；、，,/]+")
_CJK_CHAR_RE = re.compile(r"[一-鿿]")
_CACHE_KEY_MAX_LEN = 30
```

(c) `_resolve_entity_en` 两处：

在 `if not name_cn or name_cn in ("Unknown", "未知", "不详"):` 块之后插入：

```python
    # ── ASCII 直通：无中文字符（英文名/代号）原样返回——不查缓存、不翻译、不写缓存 ──
    if not _CJK_CHAR_RE.search(str(name_cn)):
        return str(name_cn)
```

把写缓存条件（`if success:` 一行，位于 double-check locking 注释块）改为：

```python
    # 垃圾键守卫：多公司串（应在上游拆串）与超长键不固化进缓存；翻译结果照常返回
    if success and not _DEV_SEP_RE.search(name_cn) and len(name_cn) <= _CACHE_KEY_MAX_LEN:
```

(d) 在 `_resolve_entity_en` 函数之后新增：

```python
def _resolve_developer_en(dev_str, client) -> str:
    """业主字段可含多家公司（;／、等分隔）：拆串逐家走 _resolve_entity_en 再以 '; ' 拼回，杜绝整串垃圾缓存键。"""
    s = str(dev_str or "").strip()
    if not s:
        return ""
    parts = [p.strip() for p in _DEV_SEP_RE.split(s) if p.strip()]
    if len(parts) <= 1:
        return _resolve_entity_en(s, client)
    return "; ".join(_resolve_entity_en(p, client) for p in parts)
```

(e) 两处调用点（valid_project 分支与 chinese_unnamed 分支的 base dict 各一处，锚点是注释 `# 业主英文：与 implementing_entity_en 同走 _resolve_entity_en（缓存+Gemini），future imports 自动带上`）：

```python
                        "Project Developer (EN)": _resolve_developer_en(common.get("Project Developer", ""), client),
```

同时把两处锚点注释更新为：`# 业主英文：拆串逐家走 _resolve_entity_en（缓存+Gemini）后 '; ' 拼回`。

- [ ] **Step 4: 跑测试确认通过（含回归）**

Run: `uv run pytest tests/test_resolve_developer_en.py tests/test_resolve_entity_en_nocache.py tests/test_write_deterministic_guard.py tests/test_write_exclude.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 全量回归 + Commit**

Run: `uv run pytest`（s01b 全套件，基线 284 + 本任务新增，全绿）

```bash
git add s01b_project_extractor/src/s01b_project_extractor/tools/project_extractor_tool.py s01b_project_extractor/tests/test_resolve_developer_en.py
git commit -m "feat(s01b): 翻译入口守卫——ASCII 直通、developer 拆串翻译、多公司串/超长键拒写缓存"
```

---

### Task 3: s02b 测试设施 + developer 归一化

**Files:**
- Modify: `s02b_entity_normalizer/pyproject.toml`（补 pytest dev 依赖与配置 + scripts 一行）
- Create: `s02b_entity_normalizer/src/s02b_entity_normalizer/developer_normalizer.py`
- Test: `s02b_entity_normalizer/tests/test_developer_normalizer.py`

**Interfaces:**
- Consumes: `entity_normalizer` 的 `DEFAULT_DB, BATCH_SIZE, _find_candidates, _llm_confirm_batch, _build_mapping`（只 import，不改 entity_normalizer.py）。
- Produces:
  - `split_developer(value) -> list[str]`
  - `unify_junk(value) -> str | None`（整值垃圾占位 → "Unknown"，否则 None）
  - `rebuild(value: str, part_mapping: dict[str, str]) -> str`（部件无变化则原样返回）
  - `plan_value_changes(conn, part_mapping: dict[str, str]) -> list[tuple[str, str]]`（(old, new) 对）
  - `apply_value_changes(db_path: str, pairs: list[tuple[str, str]]) -> int`（.bak + 事务 executemany，返回行数）
  - CLI 入口 `normalize_developers`

- [ ] **Step 1: 补 s02b 测试设施**

`s02b_entity_normalizer/pyproject.toml` 追加（对齐 s01b 模式）：

```toml
[dependency-groups]
dev = [
    "pytest>=8.0.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src", ".."]
addopts = "-v --tb=short"
```

`[project.scripts]` 追加：

```toml
normalize_developers   = "s02b_entity_normalizer.developer_normalizer:run"
```

验证既有测试可跑：`cd E:\Projects\BRI_RE_Database_v2\.claude\worktrees\entity-name-quality\s02b_entity_normalizer && uv run pytest`
Expected: `tests/test_entity_update_db.py` 2 个 PASS（先确认底座，再动工）。

- [ ] **Step 2: 写失败测试**

创建 `s02b_entity_normalizer/tests/test_developer_normalizer.py`：

```python
"""developer 归一：拆串/垃圾值统一/保守重建/计划与落库（LLM 环节不在单测内，注入映射测确定性部分）。"""
import sqlite3
from pathlib import Path
from s02b_entity_normalizer.developer_normalizer import (
    apply_value_changes, plan_value_changes, rebuild, split_developer, unify_junk,
)


def test_split_developer():
    assert split_developer("A公司;B公司、C公司") == ["A公司", "B公司", "C公司"]
    assert split_developer(" 单一公司 ") == ["单一公司"]
    assert split_developer("") == []
    assert split_developer(None) == []


def test_unify_junk():
    assert unify_junk("业主") == "Unknown"
    assert unify_junk("未知") == "Unknown"
    assert unify_junk("N/A") == "Unknown"
    assert unify_junk("中国电建") is None
    assert unify_junk("当地政府") is None  # 有语义的值不动


def test_rebuild_conservative():
    m = {"中国电建": "中国电力建设集团有限公司"}
    assert rebuild("中国电建;阳光电源", m) == "中国电力建设集团有限公司; 阳光电源"
    # 无部件命中映射 → 原样返回（连分隔符风格都不动）
    assert rebuild("A公司、B公司", {}) == "A公司、B公司"
    assert rebuild("中国电建", m) == "中国电力建设集团有限公司"


def _mk_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE project_events (id INTEGER PRIMARY KEY, project_developer TEXT)")
    conn.executemany("INSERT INTO project_events(project_developer) VALUES (?)", [
        ("中国电建;阳光电源",),
        ("中国电建",),
        ("业主",),
        ("ACWA Power",),
        (None,),
    ])
    conn.commit()
    return conn


def test_plan_value_changes(tmp_path):
    db = str(tmp_path / "t.db")
    conn = _mk_db(db)
    pairs = plan_value_changes(conn, {"中国电建": "中国电力建设集团有限公司"})
    conn.close()
    d = dict(pairs)
    assert d["中国电建;阳光电源"] == "中国电力建设集团有限公司; 阳光电源"
    assert d["中国电建"] == "中国电力建设集团有限公司"
    assert d["业主"] == "Unknown"
    assert "ACWA Power" not in d, "无变化的值不列入计划"


def test_apply_value_changes_with_backup(tmp_path):
    db = str(tmp_path / "t.db")
    _mk_db(db).close()
    n = apply_value_changes(db, [("业主", "Unknown"), ("中国电建", "中国电力建设集团有限公司")])
    assert n == 2
    conn = sqlite3.connect(db)
    vals = {r[0] for r in conn.execute("SELECT project_developer FROM project_events").fetchall()}
    conn.close()
    assert "Unknown" in vals and "中国电力建设集团有限公司" in vals and "业主" not in vals
    assert list(Path(db).parent.glob("t.db.bak.*")), "apply 前必须备份"


def test_apply_empty_pairs_no_backup(tmp_path):
    db = str(tmp_path / "t.db")
    _mk_db(db).close()
    assert apply_value_changes(db, []) == 0
    assert not list(Path(db).parent.glob("t.db.bak.*")), "无变更不产生备份"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/test_developer_normalizer.py -v`
Expected: FAIL — `ModuleNotFoundError: developer_normalizer`

- [ ] **Step 4: 实现模块**

创建 `s02b_entity_normalizer/src/s02b_entity_normalizer/developer_normalizer.py`：

```python
"""developer_normalizer.py — 业主(project_events.project_developer)归一化。

流程：整值垃圾占位统一 Unknown → 拆串取部件（带频次）→ 复用 entity_normalizer 三信号
候选 + LLM 批量确认 + Union-Find 映射 → 保守重建每个业主串（部件有变化才改）。
developer_en 不在此处理——由 en_backfill 在归一后统一重算。

用法（s02b_entity_normalizer/ 目录下）：
    uv run normalize_developers --preview        # 只看候选与 would-change，不写库
    uv run normalize_developers                  # LLM 确认后写库（.bak 先备份）
    uv run normalize_developers --db PATH --out CSV
"""
import argparse
import csv
import logging
import os
import re
import shutil
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "cp950"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

from s02b_entity_normalizer.entity_normalizer import (
    BATCH_SIZE, DEFAULT_DB, _build_mapping, _find_candidates, _llm_confirm_batch,
)

logger = logging.getLogger(__name__)
_S02B_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_S02B_ROOT / ".env")
load_dotenv()

DEV_SEP_RE = re.compile(r"[;；、，,/]+")
# 整值等于这些（大小写无关）→ 统一 'Unknown'（"业主"是占位词不是公司名）
DEV_JUNK_VALUES = {"unknown", "未知", "不详", "不明", "暂无", "n/a", "na", "-", "业主", "业主方"}


def split_developer(value) -> List[str]:
    """按分隔符拆业主串为公司名列表（去空白、去空段）。"""
    s = str(value or "").strip()
    if not s:
        return []
    return [p.strip() for p in DEV_SEP_RE.split(s) if p.strip()]


def unify_junk(value) -> Optional[str]:
    """整值为垃圾占位（业主/未知/N/A 等）→ 'Unknown'；否则 None（不改）。"""
    s = str(value or "").strip()
    return "Unknown" if s.lower() in DEV_JUNK_VALUES else None


def rebuild(value: str, part_mapping: Dict[str, str]) -> str:
    """按部件映射重建业主串：任一部件命中映射才改写（'; ' 拼回）；否则原样返回（含分隔符风格）。"""
    parts = split_developer(value)
    if not parts:
        return value
    mapped = [part_mapping.get(p, p) for p in parts]
    if mapped == parts:
        return value
    return "; ".join(mapped) if len(mapped) > 1 else mapped[0]


def plan_value_changes(conn, part_mapping: Dict[str, str]) -> List[Tuple[str, str]]:
    """扫描 distinct 业主值 → (old, new) 变更对（垃圾值统一 + 部件映射重建，只列有变化的）。"""
    rows = conn.execute(
        "SELECT project_developer FROM project_events "
        "WHERE project_developer IS NOT NULL AND TRIM(project_developer) <> '' "
        "GROUP BY project_developer"
    ).fetchall()
    pairs: List[Tuple[str, str]] = []
    for (v,) in rows:
        new = unify_junk(v) or rebuild(v, part_mapping)
        if new != v:
            pairs.append((v, new))
    return pairs


def apply_value_changes(db_path: str, pairs: List[Tuple[str, str]]) -> int:
    """先备份 .bak，再事务 executemany 改写 project_developer；返回受影响行数。"""
    if not pairs:
        return 0
    bak = db_path + datetime.now().strftime(".bak.%Y%m%d_%H%M%S")
    shutil.copy2(db_path, bak)
    logger.info("主库已备份 %s", bak)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.executemany(
            "UPDATE project_events SET project_developer = ? WHERE project_developer = ?",
            [(new, old) for old, new in pairs],
        )
        conn.commit()
        logger.info("project_developer 归一落库 %d 行（%d 个不重复值）", cur.rowcount, len(pairs))
        return cur.rowcount
    except sqlite3.Error as e:
        logger.error("落库失败（备份在 %s）: %s", bak, e)
        raise
    finally:
        conn.close()


def _collect_parts(conn) -> List[Tuple[str, int]]:
    """拆串统计部件频次（按值出现行数加权），排除 Unknown 与纯 ASCII 部件。"""
    rows = conn.execute(
        "SELECT project_developer, COUNT(*) FROM project_events "
        "WHERE project_developer IS NOT NULL AND TRIM(project_developer) <> '' "
        "GROUP BY project_developer"
    ).fetchall()
    counter: Counter = Counter()
    for v, n in rows:
        if unify_junk(v):
            continue
        for p in split_developer(v):
            if p.lower() in DEV_JUNK_VALUES or not re.search(r"[一-鿿]", p):
                continue
            counter[p] += n
    return counter.most_common()


def run(db_path: Optional[str] = None, preview: bool = False) -> None:
    """主流程：候选筛查 →（非 preview）LLM 确认 → 映射 → would-change CSV →（非 preview）落库。"""
    if db_path is None and preview is False:
        parser = argparse.ArgumentParser(description="业主(project_developer)归一化")
        parser.add_argument("--preview", action="store_true", help="只看候选与 would-change，不写库")
        parser.add_argument("--db", type=str, default=None)
        parser.add_argument("--out", type=str, default=None, help="would-change CSV 路径")
        args, _ = parser.parse_known_args()
        preview = args.preview
        db_path = args.db
        out_csv = args.out
    else:
        out_csv = None

    if db_path is None:
        db_path = str(DEFAULT_DB)
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = out_csv or str(Path(db_path).parent / f"developer_normalize_{ts}.csv")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(str(Path(out_csv).with_suffix(".log")), encoding="utf-8"),
                  logging.StreamHandler()],
    )

    conn = sqlite3.connect(db_path)
    try:
        parts = _collect_parts(conn)
        print(f">>> 业主部件（中文、去垃圾）：{len(parts)} 个")
        candidates = _find_candidates(parts)
        print(f">>> 三信号候选对：{len(candidates)}")

        if preview:
            pairs = plan_value_changes(conn, {})
            _write_csv(out_csv, candidates, pairs)
            print(f">>> [preview] 垃圾值统一 would-change {len(pairs)} 个值；候选对已写 {out_csv}（LLM 未调用）")
            return

        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("未找到 GEMINI_API_KEY（.env）")
        from google import genai
        client = genai.Client(api_key=api_key)
        confirmed = []
        for i in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[i:i + BATCH_SIZE]
            print(f">>> LLM 确认批次 {i // BATCH_SIZE + 1}/{(len(candidates) - 1) // BATCH_SIZE + 1}")
            confirmed.extend(_llm_confirm_batch(client, batch))
            time.sleep(0.5)
        mapping = _build_mapping(parts, confirmed)
        print(f">>> 部件映射 {len(mapping)} 条")
        pairs = plan_value_changes(conn, mapping)
        _write_csv(out_csv, candidates, pairs, mapping)
        print(f">>> would-change {len(pairs)} 个不重复值，明细 {out_csv}")
    finally:
        conn.close()

    if not preview and pairs:
        n = apply_value_changes(db_path, pairs)
        print(f">>> 已落库 {n} 行。注意：developer_en 需随后跑 backfill_entity_en 重算。")


def _write_csv(path: str, candidates: List[dict], pairs: List[Tuple[str, str]],
               mapping: Optional[Dict[str, str]] = None) -> None:
    """把候选对 / 部件映射 / would-change 值三段写入一个报告 CSV。"""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["kind", "a", "b", "note"])
        for c in candidates:
            w.writerow(["candidate", c["name_a"], c["name_b"], c["signals"]])
        for alias, canon in sorted((mapping or {}).items()):
            w.writerow(["part_mapping", alias, canon, ""])
        for old, new in pairs:
            w.writerow(["value_change", old, new, ""])


if __name__ == "__main__":
    run()
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest -v`（s02b 全部：既有 2 + 新增 6）
Expected: 8 PASS。smoke：`uv run normalize_developers --help`？（run() 的 argparse 在无参调用才解析——smoke 改为 `uv run python -c "from s02b_entity_normalizer.developer_normalizer import run; print('import ok')"` 输出 import ok。）

- [ ] **Step 6: Commit**

```bash
git add s02b_entity_normalizer/pyproject.toml s02b_entity_normalizer/src/s02b_entity_normalizer/developer_normalizer.py s02b_entity_normalizer/tests/test_developer_normalizer.py
git commit -m "feat(s02b): normalize_developers——业主拆串部件级归一（复用三信号+LLM+Union-Find，preview 默认可查）"
```

---

### Task 4: 英文名确定性回填 `backfill_entity_en`

**Files:**
- Create: `s02b_entity_normalizer/src/s02b_entity_normalizer/en_backfill.py`
- Modify: `s02b_entity_normalizer/pyproject.toml`（scripts 加一行）
- Test: `s02b_entity_normalizer/tests/test_en_backfill.py`

**Interfaces:**
- Consumes: 修复后的 `supporting_docs/company_name_cache.json`（en_abbr-or-en 口径）；Task 3 的 `DEV_SEP_RE, split_developer`（import 复用）。
- Produces:
  - `resolve_en(cache: dict, cn) -> tuple[str | None, str]` — (新英文名或 None, status ∈ {skip, ascii_skip, hit, hit_multi, miss, partial_miss})
  - `plan_db(conn, targets: list[tuple[str, str, str]], cache: dict) -> list[dict]` — 行 `{"table","cn_col","cn","rows","current_en","new_en","status","action"}`（action ∈ {update, report_only}）
  - `apply_db(db_path: str, plan_rows: list[dict]) -> int` — .bak + 事务逐 (table, cn_col) executemany
  - 常量 `TARGETS_MAIN`、`TARGETS_PROJECTS`
  - CLI 入口 `backfill_entity_en`

- [ ] **Step 1: 写失败测试**

创建 `s02b_entity_normalizer/tests/test_en_backfill.py`：

```python
"""英文名回填：cache 确定性映射（hit 更新 / miss 只报告 / ascii 跳过 / 多公司串全命中才拼）。"""
import sqlite3
from pathlib import Path
from s02b_entity_normalizer.en_backfill import (
    TARGETS_MAIN, apply_db, plan_db, resolve_en,
)

CACHE = {
    "中国电建": {"en": "Power Construction Corporation of China", "en_abbr": "PowerChina"},
    "阳光电源": {"en": "Sungrow Power Supply Co., Ltd.", "en_abbr": None},
    "坏值公司": {"en": "坏值公司"},  # en 含中文 → 视为 miss
}


def test_resolve_en_statuses():
    assert resolve_en(CACHE, "中国电建") == ("PowerChina", "hit")          # en_abbr 优先（缩写为主口径）
    assert resolve_en(CACHE, "阳光电源") == ("Sungrow Power Supply Co., Ltd.", "hit")
    assert resolve_en(CACHE, "没收录公司") == (None, "miss")
    assert resolve_en(CACHE, "坏值公司") == (None, "miss")                  # 缓存值含中文不算命中
    assert resolve_en(CACHE, "ACWA Power") == (None, "ascii_skip")
    assert resolve_en(CACHE, "Unknown") == (None, "skip")
    assert resolve_en(CACHE, None) == (None, "skip")
    assert resolve_en(CACHE, "中国电建;阳光电源") == ("PowerChina; Sungrow Power Supply Co., Ltd.", "hit_multi")
    assert resolve_en(CACHE, "中国电建;没收录公司") == (None, "partial_miss")


def _mk_main_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE chinese_entities (id INTEGER PRIMARY KEY,
            implementing_entity TEXT, implementing_entity_en TEXT,
            subsidiary_name TEXT, subsidiary_name_en TEXT);
        CREATE TABLE project_events (id INTEGER PRIMARY KEY,
            project_developer TEXT, project_developer_en TEXT);
    """)
    conn.executemany(
        "INSERT INTO chinese_entities(implementing_entity, implementing_entity_en, subsidiary_name, subsidiary_name_en) VALUES (?,?,?,?)",
        [("中国电建", "China Power Construction Something Wrong", "阳光电源", None),
         ("中国电建", "PowerChina", None, None),          # 已一致 → 不更新
         ("没收录公司", "Old Guess", None, None)])         # miss → 不动只报告
    conn.executemany(
        "INSERT INTO project_events(project_developer, project_developer_en) VALUES (?,?)",
        [("中国电建;阳光电源", "whole-string garbage"), ("ACWA Power", "ACWA Power")])
    conn.commit()
    return conn


def test_plan_db(tmp_path):
    db = str(tmp_path / "main.db")
    conn = _mk_main_db(db)
    plan = plan_db(conn, TARGETS_MAIN, CACHE)
    conn.close()
    updates = {(p["table"], p["cn"]): p for p in plan if p["action"] == "update"}
    reports = {(p["table"], p["cn"]): p for p in plan if p["action"] == "report_only"}
    # 中国电建：一行英文错、一行已对 → 值级 update（new_en=PowerChina）
    assert updates[("chinese_entities", "中国电建")]["new_en"] == "PowerChina"
    # 子公司列独立回填
    assert updates[("chinese_entities", "阳光电源")]["new_en"].startswith("Sungrow")
    # 多公司串全命中 → update 且 '; ' 拼接
    assert updates[("project_events", "中国电建;阳光电源")]["new_en"] == "PowerChina; Sungrow Power Supply Co., Ltd."
    # miss → report_only；ascii → 不出现在计划里
    assert ("chinese_entities", "没收录公司") in reports
    assert not any(p["cn"] == "ACWA Power" for p in plan)


def test_apply_db_updates_and_backs_up(tmp_path):
    db = str(tmp_path / "main.db")
    conn = _mk_main_db(db)
    plan = plan_db(conn, TARGETS_MAIN, CACHE)
    conn.close()
    n = apply_db(db, plan)
    assert n >= 3
    conn = sqlite3.connect(db)
    ens = [r[0] for r in conn.execute(
        "SELECT implementing_entity_en FROM chinese_entities WHERE implementing_entity='中国电建'")]
    assert ens == ["PowerChina", "PowerChina"]
    miss_en = conn.execute(
        "SELECT implementing_entity_en FROM chinese_entities WHERE implementing_entity='没收录公司'"
    ).fetchone()[0]
    assert miss_en == "Old Guess", "miss 行绝不改写"
    dev_en = conn.execute(
        "SELECT project_developer_en FROM project_events WHERE project_developer='中国电建;阳光电源'"
    ).fetchone()[0]
    assert dev_en == "PowerChina; Sungrow Power Supply Co., Ltd."
    conn.close()
    assert list(Path(db).parent.glob("main.db.bak.*")), "apply 前必须备份"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_en_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: en_backfill`

- [ ] **Step 3: 实现模块**

创建 `s02b_entity_normalizer/src/s02b_entity_normalizer/en_backfill.py`：

```python
"""en_backfill.py — 用修复后的 company_name_cache.json 确定性重算英文名列（主库 + projects.db）。

口径（用户拍板「缩写为主」）：en_abbr 优先、否则 en；缓存值含中文视为 miss。
hit 且与现值不同 → update；miss/partial_miss → 只进报告（=分层核实 campaign 的候选清单）；
ascii 名与 Unknown → 跳过。dry-run 默认；--apply 每库先 .bak 再事务 executemany。

用法：uv run backfill_entity_en [--db 主库] [--projects-db projects.db] [--cache PATH] [--apply] [--out CSV]
"""
import argparse
import csv
import json
import logging
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "cp950"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from s02b_entity_normalizer.developer_normalizer import DEV_SEP_RE, split_developer
from s02b_entity_normalizer.entity_normalizer import DEFAULT_DB

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE = str(_ROOT / "supporting_docs" / "company_name_cache.json")
_CJK_RE = re.compile(r"[一-鿿]")

TARGETS_MAIN = [
    ("chinese_entities", "implementing_entity", "implementing_entity_en"),
    ("chinese_entities", "subsidiary_name", "subsidiary_name_en"),
    ("project_events", "project_developer", "project_developer_en"),
]
TARGETS_PROJECTS = [
    ("extracted_projects", "implementing_entity", "implementing_entity_en"),
    ("extracted_projects", "subsidiary_name", "subsidiary_name_en"),
    ("extracted_projects", "project_developer", "project_developer_en"),
]


def _cache_en(cache: Dict[str, dict], cn: str) -> str:
    """en_abbr 优先、否则 en（缩写为主口径）；条目缺失/值含中文返回空串。"""
    v = cache.get(cn)
    if not isinstance(v, dict):
        return ""
    en = str(v.get("en_abbr") or v.get("en") or "").strip()
    return "" if (not en or _CJK_RE.search(en)) else en


def resolve_en(cache: Dict[str, dict], cn) -> Tuple[Optional[str], str]:
    """中文名 → (新英文名, status)。多公司串须逐段全命中才拼替换（'; ' 口径与 s01b 拆串一致）。"""
    s = str(cn or "").strip()
    if not s or s.lower() == "unknown":
        return None, "skip"
    if not _CJK_RE.search(s):
        return None, "ascii_skip"
    parts = split_developer(s)
    if len(parts) <= 1:
        en = _cache_en(cache, s)
        return (en, "hit") if en else (None, "miss")
    ens = [_cache_en(cache, p) for p in parts]
    if all(ens):
        return "; ".join(ens), "hit_multi"
    return None, "partial_miss"


def plan_db(conn, targets: List[Tuple[str, str, str]], cache: Dict[str, dict]) -> List[dict]:
    """逐 (表, 中文列, 英文列) 扫描 distinct 中文值 → update / report_only 计划行。"""
    plan: List[dict] = []
    for table, cn_col, en_col in targets:
        rows = conn.execute(
            f"SELECT {cn_col}, COUNT(*) as n, MIN(COALESCE({en_col}, '')) as sample_en "
            f"FROM {table} WHERE {cn_col} IS NOT NULL AND TRIM({cn_col}) <> '' "
            f"GROUP BY {cn_col}"
        ).fetchall()
        for cn, n, sample_en in rows:
            new_en, status = resolve_en(cache, cn)
            if status in ("hit", "hit_multi"):
                # 该 cn 名下任一行英文与 new_en 不同才需要 update
                diff = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {cn_col} = ? "
                    f"AND COALESCE({en_col}, '') <> ?",
                    (cn, new_en),
                ).fetchone()[0]
                if diff:
                    plan.append({"table": table, "cn_col": cn_col, "cn": cn, "rows": diff,
                                 "current_en": sample_en, "new_en": new_en,
                                 "status": status, "action": "update"})
            elif status in ("miss", "partial_miss"):
                plan.append({"table": table, "cn_col": cn_col, "cn": cn, "rows": n,
                             "current_en": sample_en, "new_en": "",
                             "status": status, "action": "report_only"})
    return plan


def apply_db(db_path: str, plan_rows: List[dict]) -> int:
    """先备份 .bak，再按 (表, 列) 分组事务 executemany 更新 action=update 的行；返回总行数。"""
    updates = [p for p in plan_rows if p["action"] == "update"]
    if not updates:
        return 0
    bak = db_path + datetime.now().strftime(".bak.%Y%m%d_%H%M%S")
    shutil.copy2(db_path, bak)
    logger.info("库已备份 %s", bak)
    conn = sqlite3.connect(db_path)
    total = 0
    try:
        groups: Dict[Tuple[str, str], List[dict]] = {}
        for p in updates:
            en_col = next(e for t, c, e in TARGETS_MAIN + TARGETS_PROJECTS
                          if t == p["table"] and c == p["cn_col"])
            groups.setdefault((p["table"], p["cn_col"], en_col), []).append(p)
        for (table, cn_col, en_col), items in groups.items():
            cur = conn.executemany(
                f"UPDATE {table} SET {en_col} = ? WHERE {cn_col} = ?",
                [(p["new_en"], p["cn"]) for p in items],
            )
            total += cur.rowcount
        conn.commit()
        logger.info("英文名回填落库 %d 行（%d 个值级更新）", total, len(updates))
        return total
    except sqlite3.Error as e:
        logger.error("回填失败（备份在 %s）: %s", bak, e)
        raise
    finally:
        conn.close()


def main() -> None:
    """CLI：默认 dry-run 产计划 CSV（含 miss=campaign 候选）；--apply 时逐库备份后落库。"""
    parser = argparse.ArgumentParser(description="公司英文名确定性回填（缓存 en_abbr 优先口径）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="主库路径")
    parser.add_argument("--projects-db", default=None, help="s01b projects.db 路径（可选）")
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    for path, label in [(args.db, "主库"), (args.cache, "缓存")]:
        if not Path(path).exists():
            parser.error(f"{label}不存在: {path}")
    if args.projects_db and not Path(args.projects_db).exists():
        parser.error(f"projects.db 不存在: {args.projects_db}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = args.out or str(Path(args.db).parent / f"en_backfill_{ts}.csv")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(str(Path(out_csv).with_suffix(".log")), encoding="utf-8"),
                  logging.StreamHandler()],
    )

    raw = json.loads(Path(args.cache).read_text(encoding="utf-8"))
    cache = {k: v for k, v in raw.items() if not k.startswith("_")}

    jobs = [("main", args.db, TARGETS_MAIN)]
    if args.projects_db:
        jobs.append(("projects", args.projects_db, TARGETS_PROJECTS))

    all_plans: List[Tuple[str, str, List[dict]]] = []
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["db", "table", "cn_col", "cn", "rows", "current_en", "new_en", "status", "action"])
        for label, db_path, targets in jobs:
            conn = sqlite3.connect(db_path)
            try:
                plan = plan_db(conn, targets, cache)
            finally:
                conn.close()
            all_plans.append((label, db_path, plan))
            for p in plan:
                w.writerow([label, p["table"], p["cn_col"], p["cn"], p["rows"],
                            p["current_en"], p["new_en"], p["status"], p["action"]])
            n_up = sum(1 for p in plan if p["action"] == "update")
            n_miss = len(plan) - n_up
            print(f">>> [{label}] update 候选 {n_up} 值 / miss 报告 {n_miss} 值")
    print(f">>> 计划明细: {out_csv}")

    if args.apply:
        for label, db_path, plan in all_plans:
            n = apply_db(db_path, plan)
            print(f">>> [{label}] 已回填 {n} 行")
    else:
        print(">>> dry-run 完成（未写库）。复看 CSV 后加 --apply 执行。")


if __name__ == "__main__":
    main()
```

`s02b_entity_normalizer/pyproject.toml` `[project.scripts]` 追加：

```toml
backfill_entity_en     = "s02b_entity_normalizer.en_backfill:main"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest -v`（s02b 全部：2 + 6 + 3 = 11）
Expected: 全 PASS。smoke：`uv run backfill_entity_en --help` 打印 usage 退出 0。

- [ ] **Step 5: Commit**

```bash
git add s02b_entity_normalizer/src/s02b_entity_normalizer/en_backfill.py s02b_entity_normalizer/tests/test_en_backfill.py s02b_entity_normalizer/pyproject.toml
git commit -m "feat(s02b): backfill_entity_en——缓存口径确定性回填主库/projects.db 英文名列（miss 只报告）"
```

---

### Task 5: 真实数据 dry-run（只读）+ 文档收尾

**Files:**
- Create: `docs/changes/2026-W27f.md`
- Modify: `docs/status/s01b.md`、`docs/status/s02b.md`
- 产出（不提交，git-ignored 数据）：`E:\Projects\BRI_RE_Database_v2\docs\rules_and_plans\2026-07-04-cache-hygiene-dryrun.csv`、`2026-07-04-en-backfill-dryrun.csv`

- [ ] **Step 1: s01b + s02b 全量回归**

Run: `cd <worktree>\s01b_project_extractor && uv run pytest`（预期 284+13 全绿）；`cd <worktree>\s02b_entity_normalizer && uv run pytest`（预期 11 全绿）。记录两个总数。

- [ ] **Step 2: 真实缓存 dry-run（只读，绝不 --apply）**

```bash
cd E:\Projects\BRI_RE_Database_v2\.claude\worktrees\entity-name-quality\s01b_project_extractor
uv run cache_fix --cache E:\Projects\BRI_RE_Database_v2\supporting_docs\company_name_cache.json --corrections E:\Projects\BRI_RE_Database_v2\.claude\worktrees\entity-name-quality\supporting_docs\cache_corrections\2026-07-04-hard-errors.csv --out E:\Projects\BRI_RE_Database_v2\docs\rules_and_plans\2026-07-04-cache-hygiene-dryrun.csv
```

Expected: 正常退出；清理候选数量级 ~180-200（178 分隔符键 + 其他规则）、corrections 变更 4。合理性抽查：报告前 20 行不应出现正常公司名（如带"有限公司"结尾的规范名）；若出现明显误报 → STOP 报 DONE_WITH_CONCERNS 附例子（不要自行改规则）。

- [ ] **Step 3: 真实主库回填 dry-run（只读，绝不 --apply）**

```bash
cd E:\Projects\BRI_RE_Database_v2\.claude\worktrees\entity-name-quality\s02b_entity_normalizer
uv run backfill_entity_en --db E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db --projects-db E:\Projects\BRI_RE_Database_v2\s01b_project_extractor\data\projects.db --cache E:\Projects\BRI_RE_Database_v2\supporting_docs\company_name_cache.json --out E:\Projects\BRI_RE_Database_v2\docs\rules_and_plans\2026-07-04-en-backfill-dryrun.csv
```

Expected: 正常退出，报告两库的 update/miss 计数（注意：此时缓存尚未 apply corrections，数字是"现状缓存"口径，写入文档时注明）。miss 清单即 campaign 候选池规模——记录数字。

- [ ] **Step 4: 写变更日志 `docs/changes/2026-W27f.md`**

追加式条目，含：日期 2026-07-04 · 模块 s01b/s02b/supporting_docs · 做了什么（四个交付物逐条）· 为什么（缓存审计发现：4402 条仅 40 verified、178 垃圾键、4 张冠李戴错译、developer 3492 值 866 多公司串零归一；引 2026-07-03 grill-me 定稿六口径）· 影响（新文件/CLI/测试清单；两个 dry-run 数字）· **GATED 执行序列**（用户复看后依次）：① `cache_fix --findings <复看后CSV> --corrections ... --apply` ② 分层核实 campaign（另行，产 corrections 再 apply 一轮）③ `normalize_developers --preview` 复看→真跑 ④ `backfill_entity_en --apply`（主库+projects.db）⑤ s03 重导出 + 部署。写明「缩写为主」「不重建 s02a」两个口径决策。

- [ ] **Step 5: 更新状态文件（覆盖式，现在时）**

`docs/status/s01b.md`：「当前功能」补翻译入口行为（ASCII 直通；developer 按 ;／、拆串逐家翻译 '; ' 拼回；多公司串/超长键不写缓存）；「如何运行」补 `uv run cache_fix`。
`docs/status/s02b.md`：「当前功能」补 developer 归一（normalize_developers，部件级三信号+LLM）与英文名回填（backfill_entity_en，en_abbr 优先口径、miss 只报告）；「如何运行」补两条命令；「输入/输出」补 company_name_cache.json 为 en_backfill 输入。

- [ ] **Step 6: Commit**

```bash
git add docs/changes/2026-W27f.md docs/status/s01b.md docs/status/s02b.md
git commit -m "docs(changes+status): 实体名质量专项交付记录 + s01b/s02b 状态同步"
```

---

## Self-Review 记录

- **Spec 覆盖**（对照 2026-07-03 grill-me 六口径）：分层核实=Task 1 corrections 通道承接（campaign 本身计划外）✅；developer 英文侧拆串=Task 2、中文侧 s02b 扩展=Task 3 ✅；缩写为主=`_resolve_entity_en` 逻辑不改 + `_cache_en` en_abbr 优先 ✅；核实执行=campaign 计划外、miss 报告供其取材 ✅；dry-run gated=四个工具全部 dry-run/preview 默认 + Task 1-4 禁真实数据 apply + Task 5 只读 dry-run ✅；回填面=主库三列对 + projects.db 三列对 + s03 重导出列入 GATED 序列 ✅。
- **占位符扫描**：无 TBD/TODO；所有代码步骤含完整代码；命令含预期输出。
- **类型一致性**：`split_developer`/`DEV_SEP_RE` 在 Task 3 定义、Task 4 import 复用，签名一致；`resolve_en(cache, cn) -> (str|None, str)` 定义与测试一致；`scan_cache`/`plan_corrections`/`apply_changes` 签名在 Task 1 测试与实现一致；'; ' 拼接口径在 Task 2 (`_resolve_developer_en`)、Task 3 (`rebuild`)、Task 4 (`resolve_en` hit_multi) 三处统一。
- **已知取舍**：① `unify_junk` 只统一整值垃圾，不处理"业主含垃圾部件"的混合串（罕见，campaign/复看兜底）；② `plan_db` 的 `current_en` 取 MIN 样本仅供报告展示，update 判定用逐行 diff 计数，准确；③ Task 1 的 R4 超长键阈值 30 与 Task 2 `_CACHE_KEY_MAX_LEN` 一致（同一常数两处出现属跨子项目，允许）。
