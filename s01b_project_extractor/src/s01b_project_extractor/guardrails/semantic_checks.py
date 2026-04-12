"""Layer 2: 语义校验 — 独立模型校验并纠偏枚举字段"""

import concurrent.futures
import json
import os
import time
from typing import Any

VALID_STAGE_ENUMS = {
    "拟开发", "收购", "中标", "签约", "开工", "在建", "竣工", "投入运营", "Unknown",
}
VALID_CATEGORY_ENUMS = {
    "新能源制造业", "输变电建设", "火电", "光伏", "风电", "水电",
    "核电", "地热及余热", "生物质", "储能", "综合能源", "Unknown",
}


def _get_validation_client():
    """
    根据 .env 配置获取校验模型客户端。
    优先使用 VALIDATION_MODEL (DeepSeek-V3 via OpenAI SDK)，
    回退到 Gemini。
    """
    val_api_key = os.getenv("VALIDATION_API_KEY", "").strip()
    val_model = os.getenv("VALIDATION_MODEL", "").strip()
    val_base_url = os.getenv("VALIDATION_BASE_URL", "").strip()

    if val_api_key and val_model:
        # 使用 OpenAI 兼容 API (DeepSeek-V3 等)
        try:
            from openai import OpenAI
            client = OpenAI(api_key=val_api_key, base_url=val_base_url or None)
            return "openai", client, val_model
        except ImportError:
            print("[语义校验] openai 库未安装，回退到 Gemini")

    # 回退: 使用 Gemini
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            return "gemini", client, "gemini-2.5-flash"
        except ImportError:
            pass

    return None, None, None


def _build_validation_prompt(batch: list[dict]) -> str:
    """构建批量语义纠偏 prompt（仅 stage/category）。"""
    records_text = ""
    for rec in batch:
        records_text += f"""
---
record_id: {rec.get("id", "?")}
project_summary: {rec.get("project_summary", "（无摘要）")}
提取字段:
  category: {rec.get("category", "")}
  project_stage: {rec.get("project_stage", "")}
"""

    stage_enums = "、".join(sorted(VALID_STAGE_ENUMS))
    category_enums = "、".join(sorted(VALID_CATEGORY_ENUMS))
    return f"""你是一个数据质量审核员。以下是从新闻中提取的可再生能源项目信息。
每条记录都包含一段 project_summary 和两个待检字段：project_stage、category。

任务目标（只做这两项）：
1) 判断 project_stage 与摘要语义是否一致。
2) 判断 category 与摘要语义是否一致。

重要约束：
- 仅检查字段 project_stage、category。
- 严禁输出 implementing_entity、subsidiary_name 等其他字段建议。
- 若信息不足或无法确定，跳过该记录，不要输出建议。
- 输出的 suggested_value 必须是允许枚举值：
  - project_stage: {stage_enums}
  - category: {category_enums}
- confidence 范围必须是 0~1 的小数。

请输出 JSON 对象（不是数组），格式如下：
{{
  "corrections": [
    {{
      "record_id": 123,
      "field": "project_stage",
      "extracted_value": "中标",
      "suggested_value": "拟开发",
      "reason": "摘要仅表述准备参与招标，尚未中标",
      "confidence": 0.92
    }}
  ]
}}

若无需纠偏，返回：
{{"corrections":[]}}

{records_text}"""


def _call_openai(client, model: str, prompt: str) -> list[dict]:
    """通过 OpenAI 兼容 API 调用校验模型。"""
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    text = response.choices[0].message.content
    result = json.loads(text)
    return _extract_corrections(result)


def _call_gemini(client, model: str, prompt: str) -> list[dict]:
    """通过 Gemini API 调用校验模型。"""
    from google.genai import types
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )
    result = json.loads(response.text)
    return _extract_corrections(result)


def _extract_corrections(result: Any) -> list[dict]:
    """兼容不同 JSON 结构，提取 corrections 列表。"""
    if isinstance(result, dict):
        if isinstance(result.get("corrections"), list):
            return result["corrections"]
        if isinstance(result.get("errors"), list):
            return result["errors"]
        if isinstance(result.get("results"), list):
            return result["results"]
    if isinstance(result, list):
        return result
    return []


def _validate_and_normalize_corrections(corrections: list[dict]) -> list[dict]:
    """仅保留可自动应用的纠偏建议。"""
    valid: list[dict] = []
    for item in corrections:
        rid = item.get("record_id")
        field = str(item.get("field", "")).strip()
        suggested = str(item.get("suggested_value", item.get("expected_from_summary", ""))).strip()
        extracted = str(item.get("extracted_value", "")).strip()
        reason = str(item.get("reason", "")).strip()
        conf_raw = item.get("confidence", 0.0)
        try:
            confidence = float(conf_raw)
        except Exception:
            confidence = 0.0

        if not isinstance(rid, int):
            continue
        if field == "project_stage":
            if suggested not in VALID_STAGE_ENUMS:
                continue
        elif field == "category":
            if suggested not in VALID_CATEGORY_ENUMS:
                continue
        else:
            continue
        if suggested == extracted:
            continue
        valid.append(
            {
                "record_id": rid,
                "field": field,
                "extracted_value": extracted,
                "suggested_value": suggested,
                "reason": reason,
                "confidence": max(0.0, min(1.0, confidence)),
            }
        )
    return valid


def run_semantic_checks(
    records: list[dict],
    batch_size: int = 20,
    max_workers: int = 4,
) -> tuple[list[str], list[dict]]:
    """
    对一批提取记录执行语义校验（独立模型交叉验证）。

    Args:
        records:     从 projects.db 查出的字典列表
        batch_size:  每次调用校验模型的记录数（默认 20，减少 API 调用次数）
        max_workers: 并发批次数（默认 4，批次间并行发送请求）

    Returns:
        (errors, fixes):
        - errors: 语义异常描述（会进入 guardrail 错误汇总）
        - fixes: 可自动应用的纠偏建议（仅 stage/category）
    """
    # 过滤掉没有 project_summary 的记录
    records_with_summary = [r for r in records if r.get("project_summary", "").strip()]
    if not records_with_summary:
        print("[语义校验] 无 project_summary 数据，跳过语义校验")
        return [], []

    backend, client, model = _get_validation_client()
    if client is None or not model:
        print("[语义校验] 无可用校验模型（检查 VALIDATION_API_KEY 或 GEMINI_API_KEY），跳过")
        return [], []

    # 预构建所有批次
    batches = [
        records_with_summary[i : i + batch_size]
        for i in range(0, len(records_with_summary), batch_size)
    ]
    actual_workers = min(max_workers, len(batches))
    print(
        f"[语义校验] 使用 {backend}/{model}，"
        f"共 {len(records_with_summary)} 条 → {len(batches)} 批，"
        f"并发 {actual_workers} 线程"
    )

    call_fn = _call_openai if backend == "openai" else _call_gemini
    total_batches = len(batches)

    def _call_one_batch(args: tuple[int, list[dict]]) -> tuple[int, list[str], list[dict], int, float, str]:
        """处理单个批次，返回 (batch_idx, errors, fixes, issues_count, elapsed, err_msg)。"""
        batch_idx, batch = args
        t0 = time.time()
        prompt = _build_validation_prompt(batch)
        try:
            corrections = call_fn(client, model, prompt)
            valid_fixes = _validate_and_normalize_corrections(corrections)
            return batch_idx, [], valid_fixes, len(valid_fixes), time.time() - t0, ""
        except Exception as e:
            return batch_idx, [], [], 0, time.time() - t0, str(e)

    errors: list[str] = []
    fixes: list[dict] = []
    print(f"[语义校验] 开始批量校验：共 {total_batches} 批")
    with concurrent.futures.ThreadPoolExecutor(max_workers=actual_workers) as executor:
        futures = {
            executor.submit(_call_one_batch, (idx + 1, batch)): (idx + 1, len(batch))
            for idx, batch in enumerate(batches)
        }
        for future in concurrent.futures.as_completed(futures):
            batch_idx, batch_len = futures[future]
            print(
                f"[语义校验] 进度 {batch_idx}/{total_batches} "
                f"（本批 {batch_len} 条，累计错误 {len(errors)}）"
            )
            idx_ret, batch_errors, batch_fixes, issues_count, elapsed, err_msg = future.result()
            if err_msg:
                print(
                    f"[语义校验] 批次 {idx_ret}/{total_batches} 调用失败: {err_msg}，"
                    f"已跳过（耗时 {elapsed:.1f}s）"
                )
                continue
            errors.extend(batch_errors)
            fixes.extend(batch_fixes)
            print(
                f"[语义校验] 批次 {idx_ret}/{total_batches} 完成，"
                f"新增纠偏 {issues_count}，累计 {len(fixes)}，耗时 {elapsed:.1f}s"
            )

    # 高置信纠偏优先应用；低置信建议仅记录（不作为 hard error）
    high_conf_fixes = [f for f in fixes if f["confidence"] >= 0.8]
    low_conf = [f for f in fixes if f["confidence"] < 0.8]
    if low_conf:
        print(f"[语义校验] 低置信建议 {len(low_conf)} 条（confidence < 0.8），已忽略")
    return errors, high_conf_fixes
