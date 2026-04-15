"""step2_s01b_to_s02a.py
将 s01b extracted_projects 中的 official_website / non_official_website
回填到 s02a project_events 的 official_website_links / non_official_website_links 列。

匹配逻辑：s01b.source_link ∈ s02a.project_events.source_links（JSON 数组）
         即：如果一条新闻 URL 出现在某个 project_events 的 source_links 数组里，
         就把该新闻对应的 website links 合并写入该 event。

运行方式（项目根目录）：
    python scripts/step2_s01b_to_s02a.py
依赖：仅标准库 sqlite3, json
"""

import json
import sqlite3
from pathlib import Path

ROOT  = Path(__file__).resolve().parent.parent
S01B_DB = ROOT / "s01b_project_extractor" / "data" / "projects.db"
S02A_DB = ROOT / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"


def _is_url(val: str) -> bool:
    return bool(val and val.strip().startswith("http"))


def main() -> None:
    # ── Step 1: 升级 s02a schema（已存在则跳过）───────────────────────────────
    conn2 = sqlite3.connect(S02A_DB)
    for col in ("official_website_links", "non_official_website_links"):
        try:
            conn2.execute(f"ALTER TABLE project_events ADD COLUMN {col} TEXT")
            print(f"[Step 1] 新增列 {col} ✓")
        except sqlite3.OperationalError:
            print(f"[Step 1] 列 {col} 已存在，跳过")
    conn2.commit()

    # ── Step 2: 从 s01b 建 source_link → website links 索引 ──────────────────
    conn1 = sqlite3.connect(S01B_DB)
    rows = conn1.execute(
        "SELECT source_link, official_website, non_official_website "
        "FROM extracted_projects "
        "WHERE source_link IS NOT NULL AND source_link != ''"
    ).fetchall()
    conn1.close()

    # {source_link: {"official": [url,...], "non_official": [url,...]}}
    link_index: dict[str, dict] = {}
    for src, official, non_official in rows:
        src = (src or "").strip()
        if not src:
            continue
        entry = link_index.setdefault(src, {"official": [], "non_official": []})
        if _is_url(official) and official not in entry["official"]:
            entry["official"].append(official.strip())
        if _is_url(non_official) and non_official not in entry["non_official"]:
            entry["non_official"].append(non_official.strip())

    # 只保留至少有一个链接的条目
    link_index = {k: v for k, v in link_index.items() if v["official"] or v["non_official"]}
    print(f"[Step 2] s01b 中有效 website link 记录：{len(link_index)} 条（source_link 维度）")

    # ── Step 3: 遍历 project_events，匹配并 UPDATE ────────────────────────────
    events = conn2.execute(
        "SELECT id, source_links FROM project_events"
    ).fetchall()
    print(f"[Step 3] 处理 {len(events)} 个 project_events...")

    updated = 0
    skipped = 0

    for event_id, source_links_raw in events:
        try:
            event_urls: list[str] = json.loads(source_links_raw) if source_links_raw else []
        except (json.JSONDecodeError, TypeError):
            event_urls = []

        # 收集匹配到的所有 website links（保序去重）
        seen_off:  set[str] = set()
        seen_noff: set[str] = set()
        official_links:     list[str] = []
        non_official_links: list[str] = []

        for url in event_urls:
            entry = link_index.get(url)
            if not entry:
                continue
            for link in entry["official"]:
                if link not in seen_off:
                    seen_off.add(link)
                    official_links.append(link)
            for link in entry["non_official"]:
                if link not in seen_noff:
                    seen_noff.add(link)
                    non_official_links.append(link)

        if not official_links and not non_official_links:
            skipped += 1
            continue

        conn2.execute(
            """UPDATE project_events
               SET official_website_links = ?, non_official_website_links = ?
               WHERE id = ?""",
            (
                json.dumps(official_links,     ensure_ascii=False),
                json.dumps(non_official_links, ensure_ascii=False),
                event_id,
            ),
        )
        updated += 1

    conn2.commit()
    conn2.close()

    print(f"\n[完成] 已更新 project_events：{updated} 行")
    print(f"       无匹配或无链接（跳过）：{skipped} 行")
    print("\n下一步：重跑 s03")
    print("  cd s03_data_exporter")
    print("  $env:SKIP_PROVINCE_API=\"1\"; uv run run_export")


if __name__ == "__main__":
    main()
