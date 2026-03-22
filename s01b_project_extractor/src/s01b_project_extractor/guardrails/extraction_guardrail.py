"""
提取质量 Guardrail — 主入口

串联三层校验：
  Layer 1: 格式校验 (format_checks)     — 纯 Python
  Layer 2: 语义校验 (semantic_checks)   — 独立模型交叉验证
  Layer 3: 批次统计校验 (batch_checks)  — 纯 Python 统计

crewAI 在 agent 完成 extract_projects_task 后自动调用此函数。
返回 (True, output) 通过，返回 (False, error_msg) 触发重试。
"""

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from crewai.tasks.task_output import TaskOutput

from s01b_project_extractor.guardrails.batch_checks import run_batch_checks
from s01b_project_extractor.guardrails.format_checks import run_format_checks
from s01b_project_extractor.guardrails.semantic_checks import run_semantic_checks


_S01B_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PROJECTS_DB = str(_S01B_ROOT / "data" / "projects.db")


def _parse_batch_id_from_output(raw: str) -> str:
    """从 TaskOutput 文本中解析 batch_id。"""
    # 尝试匹配 "batch_id=xxx"
    m = re.search(r"batch_id=(\S+)", raw)
    if m:
        return m.group(1)
    return ""


def _parse_db_path_from_output(raw: str) -> str:
    """从 TaskOutput 文本中解析 projects.db 路径。"""
    # 匹配 "→ /path/to/projects.db" 或 "→ X:\path\to\projects.db"
    m = re.search(r"→\s+(.+?projects\.db)", raw)
    if m:
        return m.group(1).strip()
    return _DEFAULT_PROJECTS_DB


def _load_batch_records(db_path: str, batch_id: str) -> tuple[list[dict], str]:
    """从 projects.db 加载指定批次记录，返回 (records, effective_batch_id)。"""
    if not os.path.exists(db_path):
        return [], batch_id

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        effective_batch_id = batch_id.strip()
        if not effective_batch_id:
            row = conn.execute(
                "SELECT batch_id FROM extracted_projects "
                "WHERE batch_id IS NOT NULL AND TRIM(batch_id) != '' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            effective_batch_id = str(row["batch_id"]).strip() if row and row["batch_id"] else ""

        if effective_batch_id:
            rows = conn.execute(
                "SELECT * FROM extracted_projects WHERE batch_id = ?",
                (effective_batch_id,),
            ).fetchall()
        else:
            rows = []
        return [dict(r) for r in rows], effective_batch_id
    except Exception as e:
        print(f"[Guardrail] 加载记录失败: {e}")
        return [], batch_id
    finally:
        conn.close()


def _delete_failed_records(db_path: str, record_ids: list[int]) -> int:
    """删除不合格记录，以便重试时重新提取。"""
    if not record_ids or not os.path.exists(db_path):
        return 0

    conn = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" for _ in record_ids)
        conn.execute(
            f"DELETE FROM extracted_projects WHERE id IN ({placeholders})",
            record_ids,
        )
        conn.commit()
        deleted = conn.total_changes
        return deleted
    except Exception as e:
        print(f"[Guardrail] 删除失败记录出错: {e}")
        return 0
    finally:
        conn.close()


def validate_extraction(output: TaskOutput) -> tuple[bool, Any]:
    """
    crewAI guardrail 入口函数。

    签名: (TaskOutput) -> tuple[bool, Any]
    - (True, output.raw)     → 校验通过
    - (False, error_message) → 校验失败，error_message 反馈给 agent 重试
    """
    raw = output.raw or ""

    print(f"\n{'='*60}")
    print("  [Guardrail] 提取质量校验开始")
    print(f"{'='*60}")

    # ── 解析上下文 ──
    batch_id = _parse_batch_id_from_output(raw)
    batch_id_from_output = bool(batch_id)
    db_path = _parse_db_path_from_output(raw)

    print(f"  batch_id: {batch_id}")
    print(f"  db_path:  {db_path}")

    # ── 加载本批次记录 ──
    records, effective_batch_id = _load_batch_records(db_path, batch_id)
    if effective_batch_id and effective_batch_id != batch_id:
        print(f"  [Guardrail] 未从输出解析到 batch_id，回退使用最新批次: {effective_batch_id}")
    batch_id = effective_batch_id
    if not records:
        print("  [Guardrail] 无记录可校验，通过")
        return True, raw

    print(f"  本批次记录数: {len(records)}")

    all_errors: list[str] = []
    failed_record_ids: list[int] = []

    # ── Layer 1: 格式校验 ──
    print("\n  --- Layer 1: 格式校验 ---")
    format_errors = run_format_checks(records)
    if format_errors:
        print(f"  格式错误: {len(format_errors)} 个")
        for e in format_errors[:5]:
            print(f"    - {e}")
        all_errors.extend(format_errors)

        # 收集有格式错误的记录 ID
        for e in format_errors:
            m = re.search(r"项目 #(\d+)", e)
            if m:
                failed_record_ids.append(int(m.group(1)))
    else:
        print("  格式校验通过 ✓")

    # ── Layer 2: 语义校验 ──
    print("\n  --- Layer 2: 语义校验 ---")
    try:
        semantic_errors = run_semantic_checks(records)
        if semantic_errors:
            print(f"  语义错误: {len(semantic_errors)} 个")
            for e in semantic_errors[:5]:
                print(f"    - {e}")
            all_errors.extend(semantic_errors)

            for e in semantic_errors:
                m = re.search(r"项目 #(\d+)", e)
                if m:
                    failed_record_ids.append(int(m.group(1)))
        else:
            print("  语义校验通过 ✓")
    except Exception as e:
        print(f"  语义校验异常（跳过）: {e}")

    # ── Layer 3: 批次统计校验 ──
    print("\n  --- Layer 3: 批次统计校验 ---")
    hard_errors, warnings = run_batch_checks(records)
    if warnings:
        for w in warnings:
            print(f"  [警告] {w}")
    if hard_errors:
        print(f"  统计硬错误: {len(hard_errors)} 个")
        all_errors.extend(hard_errors)
    else:
        print("  批次统计校验通过 ✓")

    # ── 汇总结果 ──
    print(f"\n  {'='*60}")

    if not all_errors:
        print("  [Guardrail] 全部通过 ✓")
        print(f"  {'='*60}\n")
        return True, raw

    # 有错误：删除不合格记录后触发重试
    unique_failed_ids = list(set(failed_record_ids))
    if unique_failed_ids:
        if batch_id_from_output:
            deleted = _delete_failed_records(db_path, unique_failed_ids)
            print(f"  已删除 {deleted} 条不合格记录，等待重试")
        else:
            print("  [Guardrail] 跳过自动删除：本次未从输出中解析到 batch_id，避免误删非本批次数据")

    error_summary = f"发现 {len(all_errors)} 个质量问题:\n" + "\n".join(
        f"  - {e}" for e in all_errors[:20]  # 限制反馈长度
    )
    if len(all_errors) > 20:
        error_summary += f"\n  ... 还有 {len(all_errors) - 20} 个问题"

    print(f"  [Guardrail] 校验失败，触发重试")
    print(f"  {'='*60}\n")

    return False, error_summary
