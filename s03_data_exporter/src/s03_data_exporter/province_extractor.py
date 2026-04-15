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
import time
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types

from s03_data_exporter.admin1_lookup import build_admin1_dict

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
        records:    [{"project_id": ..., "location": ..., "country": ..., "country_iso": ...}, ...]
        cache_path: province_cache.json 路径（运行时自动创建）
        api_key:    Gemini API Key
    """
    # 加载 Natural Earth 省份约束表（模块级单例，首次调用时读取 zip）
    admin1_dict = build_admin1_dict()

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

    # SKIP_PROVINCE_API=1 时跳过所有 API 调用，未缓存项目省份设为 null
    if os.environ.get("SKIP_PROVINCE_API") == "1":
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
