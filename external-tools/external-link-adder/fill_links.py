#!/usr/bin/env python3
"""
fill_links.py
为 projects_by_source.xlsx 中的空白行自动搜索并填充 official_website / non_official_website。

处理逻辑：
  - 跳过条件：official_website 或 non_official_website 任意一个已有值
  - 第1次搜索（有 implementing_entity）："{implementing_entity} {project_name}"
  - 第2次搜索（第1次无结果）："{project_name} {country}"
  - 用 Gemini 判断搜索结果中的链接类型（official / non-official）

多线程：
  - ThreadPoolExecutor 并发处理多行，默认 4 个 worker
  - SerperRateLimiter 全局限速（≤ 2次/秒），跨线程共享
  - DataFrame / 日志 / 保存 / 统计均通过 Lock 保护

运行方式：
  uv run --with pandas --with openpyxl --with google-genai --with python-dotenv --with tqdm --with requests fill_links.py
  uv run ... fill_links.py --workers 6          # 调整并发数
  uv run ... fill_links.py --limit 10           # 仅处理前10条（测试用）
  uv run ... fill_links.py --dry-run            # 不保存，仅打印
"""

import argparse
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types
from tqdm import tqdm

# ── 路径 ─────────────────────────────────────────────────────────────────────
_HERE        = Path(__file__).resolve().parent
INPUT_PATH   = _HERE / "projects_by_source.xlsx"
OUTPUT_PATH  = _HERE / "projects_by_source_filled.xlsx"
LOG_PATH     = _HERE / "search_log.txt"

# ── 加载 .env（优先当前目录，然后沿目录树向上查找）──────────────────────────
for _p in [_HERE, *_HERE.parents]:
    _env_file = _p / ".env"
    if _env_file.exists():
        load_dotenv(_env_file, override=False)   # 不覆盖已加载的值

# ── 常量 ──────────────────────────────────────────────────────────────────────
SERPER_URL  = "https://google.serper.dev/search"
MAX_RESULTS = 5      # 取搜索结果前5条
SAVE_EVERY  = 50     # 每处理50条保存一次


# ── 工具函数 ──────────────────────────────────────────────────────────────────
def is_empty(val) -> bool:
    """判断值是否视为"空"（NULL / 空字符串 / Unknown / NaN 等）。"""
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    s = str(val).strip()
    return s == "" or s.lower() in {"unknown", "nan", "none", "n/a", "na", "-", "–"}


def safe_str(val) -> str | None:
    """将值转为字符串并 strip；若为空则返回 None。"""
    if is_empty(val):
        return None
    return str(val).strip()


def should_skip(row: pd.Series) -> bool:
    """任意一列已有值则跳过该行。"""
    return (
        not is_empty(row.get("official_website"))
        or not is_empty(row.get("non_official_website"))
    )


# ── Serper 速率限制器 ─────────────────────────────────────────────────────────
class SerperRateLimiter:
    """
    全局 Serper 限速器（跨线程共享）。
    acquire() 会阻塞直到距上次调用已满 interval 秒，确保 ≤ calls_per_second。
    """
    def __init__(self, calls_per_second: float = 2.0):
        self._interval = 1.0 / calls_per_second
        self._lock = threading.Lock()
        self._last_call = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()


# ── Serper 搜索 ───────────────────────────────────────────────────────────────
def serper_search(query: str, api_key: str, rate_limiter: SerperRateLimiter) -> list[dict]:
    """
    调用 Serper API 执行 Google 搜索，返回 organic results 列表。
    每条结果包含 title / link / snippet。
    调用前通过 rate_limiter.acquire() 全局限速。
    """
    rate_limiter.acquire()
    try:
        resp = requests.post(
            SERPER_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": MAX_RESULTS},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        organic = data.get("organic", [])
        return [
            {
                "title"  : r.get("title", ""),
                "link"   : r.get("link", ""),
                "snippet": r.get("snippet", ""),
            }
            for r in organic[:MAX_RESULTS]
        ]
    except Exception:
        return []


# ── Gemini 判断 ───────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
你是一个可再生能源项目数据库专家，负责为项目找到最权威的网络链接。

请分析搜索结果列表，判断哪些链接属于：
- official（官方）：URL 域名归属于 implementing_entity 的企业官网，或项目专属官网
- non-official（非官方可信）：行业媒体、政府公告、国际机构报告、学术机构等第三方来源

判断规则：
1. official 优先级高于 non-official
2. 若存在 official 链接，只填 official_website，non_official_website 置为 null
3. 若仅有 non-official 链接，只填 non_official_website，official_website 置为 null
4. 新闻聚合站（如 Yahoo News、Google News）、维基百科、社交媒体（微博、Twitter、LinkedIn 主页）
   不算可信来源，两个字段都置为 null
5. 如果搜索结果与该项目完全无关，两个字段都置为 null

只返回 JSON，格式：
{"official_website": "完整URL 或 null", "non_official_website": "完整URL 或 null", "reason": "简短中文说明"}
"""


def gemini_classify(
    project_name: str,
    country: str,
    implementing_entity: str,
    search_results: list[dict],
    client,
) -> dict:
    """
    调用 Gemini 2.5 Flash Lite 判断搜索结果中的链接类型。
    返回 {"official_website": url|None, "non_official_website": url|None, "reason": str}
    Gemini 客户端线程安全，可多线程并发调用。
    """
    results_text = "\n".join(
        f"{i+1}. 标题: {r['title']}\n   链接: {r['link']}\n   摘要: {r['snippet']}"
        for i, r in enumerate(search_results)
    )

    user_prompt = (
        f"项目名称: {project_name or 'N/A'}\n"
        f"国家: {country or 'N/A'}\n"
        f"中方实施企业: {implementing_entity or 'N/A'}\n\n"
        f"搜索结果：\n{results_text}"
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part(text=_SYSTEM_PROMPT + "\n\n" + user_prompt)],
                )
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        result = json.loads(response.text)
        official     = result.get("official_website")
        non_official = result.get("non_official_website")
        reason       = result.get("reason", "")

        # 过滤 "null" 字符串
        if isinstance(official, str) and official.lower() in {"null", "none", ""}:
            official = None
        if isinstance(non_official, str) and non_official.lower() in {"null", "none", ""}:
            non_official = None

        return {
            "official_website"    : official,
            "non_official_website": non_official,
            "reason"              : reason,
        }
    except Exception as e:
        return {
            "official_website"    : None,
            "non_official_website": None,
            "reason"              : f"Gemini错误: {e}",
        }


# ── 日志 ──────────────────────────────────────────────────────────────────────
def init_log(log_path: Path) -> None:
    """初始化日志文件，写入 TSV 表头（若文件不存在）。"""
    if not log_path.exists():
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\t".join([
                "row_num", "project_name", "country", "implementing_entity",
                "query1", "query2",
                "official_website", "non_official_website",
                "reason", "status",
            ]) + "\n")


def write_log(
    log_path: Path,
    log_lock: threading.Lock,
    row_num: int,
    project_name: str,
    country: str,
    implementing_entity: str,
    query1: str,
    query2: str,
    result: dict,
    status: str,
) -> None:
    """线程安全地追加一条日志记录。"""
    line = "\t".join([
        str(row_num),
        (project_name or "")[:80],
        country or "",
        implementing_entity or "",
        query1 or "",
        query2 or "",
        result.get("official_website") or "",
        result.get("non_official_website") or "",
        (result.get("reason") or "")[:200],
        status,
    ]) + "\n"
    with log_lock:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)


# ── 单行处理函数（在线程中执行）──────────────────────────────────────────────
def process_row(
    idx: int,
    row: pd.Series,
    serper_key: str,
    client,
    rate_limiter: SerperRateLimiter,
) -> dict:
    """
    处理单行记录，返回处理结果字典。
    此函数在 worker 线程中执行，不访问共享可变状态。
    """
    project_name        = safe_str(row.get("project_name")) or ""
    country             = safe_str(row.get("country")) or ""
    implementing_entity = safe_str(row.get("implementing_entity")) or ""
    row_num             = idx + 2   # Excel 行号（含表头）

    result = {"official_website": None, "non_official_website": None, "reason": ""}
    query1 = ""
    query2 = ""
    status = "not_found"

    try:
        # ── 第1次搜索 ──────────────────────────────────────────────────────
        if implementing_entity:
            query1 = f"{implementing_entity} {project_name}"
            hits = serper_search(query1, serper_key, rate_limiter)
            if hits:
                result = gemini_classify(
                    project_name, country, implementing_entity, hits, client
                )

            if result["official_website"] or result["non_official_website"]:
                status = "search1_found"

        # ── 第2次搜索（第1次未找到时）─────────────────────────────────────
        if not result["official_website"] and not result["non_official_website"]:
            query2 = f"{project_name} {country}"
            hits2 = serper_search(query2, serper_key, rate_limiter)
            if hits2:
                result = gemini_classify(
                    project_name, country, implementing_entity, hits2, client
                )

            if result["official_website"] or result["non_official_website"]:
                status = "search2_found"
            else:
                status = "not_found"

    except Exception as e:
        result["reason"] = f"处理异常: {e}"
        status = "error"

    return {
        "idx"                : idx,
        "row_num"            : row_num,
        "project_name"       : project_name,
        "country"            : country,
        "implementing_entity": implementing_entity,
        "query1"             : query1,
        "query2"             : query2,
        "result"             : result,
        "status"             : status,
    }


# ── 主流程 ────────────────────────────────────────────────────────────────────
def main(dry_run: bool = False, limit: int | None = None, workers: int = 4) -> None:
    # 检查 API keys
    serper_key = os.getenv("SERPER_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not serper_key:
        raise EnvironmentError("未找到 SERPER_API_KEY，请检查 .env 文件。")
    if not gemini_key:
        raise EnvironmentError("未找到 GEMINI_API_KEY，请检查 .env 文件。")

    # 初始化共享资源
    client       = genai.Client(api_key=gemini_key)
    rate_limiter = SerperRateLimiter(calls_per_second=2.0)
    df_lock      = threading.Lock()
    log_lock     = threading.Lock()
    save_lock    = threading.Lock()
    stats_lock   = threading.Lock()
    print(f"[Gemini] 客户端已初始化，并发 workers={workers}")

    # 读取输入 Excel
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"输入文件不存在: {INPUT_PATH}")
    print(f"[读取] {INPUT_PATH}")
    df = pd.read_excel(INPUT_PATH, dtype=str)
    print(f"[读取] 共 {len(df)} 行，{len(df.columns)} 列")

    # 断点续传：若输出文件已存在，将其中已填写的链接合并回 df
    if OUTPUT_PATH.exists():
        print("[断点续传] 检测到已有输出文件，正在合并进度...")
        df_prev = pd.read_excel(OUTPUT_PATH, dtype=str)
        merged_count = 0
        for col in ("official_website", "non_official_website"):
            if col in df_prev.columns:
                mask = df_prev[col].apply(lambda v: not is_empty(v))
                df.loc[mask, col] = df_prev.loc[mask, col]
                merged_count = max(merged_count, int(mask.sum()))
        print(f"[断点续传] 已恢复约 {merged_count} 条已处理记录")

    # 初始化日志
    init_log(LOG_PATH)

    # 确保目标列存在
    for col in ("official_website", "non_official_website"):
        if col not in df.columns:
            df[col] = ""

    # 确定需要处理的行（跳过已有值的行）
    total_rows    = len(df)
    cap           = min(limit, total_rows) if limit else total_rows
    pending_idxs  = [i for i in range(cap) if not should_skip(df.iloc[i])]
    skipped_count = cap - len(pending_idxs)

    print(f"\n[开始] 共 {cap} 行，跳过 {skipped_count} 行（已有值），待处理 {len(pending_idxs)} 行")
    if dry_run:
        print("[DRY-RUN 模式] 不会保存文件")

    # 统计计数器
    stats = {"search1": 0, "search2": 0, "not_found": 0, "error": 0, "processed": 0}

    with tqdm(total=len(pending_idxs), unit="行") as pbar:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    process_row,
                    i,
                    df.iloc[i].copy(),   # 传副本，避免线程读取时 df 被修改
                    serper_key,
                    client,
                    rate_limiter,
                ): i
                for i in pending_idxs
            }

            for future in as_completed(futures):
                res = future.result()
                idx    = res["idx"]
                status = res["status"]
                result = res["result"]

                # 更新统计
                with stats_lock:
                    stats["processed"] += 1
                    if status == "search1_found":
                        stats["search1"] += 1
                    elif status == "search2_found":
                        stats["search2"] += 1
                    elif status == "error":
                        stats["error"] += 1
                    else:
                        stats["not_found"] += 1
                    processed_count = stats["processed"]

                # 写回 DataFrame（线程安全）
                if not dry_run:
                    with df_lock:
                        df.at[idx, "official_website"]    = result["official_website"] or ""
                        df.at[idx, "non_official_website"] = result["non_official_website"] or ""

                # 写日志（线程安全）
                write_log(
                    LOG_PATH, log_lock,
                    res["row_num"], res["project_name"], res["country"],
                    res["implementing_entity"], res["query1"], res["query2"],
                    result, status,
                )

                # 定期保存（线程安全）
                if not dry_run and processed_count % SAVE_EVERY == 0:
                    with save_lock:
                        with df_lock:
                            df.to_excel(OUTPUT_PATH, index=False)
                        tqdm.write(f"  [保存] 已处理 {processed_count} 条，进度已保存")

                pbar.update(1)

    # 最终保存
    if not dry_run:
        with df_lock:
            df.to_excel(OUTPUT_PATH, index=False)
        print(f"\n[完成] 输出文件: {OUTPUT_PATH}")
    else:
        print("\n[DRY-RUN] 未保存文件")

    print(f"\n处理统计：")
    print(f"  已跳过（已有值）:      {skipped_count}")
    print(f"  第1次搜索找到链接:     {stats['search1']}")
    print(f"  第2次搜索找到链接:     {stats['search2']}")
    print(f"  两次均未找到:          {stats['not_found']}")
    print(f"  处理异常:              {stats['error']}")
    print(f"  日志文件:              {LOG_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="自动填充能源项目网站链接")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="不保存文件，仅打印处理过程（测试用）"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="仅处理前N条记录（测试用，如 --limit 10）"
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="并发 worker 数（默认4，建议4-8）"
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run, limit=args.limit, workers=args.workers)
