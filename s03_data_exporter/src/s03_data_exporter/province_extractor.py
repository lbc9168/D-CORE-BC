"""province_extractor.py
从 canonical_projects.location 字段中提取一级行政区（省/州/邦等）英文名。

处理逻辑：
- location 为空 → 直接返回 None，不调用 API
- 已有缓存 → 从 province_cache.json 读取，不重复调用
- 其余 → 批量送 Gemini，结果写回缓存
"""

import json
import time
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types

GEMINI_MODEL = "gemini-2.5-flash-lite"
BATCH_SIZE = 30  # 每次 API 调用处理的项目数


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
) -> dict[str, Optional[str]]:
    """
    批量提取省份，返回 {project_id: province_en_or_None}。

    Args:
        records:    [{"project_id": ..., "location": ..., "country": ...}, ...]
        cache_path: province_cache.json 路径（运行时自动创建）
        api_key:    Gemini API Key
    """
    cache = _load_cache(cache_path)
    result: dict[str, Optional[str]] = {}
    to_process: list[dict] = []

    for rec in records:
        pid = rec["project_id"]
        loc = (rec.get("location") or "").strip()
        if not loc:
            result[pid] = None
        elif pid in cache:
            result[pid] = cache[pid]
        else:
            to_process.append(rec)

    if not to_process:
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
                }
                for r in batch
            ],
            ensure_ascii=False,
        )

        prompt = f"""你是地理信息专家。对于以下每个项目，从 location 字段中提取所在地的一级行政区（省/州/邦/大区等）的标准英文名称。

规则：
1. 若 location 中有明确的省/州/邦名称，提取其国际通用英文名（如"旁遮普省" → "Punjab"，"西爪哇省" → "West Java"）
2. 若 location 只包含国家名或城市名、无省级信息，返回 null
3. 若 location 为空或无法判断，返回 null
4. 直辖市/首都地区（如"内罗毕"、"拉各斯"）可直接返回其英文名

输入（JSON 数组）：
{items_json}

只输出如下格式的 JSON，不要有其他文字：
{{"results": [{{"id": "项目ID", "province": "英文省份名或null"}}]}}"""

        success = False
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
                parsed = json.loads(response.text)
                for item in parsed.get("results", []):
                    pid = item["id"]
                    province = item.get("province")  # None if null
                    result[pid] = province
                    cache[pid] = province
                success = True
                break
            except Exception as e:
                print(f"  [警告] 批次 {batch_num} 第 {attempt} 次尝试失败: {e}")
                if attempt < 3:
                    time.sleep(2 ** attempt)  # 指数退避：2s, 4s

        if not success:
            print(f"  [错误] 批次 {batch_num} 3 次重试均失败，该批次省份设为 null（下次运行可重试）")
            for rec in batch:
                result[rec["project_id"]] = None
                # 不写入缓存，下次运行时重试

        time.sleep(0.3)  # 避免触发限速

    _save_cache(cache_path, cache)
    return result
