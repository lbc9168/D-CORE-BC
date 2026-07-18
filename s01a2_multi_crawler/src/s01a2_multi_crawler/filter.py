# filter.py：文章初筛三段漏斗——结构清洗(去重+噪音规则) → 新能源关键词门 → LLM 三轴判定(海外/中企/项目事件)。
# 来源无关：只读 articles 表的 (url, site, title, content_text)，新来源（新站点/RSS 公众号 Markdown）只需
# 把文章落进 articles 表即可被筛；config/filter.yaml 里没配按站规则的来源自动只走通用规则。
import argparse
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from .state import DB_PATH, connect

logger = logging.getLogger(__name__)

FILTER_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "filter.yaml"


# ---------- 配置 ----------

class LLMSettings(BaseModel):
    """LLM 判定阶段的参数（模型名/批大小/正文截断长度/重试）"""
    model: str = "gemini-2.5-flash-lite"
    batch_size: int = Field(default=10, ge=1, le=50)
    content_chars: int = Field(default=800, ge=100)
    max_retries: int = Field(default=3, ge=1)
    backoff_base: float = 2.0


class FilterRules(BaseModel):
    """初筛规则配置（config/filter.yaml）：关键词表 + 按来源噪音规则 + LLM 参数"""
    re_keywords: list[str] = Field(min_length=1)
    per_site_noise: dict[str, list[str]] = Field(default_factory=dict)  # site → 标题子串黑名单；未配置的来源自动跳过
    llm: LLMSettings = Field(default_factory=LLMSettings)


def load_filter_rules(path: Path = FILTER_CONFIG_PATH) -> FilterRules:
    """加载并校验初筛配置，非法即抛"""
    with open(path, encoding="utf-8") as f:
        return FilterRules(**yaml.safe_load(f))


# ---------- DB 读写 ----------

def unfiltered_articles(conn: sqlite3.Connection) -> list[dict]:
    """返回尚未筛过的文章（不含抓取失败行），供漏斗处理"""
    rows = conn.execute(
        "SELECT a.url, a.site, a.title, a.publish_date, a.content_text, a.content_hash FROM articles a"
        " LEFT JOIN article_filter f ON a.url = f.url"
        " WHERE f.url IS NULL AND a.status != 'error' ORDER BY a.url"
    ).fetchall()
    cols = ["url", "site", "title", "publish_date", "content_text", "content_hash"]
    return [dict(zip(cols, r)) for r in rows]


def save_verdicts(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """批量写入筛选结果（单事务，幂等）；row 至少含 url/verdict/stage"""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data = [
        (r["url"], r["verdict"], r["stage"], r.get("reason"), r.get("location"), r.get("chinese_entity"),
         r.get("is_project_event"), r.get("energy_type"), r.get("country"), r.get("model"), now)
        for r in rows
    ]
    with conn:
        cur = conn.executemany(
            "INSERT OR REPLACE INTO article_filter(url, verdict, stage, reason, location, chinese_entity,"
            " is_project_event, energy_type, country, model, filtered_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            data,
        )
    return cur.rowcount


# ---------- Stage 0：结构清洗 ----------

def stage_structural(conn: sqlite3.Connection, articles: list[dict], rules: FilterRules) -> tuple[list[dict], list[dict]]:
    """结构清洗：跨条目正文去重（保留每组首条）+ 按来源标题噪音规则；返回 (存活, 淘汰verdicts)"""
    # 全表统计非空正文 hash 的首条（canonical），保证增量运行时 canonical 稳定
    canonical: dict[str, str] = {}
    for url, h in conn.execute(
        "SELECT url, content_hash FROM articles WHERE content_text IS NOT NULL AND length(content_text) > 50"
        " ORDER BY publish_date, url"
    ):
        canonical.setdefault(h, url)

    survivors, killed = [], []
    for a in articles:
        noise = _match_noise(a, rules)
        if noise:
            killed.append({"url": a["url"], "verdict": "rejected", "stage": "structural", "reason": f"noise:{noise}"})
            continue
        h = a["content_hash"]
        if a["content_text"] and len(a["content_text"]) > 50 and canonical.get(h) != a["url"]:
            killed.append({"url": a["url"], "verdict": "rejected", "stage": "structural",
                           "reason": f"duplicate_of:{canonical[h]}"})
            continue
        survivors.append(a)
    return survivors, killed


def _match_noise(article: dict, rules: FilterRules) -> str | None:
    """标题命中该来源噪音黑名单则返回命中的子串，否则 None；来源未配置规则时不匹配"""
    title = article.get("title") or ""
    for pat in rules.per_site_noise.get(article["site"], []):
        if pat in title:
            return pat
    return None


# ---------- Stage 1：关键词门 ----------

def stage_keyword(articles: list[dict], rules: FilterRules) -> tuple[list[dict], list[dict]]:
    """新能源关键词门：标题+正文命中任一关键词才存活；返回 (存活, 淘汰verdicts)"""
    survivors, killed = [], []
    for a in articles:
        text = (a.get("title") or "") + " " + (a.get("content_text") or "")
        if any(k in text for k in rules.re_keywords):
            survivors.append(a)
        else:
            killed.append({"url": a["url"], "verdict": "rejected", "stage": "keyword", "reason": "no_re_keyword"})
    return survivors, killed


# ---------- Stage 2：LLM 三轴判定 ----------

class LLMVerdict(BaseModel):
    """LLM 对单篇文章的三轴判定结果（运行时结构校验，非法即弃）"""
    id: int
    location: Literal["overseas", "domestic", "unknown"]
    chinese_entity: Literal["yes", "no", "unknown"]
    is_project_event: bool
    energy_type: Literal["PV", "WD", "HY", "GT", "BM", "ES", "HB", "OTHER_RE", "NOT_RE"]
    country: str | None = None
    reason: str = ""


PROMPT_HEADER = """你是"一带一路"海外可再生能源项目数据库的新闻初筛员。数据库只收录：中国企业/机构参与的、位于中国大陆以外的、可再生能源具体项目的进展事件（签约、中标、开工、融资、设备交付、并网、投产、收购等）。

下面是若干篇新闻（编号 id、标题、正文开头；部分只有标题，按标题判断）。对每篇独立判断，输出 JSON 数组，每篇一个对象，字段：
- id: 原样返回（整数）
- location: "overseas"（项目在中国大陆以外，含港澳台）/ "domestic"（项目在中国大陆）/ "unknown"
- chinese_entity: "yes"（明确有中国企业/机构参与该项目）/ "no"（明确是纯外方项目，无中方参与）/ "unknown"
- is_project_event: true（围绕一个具体项目的进展事件）/ false（行业统计、价格行情、政策解读、企业战略、展会会议、综述评论等非具体项目新闻）
- energy_type: "PV"光伏/"WD"风电/"GT"地热/"BM"生物质/"ES"储能/"HY"水电/"HB"混合（多种可再生打捆）/"OTHER_RE"（其他可再生形态，如绿氢、光热、海洋能）/"NOT_RE"（纯化石能源、纯核电、输电线路、或与能源项目无关）
- country: 项目所在国家中文名；判不出为 null
- reason: 一句话理由（15 字以内）

注意：判断对象是"新闻里的项目"，不是新闻发布方——中国企业在海外承建的项目 location 是 overseas。只输出 JSON 数组，不要其他文字。

新闻列表：
"""


def build_prompt(batch: list[dict], content_chars: int) -> str:
    """把一批文章拼成判定 prompt（标题 + 截断正文）"""
    lines = [PROMPT_HEADER]
    for i, a in enumerate(batch):
        body = (a.get("content_text") or "").replace("\n", " ").strip()[:content_chars]
        lines.append(f"[id={i}] 标题：{a.get('title') or '(无标题)'}")
        lines.append(f"正文：{body}" if body else "正文：(仅有标题)")
        lines.append("")
    return "\n".join(lines)


class GeminiFilterClient:
    """精简 Gemini 调用器：json_mode + 指数退避重试；全失败抛 RuntimeError（调用方跳过该批，下轮增量重试）"""

    def __init__(self, settings: LLMSettings, _client=None):
        self.settings = settings
        if _client is not None:
            self._client = _client
        else:
            api_key = os.getenv("GEMINI_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("缺少 GEMINI_API_KEY（请检查 .env）")
            from google import genai
            self._client = genai.Client(api_key=api_key)

    def judge(self, prompt: str) -> list[dict]:
        """跑一次批判定，返回解析后的 JSON 数组；重试耗尽抛 RuntimeError"""
        from google.genai import errors as genai_errors, types
        last = ""
        for attempt in range(1, self.settings.max_retries + 1):
            try:
                resp = self._client.models.generate_content(
                    model=self.settings.model, contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.1, response_mime_type="application/json"),
                )
                data = json.loads(resp.text)
                if not isinstance(data, list):
                    raise ValueError(f"LLM 返回非数组: {type(data).__name__}")
                return data
            except (genai_errors.APIError, ValueError, TypeError, OSError, ConnectionError, TimeoutError) as e:
                last = f"{type(e).__name__}: {e}"
                logger.error("LLM 判定失败 attempt=%d/%d error=%s", attempt, self.settings.max_retries, last)
                if attempt < self.settings.max_retries:
                    time.sleep(self.settings.backoff_base ** attempt)
        raise RuntimeError(f"LLM 判定重试耗尽: {last}")


def decide(v: LLMVerdict) -> str:
    """三轴合成最终结论：仅当明确国内 / 明确无中企 / 非项目事件 / 明确非可再生时淘汰，判不出一律保留（召回优先）"""
    if v.location == "domestic" or v.chinese_entity == "no" or not v.is_project_event or v.energy_type == "NOT_RE":
        return "rejected"
    return "relevant"


def stage_llm(articles: list[dict], rules: FilterRules, client: GeminiFilterClient,
              save=None) -> tuple[list[dict], int]:
    """LLM 批判定：按 batch_size 分批调用，校验每条输出并合成 verdict；save 回调每批判完立即落库
    （中途崩溃不丢已判进度，重跑自动断点续）；返回 (verdicts, 失败批的文章数)"""
    verdicts, failed = [], 0
    bs = rules.llm.batch_size
    for start in range(0, len(articles), bs):
        batch = articles[start:start + bs]
        try:
            raw = client.judge(build_prompt(batch, rules.llm.content_chars))
        except RuntimeError as e:
            logger.error("批次 %d-%d 判定失败，留待下轮重试: %s", start, start + len(batch) - 1, e)
            failed += len(batch)
            continue
        by_id = {}
        for item in raw:
            try:
                v = LLMVerdict(**item)
                by_id[v.id] = v
            except (ValidationError, TypeError) as e:
                logger.error("LLM 输出条目校验失败（弃该条，留待下轮）: %s item=%s", e, item)
        batch_verdicts = []
        for i, a in enumerate(batch):
            v = by_id.get(i)
            if v is None:
                failed += 1
                continue
            batch_verdicts.append({
                "url": a["url"], "verdict": decide(v), "stage": "llm", "reason": v.reason,
                "location": v.location, "chinese_entity": v.chinese_entity,
                "is_project_event": int(v.is_project_event), "energy_type": v.energy_type,
                "country": v.country, "model": rules.llm.model,
            })
        if save and batch_verdicts:
            save(batch_verdicts)
        verdicts.extend(batch_verdicts)
        done = min(start + bs, len(articles))
        logger.info("LLM 判定进度 %d/%d", done, len(articles))
        print(f"  LLM 判定 {done}/{len(articles)}")
    return verdicts, failed


# ---------- CLI ----------

def run_filter(conn: sqlite3.Connection, rules: FilterRules, limit: int | None = None,
               dry_run: bool = False, client: GeminiFilterClient | None = None) -> dict:
    """完整漏斗：取未筛文章 → 结构清洗 → 关键词门 → LLM 判定 → 落库；返回漏斗统计"""
    articles = unfiltered_articles(conn)
    if limit:
        articles = articles[:limit]
    s0_alive, s0_killed = stage_structural(conn, articles, rules)
    s1_alive, s1_killed = stage_keyword(s0_alive, rules)
    stats = {"total": len(articles), "structural_killed": len(s0_killed), "keyword_killed": len(s1_killed),
             "llm_input": len(s1_alive), "llm_relevant": 0, "llm_rejected": 0, "llm_failed": 0}
    if dry_run:
        return stats
    save_verdicts(conn, s0_killed + s1_killed)
    if s1_alive:
        llm_verdicts, failed = stage_llm(s1_alive, rules, client or GeminiFilterClient(rules.llm),
                                         save=lambda vs: save_verdicts(conn, vs))
        stats["llm_relevant"] = sum(1 for v in llm_verdicts if v["verdict"] == "relevant")
        stats["llm_rejected"] = sum(1 for v in llm_verdicts if v["verdict"] == "rejected")
        stats["llm_failed"] = failed
    return stats


def main_filter() -> None:
    """filter 命令：对 crawl_state.db 里未筛过的文章跑三段漏斗（增量、幂等）；--dry-run 只报漏斗不调 LLM 不写库"""
    parser = argparse.ArgumentParser(description="s01a2 文章初筛（Python 粗杀 + LLM 三轴判定）")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 篇未筛文章（试跑用）")
    parser.add_argument("--dry-run", action="store_true", help="只统计各段漏斗，不调 LLM 不写库")
    args = parser.parse_args()

    from .main import _setup_logging
    _setup_logging(prefix="filter")
    try:
        from dotenv import load_dotenv
        for env in (Path(__file__).resolve().parents[2] / ".env", Path(__file__).resolve().parents[3] / ".env"):
            if env.is_file():
                load_dotenv(env)
                break
    except ImportError:  # 无害边界：无 dotenv 时依赖外部已 export 的环境变量
        pass

    rules = load_filter_rules()
    conn = connect()
    logger.info("初筛开始 limit=%s dry_run=%s model=%s", args.limit, args.dry_run, rules.llm.model)
    stats = run_filter(conn, rules, limit=args.limit, dry_run=args.dry_run)
    conn.close()
    logger.info("初筛完成 %s", stats)
    print(f"未筛文章 {stats['total']} → 结构清洗杀 {stats['structural_killed']}"
          f" → 关键词门杀 {stats['keyword_killed']} → 进 LLM {stats['llm_input']}"
          + ("（dry-run 到此为止）" if args.dry_run else
         f" → 保留 {stats['llm_relevant']} / 淘汰 {stats['llm_rejected']} / 失败待重试 {stats['llm_failed']}"))
