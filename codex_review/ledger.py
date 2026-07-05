"""Codex 判定台账：内容哈希幂等、原子写，防止重复判定。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from codex_review.schemas import ProjectContext

logger = logging.getLogger(__name__)

# 台账默认路径
DEFAULT_LEDGER_PATH = Path(__file__).parent / "ledger.json"

LedgerStatus = Literal["accepted_no_change", "applied", "rejected"]


# ──────────────────────────────────────────────
# key 计算
# ──────────────────────────────────────────────


def _sig(p: ProjectContext | dict) -> str:
    """计算单个项目的签名串：canonical_id‖project_name‖country_iso‖category。"""
    if isinstance(p, dict):
        return "‖".join(
            [
                p.get("canonical_id", ""),
                p.get("project_name", ""),
                p.get("country_iso") or "",
                p.get("category", ""),
            ]
        )
    return "‖".join(
        [
            p.canonical_id,
            p.project_name,
            p.country_iso or "",
            p.category,
        ]
    )


def t1_key(a: ProjectContext | dict, b: ProjectContext | dict) -> str:
    """计算 T1 对的幂等 key：两端 sig 排序后取 sha1，与顺序无关。"""
    sig_a = _sig(a)
    sig_b = _sig(b)
    payload = "T1‖" + "❘".join(sorted([sig_a, sig_b]))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def t2_key(project_id: str, trigger: str) -> str:
    """计算 T2 条目的幂等 key：project_id + trigger 取 sha1。"""
    payload = "T2‖" + project_id + "‖" + trigger
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def t3_key(raw_name: str) -> str:
    """计算 T3 条目的幂等 key：raw_name 取 sha1。"""
    payload = "T3‖" + raw_name
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────
# 台账 I/O
# ──────────────────────────────────────────────


def load_ledger(path: str | Path) -> dict:
    """从磁盘加载台账；文件不存在返回空台账，损坏 JSON 抛异常。"""
    path = Path(path)
    if not path.exists():
        return {"schema_version": 1, "entries": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("台账文件 JSON 解析失败，路径=%s，错误=%s", path, e)
        raise ValueError(f"台账文件损坏，无法解析 JSON：{path}") from e
    if not isinstance(data, dict) or "entries" not in data:
        logger.error("台账文件结构非法，路径=%s", path)
        raise ValueError(f"台账文件结构非法，缺少 'entries' 字段：{path}")
    return data


def save_ledger(path: str | Path, ledger: dict) -> None:
    """原子写台账到磁盘：先写临时文件再 replace，防止写入中断导致损坏。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    dir_ = str(path.parent)
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        logger.error("台账写入失败，路径=%s，错误=%s", path, e)
        # 清理临时文件（静默——若 tmp_path 已被 replace 则不存在，无害）
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass  # 已被 replace，无需清理
        raise


# ──────────────────────────────────────────────
# 查询与写入
# ──────────────────────────────────────────────


def is_settled(ledger: dict, key: str) -> bool:
    """判断某 key 是否已在台账中定案（exists 即为 True）。"""
    return key in ledger.get("entries", {})


def _today() -> str:
    """返回今日 ISO 日期字符串（YYYY-MM-DD）。"""
    from datetime import date

    return date.today().isoformat()


def record(
    ledger: dict,
    key: str,
    *,
    task: str,
    quarter: str,
    item_id: str,
    status: LedgerStatus,
    verdict_digest: str,
) -> None:
    """向内存台账写入一条定案记录（调用方负责调 save_ledger 持久化）。"""
    ledger.setdefault("entries", {})[key] = {
        "task": task,
        "quarter": quarter,
        "item_id": item_id,
        "status": status,
        "decided_at": _today(),
        "verdict_digest": verdict_digest,
    }
