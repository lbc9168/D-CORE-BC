"""province_extractor.py
从 canonical_projects.location 字段中提取一级行政区（省/州/邦等）英文名。

处理逻辑：
- location 为空 → 直接返回 None，不调用 API
- 已有缓存 → 从 province_cache.json 读取，不重复调用
- 其余 → 批量送 Gemini，结果写回缓存

省份约束：
- 从 Natural Earth admin1 数据加载该国已知省份列表，随 prompt 传入 Gemini
- Gemini 输出必须来自列表，或 null（防止幻觉）
- zip 不存在或 pyshp 未安装时退化为原有无约束行为
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types, errors
import httpx

from s03_data_exporter.admin1_lookup import build_admin1_dict

GEMINI_MODEL = "gemini-2.5-flash-lite"
BATCH_SIZE = 30  # 每次 API 调用处理的项目数


def _cache_location_key(record: dict) -> str:
    """以国家和规范化 location 构造省份缓存键，避免 canonical ID 重用串值。"""
    iso = str(record.get("country_iso") or "UNK").strip().upper()
    location = re.sub(r"[\s,，;；·._-]+", "", str(record.get("location") or "").strip()).casefold()
    return f"location::{iso}::{location}"


def _cache_fingerprint_key(project_id: str) -> str:
    """构造项目 ID 对应位置指纹的审计键。"""
    return f"fingerprint::{project_id}"


def _load_cache(cache_path: Path) -> dict:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache_path: Path, cache: dict) -> None:
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def extract_provinces(
    records: list[dict],
    cache_path: Path,
    api_key: str,
    *, allow_model_calls: bool = True, diagnostics: dict | None = None,
) -> dict[str, Optional[str]]:
    """
    批量提取省份，返回 {project_id: province_en_or_None}。

    Args:
        records:    [{"project_id": ..., "location": ..., "country": ..., "country_iso": ...}, ...]
        cache_path: province_cache.json 路径（运行时自动创建）
        api_key:    Gemini API Key
    """
    if diagnostics is not None:
        diagnostics.update(failed_ids=[], failures=[])
    # 加载 Natural Earth 省份约束表（模块级单例，首次调用时读取 zip）
    admin1_dict = build_admin1_dict()

    cache = _load_cache(cache_path)
    result: dict[str, Optional[str]] = {}
    to_process: list[dict] = []

    for rec in records:
        pid = rec["project_id"]
        loc = (rec.get("location") or "").strip()
        location_key = _cache_location_key(rec)
        fingerprint_key = _cache_fingerprint_key(pid)
        if not loc:
            result[pid] = None
        elif location_key in cache:
            result[pid] = cache[location_key]
            cache[fingerprint_key] = location_key
        elif pid in cache and (
            fingerprint_key not in cache or cache.get(fingerprint_key) == location_key
        ):
            # 旧缓存一次性迁移：当前数据已经过人工修正；迁移后 ID/位置不一致会失效。
            result[pid] = cache[pid]
            cache[location_key] = cache[pid]
            cache[fingerprint_key] = location_key
        else:
            to_process.append(rec)

    if not to_process:
        _save_cache(cache_path, cache)
        return result

    # SKIP_PROVINCE_API=1 时跳过所有 API 调用，未缓存项目省份设为 null
    if not allow_model_calls or os.environ.get("SKIP_PROVINCE_API") == "1":
        print(f"  [跳过] SKIP_PROVINCE_API=1，{len(to_process)} 条未缓存记录省份设为 null")
        for rec in to_process:
            result[rec["project_id"]] = None
        return result

    client = genai.Client(api_key=api_key)

    for batch_start in range(0, len(to_process), BATCH_SIZE):
        batch = to_process[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(to_process) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"  省份提取批次 {batch_num}/{total_batches}（{len(batch)} 条）...")

        items_json = json.dumps(
            [
                {
                    "id": r["project_id"],
                    "location": r["location"],
                    "country": r["country"],
                    # 已知省份列表约束：无数据时为 null，Gemini 须按常识判断
                    "known_provinces": admin1_dict.get(
                        (r.get("country_iso") or "").strip().upper()
                    ) or None,
                }
                for r in batch
            ],
            ensure_ascii=False,
        )

        prompt = f"""你是地理信息专家。对于以下每个项目，从 location 字段中提取所在地的一级行政区（省/州/邦/大区等）的标准英文名称。

每个项目包含字段：id（项目ID）、location（位置描述）、country（国家）、known_provinces（该国已知一级行政区英文名列表，可为 null）。

规则：
1. 若 location 中有明确的省/州/邦名称，从 known_provinces 中选取最接近的名称返回（精确或近似匹配均可）
2. 若 location 只包含城市名，判断该城市属于哪个一级行政区，从 known_provinces 中返回对应省份名
3. 若 location 是本身即为一级行政区的首都或直辖市（如"内罗毕"、"拉各斯"、"曼谷"），从 known_provinces 中返回对应名称
4. 若 known_provinces 为 null，按常识判断省份英文名并直接返回
5. 若确实无法判断所在省级行政区，或 location 仅为国家名，返回 null

输出要求：
- 若 known_provinces 不为 null，输出的 province 必须是 known_provinces 列表中的某个值，或 null
- 不得自行创造列表外的名称

输入（JSON 数组）：
{items_json}

只输出如下格式的 JSON，不要有其他文字：
{{"results": [{{"id": "项目ID", "province": "英文省份名或null"}}]}}"""

        success = False
        batch_by_id = {str(item["project_id"]): item for item in batch}
        last_error = ""
        for attempt in range(1, 4):  # 最多重试 3 次
            try:
                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0,
                    ),
                )
                if not isinstance(response.text, str):
                    raise ValueError("省份模型未返回JSON文本")
                parsed = json.loads(response.text)
                if not isinstance(parsed, dict) or not isinstance(parsed.get("results"), list):
                    raise ValueError("省份响应须包含results数组")
                staged = {}
                for item in parsed["results"]:
                    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or "province" not in item:
                        raise ValueError("省份响应项须含字符串id及province")
                    pid, province = item["id"], item["province"]
                    if pid not in batch_by_id or pid in staged:
                        raise ValueError(f"省份模型返回未知或重复项目 ID: {pid}")
                    if province is not None and not isinstance(province, str):
                        raise ValueError(f"省份须为字符串或null: {pid}")
                    staged[pid] = province
                if set(staged) != set(batch_by_id):
                    raise ValueError(f"省份响应缺少项目ID: {sorted(set(batch_by_id) - set(staged))}")
                # Commit to the cache only after the complete batch passes validation.
                # A failed partial response must not poison later retries with nulls.
                for pid, province in staged.items():
                    result[pid] = province
                    cache[pid] = province
                    location_key = _cache_location_key(batch_by_id[pid])
                    cache[location_key] = province
                    cache[_cache_fingerprint_key(pid)] = location_key
                success = True
                break
            except (ValueError, RuntimeError, OSError, errors.APIError, httpx.TransportError) as e:
                last_error = f"{type(e).__name__}: {e}"
                print(f"  [警告] 批次 {batch_num} 第 {attempt} 次尝试失败: {e}")
                if attempt < 3:
                    time.sleep(2 ** attempt)  # 指数退避：2s, 4s

        if not success:
            if diagnostics is not None:
                ids = [str(rec["project_id"]) for rec in batch]
                diagnostics["failed_ids"].extend(ids)
                diagnostics["failures"].append({"project_ids": ids, "attempts": 3, "error": last_error})
            print(f"  [错误] 批次 {batch_num} 3 次重试均失败，该批次省份设为 null（下次运行可重试）")
            for rec in batch:
                result[rec["project_id"]] = None
                # 不写入缓存，下次运行时重试

        time.sleep(0.3)  # 避免触发限速

    _save_cache(cache_path, cache)
    return result
