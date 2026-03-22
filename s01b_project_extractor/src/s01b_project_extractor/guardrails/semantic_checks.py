"""Layer 2: 语义校验 — 独立模型交叉验证 project_summary 与字段一致性"""

import concurrent.futures
import json
import os
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


def run_semantic_checks(
    records: list[dict],
    batch_size: int = 20,
    max_workers: int = 4,
) -> list[str]:
    """
    对一批提取记录执行语义校验（独立模型交叉验证）。

    Args:
        records:     从 projects.db 查出的字典列表
        batch_size:  每次调用校验模型的记录数（默认 20，减少 API 调用次数）
        max_workers: 并发批次数（默认 4，批次间并行发送请求）

    Returns:
        错误描述列表。空列表表示全部通过。
    """
    # 过滤掉没有 project_summary 的记录
    records_with_summary = [r for r in records if r.get("project_summary", "").strip()]
    if not records_with_summary:
        print("[语义校验] 无 project_summary 数据，跳过语义校验")
        return []

    backend, client, model = _get_validation_client()
    if client is None:
        print("[语义校验] 无可用校验模型（检查 VALIDATION_API_KEY 或 GEMINI_API_KEY），跳过")
        return []

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

    def _call_one_batch(args: tuple[int, list[dict]]) -> list[str]:
        """处理单个批次，返回该批次的错误列表。"""
        batch_idx, batch = args
        prompt = _build_validation_prompt(batch)
        try:
            issues = call_fn(client, model, prompt)
            results = []
            for issue in issues:
                rid      = issue.get("record_id", "?")
                field    = issue.get("field", "?")
                extracted = issue.get("extracted_value", "?")
                expected  = issue.get("expected_from_summary", "?")
                reason    = issue.get("reason", "")
                results.append(
                    f"项目 #{rid} 语义不一致: {field}='{extracted}' "
                    f"(应为 '{expected}') — {reason}"
                )
            return results
        except Exception as e:
            print(f"[语义校验] 批次 {batch_idx + 1}/{len(batches)} 调用失败: {e}，跳过")
            return []

    # 并发调用所有批次
    errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=actual_workers) as executor:
        for batch_errors in executor.map(_call_one_batch, enumerate(batches)):
            errors.extend(batch_errors)

    return errors
