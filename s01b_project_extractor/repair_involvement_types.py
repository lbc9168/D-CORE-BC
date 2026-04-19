"""
将 s02a_duplicate_merger/data/db/bri_re_database.db 中 chinese_entities 表
的非法 involvement_type 值语义映射到合法枚举，一次性修复存量数据。

合法值：BOT/BOOT、EPC总承包、设计/施工服务方、股权投资方、设备供应商、投融资+EPC、融资方、Unknown

注意：直接修改 s02a 主库，修复后只需重跑 s03 即可更新网页，无需重跑 s02a。

用法（需 google-genai 和 python-dotenv）：
  cd s01b_project_extractor && uv run python repair_involvement_types.py
"""

import json
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

_HERE = Path(__file__).parent
load_dotenv(_HERE / ".env")
load_dotenv(_HERE.parent / ".env")

VALID_INVOLVEMENT_TYPES = {
    "BOT/BOOT", "EPC总承包", "设计/施工服务方", "股权投资方",
    "设备供应商", "投融资+EPC", "融资方", "Unknown",
}

# 修复目标：s02a 主库（网页数据的来源）
DB_PATH = _HERE.parent / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"
TABLE = "chinese_entities"


def _get_invalid_values(conn: sqlite3.Connection) -> dict[str, int]:
    """查询 chinese_entities 中所有非法 involvement_type 及出现次数。"""
    cur = conn.execute(
        f"SELECT involvement_type, COUNT(*) FROM {TABLE} GROUP BY involvement_type"
    )
    return {
        row[0]: row[1]
        for row in cur.fetchall()
        if row[0] and row[0].strip() not in VALID_INVOLVEMENT_TYPES
    }


def _map_via_gemini(invalid_values: list[str]) -> dict[str, str]:
    """调用 Gemini，将非法值语义映射到最近合法枚举。"""
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未找到 GEMINI_API_KEY，请检查 .env 文件")

    client = genai.Client(api_key=api_key)
    valid_list = "、".join(sorted(VALID_INVOLVEMENT_TYPES))
    values_text = "\n".join(f"- {v}" for v in invalid_values)

    prompt = f"""你是一个数据标准化专家。以下是从可再生能源项目数据库中提取的"中企参与方式"标签，它们不符合规范。
请将每个标签映射到语义最相近的合法值（必须从合法列表中选一个）。

合法值列表：{valid_list}

需要映射的标签：
{values_text}

映射规则（参考）：
- 业主/开发商/投资运营/股权收购 → 股权投资方
- EPC/总承包/施工/建设/承包/中标/签约 → EPC总承包
- 设计/施工服务 → 设计/施工服务方
- 设备/供货/供应/制造 → 设备供应商
- 融资/贷款/金融机构 → 融资方
- 投融资+EPC组合 → 投融资+EPC
- BOT/BOOT特许经营 → BOT/BOOT
- 无法判断/其他 → Unknown

请以 JSON dict 返回，key 为原标签，value 为合法值，例如：
{{"原标签1": "合法值1", "原标签2": "合法值2"}}
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )

    mapping: dict = json.loads(response.text.strip())
    result = {}
    for orig, suggested in mapping.items():
        if suggested in VALID_INVOLVEMENT_TYPES:
            result[orig] = suggested
        else:
            print(f"  [警告] Gemini 建议了非法值 {repr(suggested)}，改为 Unknown")
            result[orig] = "Unknown"
    return result


def _apply_mapping(conn: sqlite3.Connection, mapping: dict[str, str]) -> int:
    """批量更新 chinese_entities，返回受影响行数。"""
    total = 0
    for old_val, new_val in mapping.items():
        cur = conn.execute(
            f"UPDATE {TABLE} SET involvement_type = ? WHERE involvement_type = ?",
            (new_val, old_val),
        )
        total += cur.rowcount
    conn.commit()
    return total


def main() -> None:
    """修复 s02a 主库中非法的 involvement_type 值。"""
    if not DB_PATH.exists():
        print(f"[错误] 数据库不存在：{DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        invalid = _get_invalid_values(conn)
        if not invalid:
            print("✓ 所有 involvement_type 值均合法，无需修复。")
            return

        print(f"目标库：{DB_PATH}")
        print(f"发现 {len(invalid)} 个非法值（共 {sum(invalid.values())} 条记录）：")
        for val, cnt in sorted(invalid.items(), key=lambda x: -x[1]):
            print(f"  {repr(val)} × {cnt}")

        print(f"\n调用 Gemini 进行语义映射（{len(invalid)} 个值）…")
        mapping = _map_via_gemini(list(invalid.keys()))

        print("\n映射结果：")
        for old_val, new_val in sorted(mapping.items(), key=lambda x: -invalid.get(x[0], 0)):
            print(f"  {repr(old_val)} ({invalid.get(old_val, '?')}行) → {repr(new_val)}")

        updated = _apply_mapping(conn, mapping)
        print(f"\n✓ 已更新 {updated} 条记录。")
        print("  请重跑 s03_data_exporter 以更新 projects_flat.json 和网页。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
