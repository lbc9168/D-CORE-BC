"""Layer 2: 语义校验 — 独立模型交叉验证 project_summary 与字段一致性"""

import json
import os
import time
from typing import Any


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
    """构建批量语义校验的 prompt。"""
    records_text = ""
    for rec in batch:
        records_text += f"""
---
record_id: {rec.get("id", "?")}
project_summary: {rec.get("project_summary", "（无摘要）")}
提取字段:
  project_name: {rec.get("project_name", "")}
  country: {rec.get("country", "")}
  location: {rec.get("location", "")}
  category: {rec.get("category", "")}
  capacity_mw: {rec.get("capacity_mw", "")}
  project_stage: {rec.get("project_stage", "")}
  project_developer: {rec.get("project_developer", "")}
  implementing_entity: {rec.get("implementing_entity", "")}
  subsidiary_name: {rec.get("subsidiary_name", "")}
"""

    return f"""你是一个数据质量审核员。以下是从新闻中提取的可再生能源项目信息。
每条记录都包含一段"project_summary"（从新闻原文中提炼的项目摘要）和对应的结构化提取字段。

请对比每条记录的 project_summary 与提取字段，找出**明显不一致**的字段。例如：
- 国家名与摘要描述不符
- 容量数值与摘要差距大
- 分类与摘要描述的项目类型矛盾（如摘要说"风电"但分类填了"光伏"）
- 中方企业名称与摘要中提到的企业不同

**只报告确定的错误**，忽略模糊或信息不足的情况。
如果 project_summary 为空或内容太少，跳过该记录。

请以 JSON 数组格式输出：
[
  {{"record_id": 123, "field": "category", "extracted_value": "光伏", "expected_from_summary": "风电", "reason": "摘要明确提到风电项目"}},
  ...
]

如果没有任何错误，返回空数组 []。

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
    # 兼容 {"errors": [...]} 或直接 [...]
    if isinstance(result, dict):
        return result.get("errors", result.get("results", []))
    return result if isinstance(result, list) else []


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
    if isinstance(result, dict):
        return result.get("errors", result.get("results", []))
    return result if isinstance(result, list) else []


def run_semantic_checks(records: list[dict], batch_size: int = 10) -> list[str]:
    """
    对一批提取记录执行语义校验（独立模型交叉验证）。

    Args:
        records: 从 projects.db 查出的字典列表
        batch_size: 每次调用校验模型的记录数

    Returns:
        错误描述列表。空列表表示全部通过。
    """
    # 过滤掉没有 project_summary 的记录
    records_with_summary = [r for r in records if r.get("project_summary", "").strip()]
    if not records_with_summary:
        print("[语义校验] 无 project_summary 数据，跳过语义校验")
        return []

    backend, client, model = _get_validation_client()
    if client is None or not model:
        print("[语义校验] 无可用校验模型（检查 VALIDATION_API_KEY 或 GEMINI_API_KEY），跳过")
        return []

    print(f"[语义校验] 使用 {backend}/{model}，共 {len(records_with_summary)} 条待检")

    errors: list[str] = []
    call_fn = _call_openai if backend == "openai" else _call_gemini
    total_batches = (len(records_with_summary) + batch_size - 1) // batch_size

    # 分批调用
    for i in range(0, len(records_with_summary), batch_size):
        batch_idx = i // batch_size + 1
        batch = records_with_summary[i:i + batch_size]
        prompt = _build_validation_prompt(batch)
        t0 = time.time()
        print(
            f"[语义校验] 进度 {batch_idx}/{total_batches} "
            f"（本批 {len(batch)} 条，累计错误 {len(errors)}）"
        )

        try:
            issues = call_fn(client, model, prompt)
            for issue in issues:
                rid = issue.get("record_id", "?")
                field = issue.get("field", "?")
                extracted = issue.get("extracted_value", "?")
                expected = issue.get("expected_from_summary", "?")
                reason = issue.get("reason", "")
                errors.append(
                    f"项目 #{rid} 语义不一致: {field}='{extracted}' "
                    f"(应为 '{expected}') — {reason}"
                )
            elapsed = time.time() - t0
            print(
                f"[语义校验] 批次 {batch_idx}/{total_batches} 完成，"
                f"新增问题 {len(issues)}，累计 {len(errors)}，耗时 {elapsed:.1f}s"
            )
        except Exception as e:
            elapsed = time.time() - t0
            print(
                f"[语义校验] 批次 {batch_idx}/{total_batches} 调用失败: {e}，"
                f"已跳过（耗时 {elapsed:.1f}s）"
            )

    return errors
