"""cross_verification_tool.py
一带一路可再生能源数据库 — 外部信源交叉验证工具

四阶段处理：
1. GDELT 批量查询：按国家×能源类型拉取新闻标题，缓存到本地（零 token 消耗）
2. Gemini 多语言嵌入向量匹配：筛出候选项目-文章对
3. LLM 确认匹配关系（基于文章标题）
4. 写回 SQLite + 导出人工核查清单
"""

import json
import math
import os
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Type

import pandas as pd
import requests
from crewai.tools import BaseTool
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()

# ── 常量 ──────────────────────────────────────────────────────────────────────

GEMINI_MODEL    = "gemini-2.5-flash-lite"
EMBEDDING_MODEL = "text-embedding-004"
GDELT_API_URL   = "https://api.gdeltproject.org/api/v2/doc/doc"
NEWS_API_URL    = "https://newsapi.org/v2/everything"

# 能源类型 → GDELT 英文查询关键词（取首个作为主查询词）
CATEGORY_EN_TERMS: Dict[str, str] = {
    "光伏":     "solar photovoltaic",
    "风电":     "wind power",
    "水电":     "hydropower",
    "地热及余热": "geothermal",
    "生物质":   "biomass energy",
    "储能":     "battery energy storage",
    "综合能源": "renewable energy",
}

# ISO3 → 英文国名（常见 BRI 国家，用于构建 GDELT 查询）
ISO3_TO_EN: Dict[str, str] = {
    "PAK": "Pakistan",   "BGD": "Bangladesh", "LKA": "Sri Lanka",
    "NPL": "Nepal",      "MMR": "Myanmar",    "KHM": "Cambodia",
    "LAO": "Laos",       "VNM": "Vietnam",    "IDN": "Indonesia",
    "MYS": "Malaysia",   "PHL": "Philippines","THA": "Thailand",
    "SGP": "Singapore",  "BRN": "Brunei",     "TLS": "Timor-Leste",
    "ETH": "Ethiopia",   "KEN": "Kenya",      "TZA": "Tanzania",
    "UGA": "Uganda",     "MOZ": "Mozambique", "ZAF": "South Africa",
    "NGA": "Nigeria",    "GHA": "Ghana",      "COD": "DR Congo",
    "ZMB": "Zambia",     "ZWE": "Zimbabwe",   "SDN": "Sudan",
    "EGY": "Egypt",      "DZA": "Algeria",    "MAR": "Morocco",
    "TUN": "Tunisia",    "LBY": "Libya",      "CMR": "Cameroon",
    "AGO": "Angola",     "MDG": "Madagascar", "SEN": "Senegal",
    "CIV": "Ivory Coast","GNB": "Guinea-Bissau","MLI": "Mali",
    "BFA": "Burkina Faso","NER": "Niger",     "TCD": "Chad",
    "IRQ": "Iraq",       "IRN": "Iran",       "SAU": "Saudi Arabia",
    "ARE": "UAE",        "QAT": "Qatar",      "KWT": "Kuwait",
    "OMN": "Oman",       "YEM": "Yemen",      "JOR": "Jordan",
    "LBN": "Lebanon",    "SYR": "Syria",      "ISR": "Israel",
    "PSE": "Palestine",  "AFG": "Afghanistan","KAZ": "Kazakhstan",
    "UZB": "Uzbekistan", "TKM": "Turkmenistan","KGZ": "Kyrgyzstan",
    "TJK": "Tajikistan", "AZE": "Azerbaijan", "GEO": "Georgia",
    "ARM": "Armenia",    "RUS": "Russia",     "UKR": "Ukraine",
    "POL": "Poland",     "CZE": "Czech Republic","HUN": "Hungary",
    "ROU": "Romania",    "BGR": "Bulgaria",   "SRB": "Serbia",
    "HRV": "Croatia",    "BIH": "Bosnia",     "MKD": "Macedonia",
    "ALB": "Albania",    "MNE": "Montenegro", "GRC": "Greece",
    "PRT": "Portugal",   "ESP": "Spain",      "ITA": "Italy",
    "BRA": "Brazil",     "ARG": "Argentina",  "CHL": "Chile",
    "PER": "Peru",       "COL": "Colombia",   "VEN": "Venezuela",
    "ECU": "Ecuador",    "BOL": "Bolivia",    "PRY": "Paraguay",
    "URY": "Uruguay",    "GUY": "Guyana",     "SUR": "Suriname",
    "PAN": "Panama",     "CRI": "Costa Rica", "HND": "Honduras",
    "GTM": "Guatemala",  "MEX": "Mexico",     "CUB": "Cuba",
    "JAM": "Jamaica",    "TTO": "Trinidad",   "PNG": "Papua New Guinea",
    "FJI": "Fiji",       "WSM": "Samoa",      "TON": "Tonga",
    "IND": "India",      "CHN": "China",      "MNG": "Mongolia",
    "PRK": "North Korea","KOR": "South Korea","JPN": "Japan",
}


# ── 工具函数 ────────────────────────────────────────────────────────────────────

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """计算余弦相似度。"""
    dot   = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b) if norm_a * norm_b > 0 else 0.0


def _get_embedding(
    client: genai.Client,
    text: str,
    task_type: str = "RETRIEVAL_QUERY",
) -> Optional[List[float]]:
    """调用 Gemini text-embedding-004 获取多语言向量。"""
    try:
        result = client.models.embed_content(
            model=EMBEDDING_MODEL,
            content=text,
            config=types.EmbedContentConfig(task_type=task_type),
        )
        return list(result.embedding.values)
    except Exception as e:
        print(f"    [嵌入失败] {e}")
        return None


def _build_project_text(proj: Dict) -> str:
    """构建项目的向量查询文本（英文优先）。"""
    parts = []
    name = proj.get("project_name_en") or proj.get("project_name", "")
    if name:
        parts.append(name)
    country = ISO3_TO_EN.get(proj.get("country_iso", ""), proj.get("country", ""))
    if country:
        parts.append(country)
    cat_en = CATEGORY_EN_TERMS.get(proj.get("category", ""), "")
    if cat_en:
        parts.append(cat_en)
    entities = proj.get("entities", [])
    if entities:
        en = entities[0].get("implementing_entity_en") or entities[0].get("implementing_entity", "")
        if en:
            parts.append(en)
    return " ".join(parts)


def _build_article_text(art: Dict) -> str:
    """构建文章的向量文档文本。"""
    title  = art.get("title", "")
    domain = art.get("domain", "")
    return f"{title} {domain}".strip()


# ── 阶段一：从 SQLite 加载项目 ──────────────────────────────────────────────────

def _load_unverified_projects(conn: sqlite3.Connection) -> List[Dict]:
    """
    从数据库加载所有待验证项目事件（含关联的典型项目信息和中方实体）。
    只加载 verification_status = 'unverified' 的记录。
    """
    sql = """
        SELECT
            pe.id              AS event_id,
            cp.id              AS canonical_id,
            cp.project_name,
            cp.project_name_en,
            cp.country,
            cp.country_iso,
            cp.category,
            pe.project_stage,
            pe.capacity_mw,
            pe.total_investment_usd,
            pe.investment_date,
            pe.financing_model,
            pe.financing_model_details,
            pe.verification_status
        FROM project_events pe
        JOIN canonical_projects cp ON pe.canonical_id = cp.id
        WHERE pe.verification_status = 'unverified' OR pe.verification_status IS NULL
    """
    rows = conn.execute(sql).fetchall()
    col_names = [
        "event_id", "canonical_id", "project_name", "project_name_en",
        "country", "country_iso", "category", "project_stage", "capacity_mw",
        "total_investment_usd", "investment_date", "financing_model",
        "financing_model_details", "verification_status",
    ]
    projects = []
    for row in rows:
        proj = dict(zip(col_names, row))
        ents = conn.execute(
            "SELECT implementing_entity, implementing_entity_en FROM chinese_entities WHERE event_id = ?",
            (proj["event_id"],),
        ).fetchall()
        proj["entities"] = [
            {"implementing_entity": e[0], "implementing_entity_en": e[1]} for e in ents
        ]
        projects.append(proj)
    return projects


# ── 阶段二：GDELT 批量查询 & 本地缓存 ─────────────────────────────────────────

def _query_gdelt(query: str, months: int) -> List[Dict]:
    """查询 GDELT DOC API，返回文章列表。"""
    try:
        resp = requests.get(
            GDELT_API_URL,
            params={
                "query":      query,
                "mode":       "artlist",
                "maxrecords": 250,
                "format":     "json",
                "timespan":   f"{months}m",
                "sort":       "datedesc",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("articles", [])
    except Exception as e:
        print(f"    [GDELT 查询失败] {query[:60]}: {e}")
        return []


def _query_news_api(query: str, api_key: str, months: int) -> List[Dict]:
    """查询 NewsAPI，返回与 GDELT 格式兼容的文章列表。"""
    from datetime import datetime, timedelta
    date_from = (datetime.utcnow() - timedelta(days=30 * months)).strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            NEWS_API_URL,
            params={
                "q":        query,
                "from":     date_from,
                "language": "en",
                "sortBy":   "publishedAt",
                "pageSize": 100,
                "apiKey":   api_key,
            },
            timeout=30,
        )
        resp.raise_for_status()
        articles = []
        for a in resp.json().get("articles", []):
            articles.append({
                "title":     a.get("title", ""),
                "url":       a.get("url", ""),
                "seendate":  a.get("publishedAt", ""),
                "domain":    a.get("source", {}).get("name", ""),
                "language":  "English",
            })
        return articles
    except Exception as e:
        print(f"    [NewsAPI 查询失败] {query[:60]}: {e}")
        return []


def _build_gdelt_cache(
    projects: List[Dict],
    cache_dir: str,
    months: int,
    news_api_key: str,
) -> Dict[str, List[Dict]]:
    """
    按 country_iso × category 分组批量查询 GDELT（和可选的 NewsAPI），
    将结果合并缓存到 gdelt_articles.json。已缓存的分组直接跳过。
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, "gdelt_articles.json")

    cache: Dict[str, List[Dict]] = {}
    if os.path.exists(cache_file):
        with open(cache_file, encoding="utf-8") as f:
            cache = json.load(f)
        print(f"  已加载 GDELT 缓存，共 {len(cache)} 个分组")

    # 统计需要新查询的分组
    groups_needed: Dict[str, Tuple[str, str]] = {}
    for p in projects:
        iso = p.get("country_iso", "UNK")
        cat = p.get("category", "")
        key = f"{iso}_{cat}"
        if key not in cache:
            country_en = ISO3_TO_EN.get(iso, p.get("country", iso))
            groups_needed[key] = (country_en, cat)

    print(f"  需要新查询的分组：{len(groups_needed)} 个")
    for key, (country_en, cat) in groups_needed.items():
        cat_term = CATEGORY_EN_TERMS.get(cat, "renewable energy")
        query    = f'"{cat_term}" "{country_en}" China'
        print(f"    GDELT 查询 [{key}]: {query}")

        articles = _query_gdelt(query, months)
        if news_api_key:
            articles += _query_news_api(query, news_api_key, months)

        # 去重（按 URL）
        seen_urls: set = set()
        deduped: List[Dict] = []
        for a in articles:
            url = a.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                deduped.append(a)

        cache[key] = deduped
        print(f"      → 获取 {len(deduped)} 篇（去重后）")
        time.sleep(5)  # GDELT 建议 ≥5 秒间隔

    if groups_needed:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    return cache


# ── 阶段三：向量嵌入 & 候选匹配 ───────────────────────────────────────────────

def _load_or_build_article_embeddings(
    client: genai.Client,
    article_cache: Dict[str, List[Dict]],
    cache_dir: str,
) -> Dict[str, List[Tuple[Dict, List[float]]]]:
    """
    为每个分组中的文章生成嵌入向量（使用 RETRIEVAL_DOCUMENT 任务类型）。
    嵌入结果缓存到 article_embeddings.json，避免重复计算。
    返回：{group_key: [(article_dict, embedding_vector), ...]}
    """
    emb_cache_file = os.path.join(cache_dir, "article_embeddings.json")

    # 加载已有嵌入缓存（格式：{key: [[title, url, emb_vector], ...]}）
    raw_cache: Dict[str, List] = {}
    if os.path.exists(emb_cache_file):
        with open(emb_cache_file, encoding="utf-8") as f:
            raw_cache = json.load(f)
        print(f"  已加载文章嵌入缓存，共 {len(raw_cache)} 个分组")

    result: Dict[str, List[Tuple[Dict, List[float]]]] = {}
    cache_updated = False

    for key, articles in article_cache.items():
        cached = {item[1]: item[2] for item in raw_cache.get(key, [])}  # url → vector
        embedded: List[Tuple[Dict, List[float]]] = []
        new_entries: List = []

        for art in articles:
            url = art.get("url", "")
            if url in cached:
                embedded.append((art, cached[url]))
            else:
                art_text = _build_article_text(art)
                if not art_text.strip():
                    continue
                emb = _get_embedding(client, art_text, "RETRIEVAL_DOCUMENT")
                if emb:
                    embedded.append((art, emb))
                    new_entries.append([art.get("title", ""), url, emb])
                    cache_updated = True
                time.sleep(0.2)

        result[key] = embedded

        if new_entries:
            existing = raw_cache.get(key, [])
            raw_cache[key] = existing + new_entries
            print(f"  [{key}] 新嵌入 {len(new_entries)} 篇，共 {len(embedded)} 篇")

    if cache_updated:
        with open(emb_cache_file, "w", encoding="utf-8") as f:
            json.dump(raw_cache, f, ensure_ascii=False)

    return result


def _find_candidate_pairs(
    client: genai.Client,
    projects: List[Dict],
    article_embeddings: Dict[str, List[Tuple[Dict, List[float]]]],
    threshold: float,
    top_k: int = 3,
) -> List[Tuple[Dict, Dict, float]]:
    """
    对每个项目计算向量查询嵌入，与同分组文章对比，
    返回相似度超过阈值的候选对（每个项目最多取 top_k 篇）。
    """
    candidates: List[Tuple[Dict, Dict, float]] = []

    for proj in projects:
        iso     = proj.get("country_iso", "UNK")
        cat     = proj.get("category", "")
        key     = f"{iso}_{cat}"
        art_embs = article_embeddings.get(key, [])
        if not art_embs:
            continue

        proj_text = _build_project_text(proj)
        proj_emb  = _get_embedding(client, proj_text, "RETRIEVAL_QUERY")
        if not proj_emb:
            continue
        time.sleep(0.3)

        scored = [
            (art, _cosine_similarity(proj_emb, art_emb))
            for art, art_emb in art_embs
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        for art, sim in scored[:top_k]:
            if sim >= threshold:
                candidates.append((proj, art, sim))

    print(f"  共筛出候选对 {len(candidates)} 组（相似度 ≥ {threshold}）")
    return candidates


# ── 阶段四：LLM 确认 ────────────────────────────────────────────────────────────

def _llm_confirm_match(
    client: genai.Client,
    config: types.GenerateContentConfig,
    proj: Dict,
    art: Dict,
) -> Dict:
    """
    调用 Gemini 判断文章标题是否与项目相符。
    返回：{"is_match": bool, "confidence": "high/medium/low", "reasoning": str}
    """
    proj_name = proj.get("project_name_en") or proj.get("project_name", "")
    country   = ISO3_TO_EN.get(proj.get("country_iso", ""), proj.get("country", ""))
    cat_en    = CATEGORY_EN_TERMS.get(proj.get("category", ""), "")
    capacity  = proj.get("capacity_mw", "")
    stage     = proj.get("project_stage", "")
    entity    = ""
    if proj.get("entities"):
        entity = (proj["entities"][0].get("implementing_entity_en")
                  or proj["entities"][0].get("implementing_entity", ""))

    art_title  = art.get("title", "")
    art_domain = art.get("domain", "")
    art_date   = art.get("seendate", "")

    prompt = f"""
Determine if the news article below is reporting on the same renewable energy project.

PROJECT INFO:
- Name: {proj_name}
- Country: {country}
- Type: {cat_en}, Capacity: {capacity} MW
- Stage: {stage}
- Main Chinese Entity: {entity}

ARTICLE:
- Title: {art_title}
- Source: {art_domain}
- Date: {art_date}

Rules:
- Answer "true" only if the article clearly describes THIS specific project.
- Consider the country, energy type, capacity, and Chinese company as matching signals.
- A vague match (same country + same type but different project) should be "false".
- Be strict: similar-sounding but different projects must be "false".

Return ONLY valid JSON (no other text):
{{"is_match": true_or_false, "confidence": "high_or_medium_or_low", "reasoning": "one sentence"}}
"""
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt, config=config
        )
        result = json.loads(resp.text)
        return {
            "is_match":   bool(result.get("is_match", False)),
            "confidence": result.get("confidence", "low"),
            "reasoning":  result.get("reasoning", ""),
        }
    except Exception as e:
        print(f"    [LLM 确认失败] {e}")
        return {"is_match": False, "confidence": "low", "reasoning": f"error: {e}"}


# ── 阶段五：写回 SQLite & 导出人工核查清单 ────────────────────────────────────────

def _update_db(
    conn: sqlite3.Connection,
    event_id: str,
    status: str,
    confidence: str,
    sources: List[str],
    needs_review: int,
) -> None:
    """更新 project_events 的验证字段。"""
    # 合并已有外部信源链接（不覆盖，追加去重）
    old_raw = conn.execute(
        "SELECT verification_sources FROM project_events WHERE id = ?", (event_id,)
    ).fetchone()
    old_links: List[str] = json.loads(old_raw[0]) if old_raw and old_raw[0] else []
    merged = list(dict.fromkeys(old_links + sources))

    conn.execute(
        """UPDATE project_events
           SET verification_status     = ?,
               verification_confidence = ?,
               verification_sources    = ?,
               needs_manual_review     = ?
           WHERE id = ?""",
        (status, confidence, json.dumps(merged, ensure_ascii=False), needs_review, event_id),
    )


def _export_manual_review(records: List[Dict], output_dir: str) -> str:
    """将低置信度记录导出为 Excel 人工核查清单。"""
    if not records:
        return ""
    os.makedirs(output_dir, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.join(output_dir, f"manual_review_{ts}.xlsx")
    pd.DataFrame(records).to_excel(path, index=False)
    return path


# ── Input Schema & Tool ────────────────────────────────────────────────────────

class CrossVerificationInput(BaseModel):
    db_path: str = Field(
        default="data/db/bri_re_database.db",
        description="SQLite 数据库路径",
    )
    cache_dir: str = Field(
        default="data/verification_cache",
        description="GDELT 文章缓存及向量嵌入缓存目录",
    )
    output_dir: str = Field(
        default="data/manual_review",
        description="人工核查清单 Excel 输出目录",
    )
    gdelt_months: int = Field(
        default=24,
        description="GDELT 查询覆盖的历史月数（默认 24 个月）",
    )
    similarity_threshold: float = Field(
        default=0.65,
        description="向量相似度阈值，超过此值才进入 LLM 确认（默认 0.65）",
    )
    use_news_api: bool = Field(
        default=False,
        description="是否启用 NewsAPI 补充查询（需在 .env 中设置 NEWS_API_KEY）",
    )


class CrossVerificationTool(BaseTool):
    name: str = "cross_verification_tool"
    description: str = (
        "从 SQLite 数据库读取待验证项目，通过 GDELT 国际新闻库进行外部交叉验证，"
        "使用 Gemini 多语言嵌入向量匹配 + LLM 最终确认，"
        "将验证结果写回数据库，并导出低置信度记录的人工核查清单。"
    )
    args_schema: Type[BaseModel] = CrossVerificationInput

    def _run(
        self,
        db_path:              str   = "data/db/bri_re_database.db",
        cache_dir:            str   = "data/verification_cache",
        output_dir:           str   = "data/manual_review",
        gdelt_months:         int   = 24,
        similarity_threshold: float = 0.65,
        use_news_api:         bool  = False,
    ) -> str:
        # ── 初始化 ───────────────────────────────────────────────────────────
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "错误：未找到 GEMINI_API_KEY 环境变量，请检查 .env 文件。"

        news_api_key = os.getenv("NEWS_API_KEY", "") if use_news_api else ""

        client = genai.Client(api_key=api_key)
        llm_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        )

        if not os.path.exists(db_path):
            return f"错误：找不到数据库文件 {db_path}"

        conn = sqlite3.connect(db_path)

        try:
            # ── 阶段一：加载待验证项目 ───────────────────────────────────────
            print("\n>>> [阶段一] 从数据库加载待验证项目...")
            projects = _load_unverified_projects(conn)
            if not projects:
                return "所有项目均已完成验证，无需处理。"
            print(f"  待验证项目事件数：{len(projects)}")

            # ── 阶段二：GDELT 批量查询（含缓存） ─────────────────────────────
            print(f"\n>>> [阶段二] GDELT 批量查询（过去 {gdelt_months} 个月）...")
            article_cache = _build_gdelt_cache(projects, cache_dir, gdelt_months, news_api_key)
            total_articles = sum(len(v) for v in article_cache.values())
            print(f"  缓存文章总数：{total_articles} 篇，分组数：{len(article_cache)}")

            # ── 阶段三：向量嵌入匹配 ─────────────────────────────────────────
            print("\n>>> [阶段三] 向量嵌入 & 候选对筛选...")
            article_embeddings = _load_or_build_article_embeddings(client, article_cache, cache_dir)
            candidates = _find_candidate_pairs(
                client, projects, article_embeddings, similarity_threshold
            )

            # 记录每个 event_id 的最佳候选（按相似度）
            best_candidates: Dict[str, Tuple[Dict, Dict, float]] = {}
            for proj, art, sim in candidates:
                eid = proj["event_id"]
                if eid not in best_candidates or sim > best_candidates[eid][2]:
                    best_candidates[eid] = (proj, art, sim)

            # ── 阶段四：LLM 确认 ─────────────────────────────────────────────
            print(f"\n>>> [阶段四] LLM 逐对确认（{len(best_candidates)} 对候选）...")
            manual_review_records: List[Dict] = []
            stats = {"verified": 0, "unmatched": 0, "pending_review": 0}

            # 对有候选对的项目逐一 LLM 确认
            verified_event_ids: set = set()
            for eid, (proj, art, sim) in best_candidates.items():
                print(f"  LLM 确认: [{proj.get('project_name_en') or proj.get('project_name', '')}]")
                confirm = _llm_confirm_match(client, llm_config, proj, art)
                time.sleep(1)

                if confirm["is_match"]:
                    confidence = confirm["confidence"]
                    if confidence == "low":
                        # 低置信度 → 标记人工核查
                        _update_db(conn, eid, "pending_review", confidence, [art.get("url", "")], 1)
                        stats["pending_review"] += 1
                        manual_review_records.append({
                            "event_id":        eid,
                            "project_name":    proj.get("project_name", ""),
                            "project_name_en": proj.get("project_name_en", ""),
                            "country":         proj.get("country", ""),
                            "category":        proj.get("category", ""),
                            "stage":           proj.get("project_stage", ""),
                            "matched_title":   art.get("title", ""),
                            "matched_url":     art.get("url", ""),
                            "matched_domain":  art.get("domain", ""),
                            "similarity":      round(sim, 4),
                            "llm_confidence":  confidence,
                            "llm_reasoning":   confirm["reasoning"],
                        })
                    else:
                        # high / medium → 标记为已验证
                        _update_db(conn, eid, "verified", confidence, [art.get("url", "")], 0)
                        stats["verified"] += 1
                    verified_event_ids.add(eid)
                else:
                    # LLM 否定：不算匹配，后续走 unmatched 流程
                    pass

            # 对没有任何有效匹配的项目标记 unmatched
            for proj in projects:
                eid = proj["event_id"]
                if eid not in verified_event_ids and eid not in {
                    k for k, (_, _, _) in best_candidates.items()
                    if best_candidates[k][0]["event_id"] == eid
                }:
                    _update_db(conn, eid, "unmatched", "", [], 0)
                    stats["unmatched"] += 1

            # 同时处理有候选但 LLM 全部否定的项目
            for eid in best_candidates:
                if eid not in verified_event_ids:
                    _update_db(conn, eid, "unmatched", "", [], 0)
                    stats["unmatched"] += 1

            conn.commit()

            # ── 导出人工核查清单 ─────────────────────────────────────────────
            review_path = ""
            if manual_review_records:
                review_path = _export_manual_review(manual_review_records, output_dir)
                print(f"\n  已导出人工核查清单：{review_path}")

            return (
                f"交叉验证完成。\n"
                f"  待验证项目数：    {len(projects)}\n"
                f"  GDELT 文章总数：  {total_articles}\n"
                f"  候选对数：        {len(best_candidates)}\n"
                f"  ✅ 已验证（high/medium）：{stats['verified']}\n"
                f"  ⚠️  待人工核查（low）：   {stats['pending_review']}\n"
                f"  ❌ 未匹配：              {stats['unmatched']}\n"
                + (f"  人工核查清单：{review_path}" if review_path else "")
            )

        finally:
            conn.close()
