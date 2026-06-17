"""P0.1 分层抽样器：按 category + 难度桶抽取 ~N 篇文章，按 project_name 拆成一行一项目的人工标注草稿 CSV。"""
import argparse
import logging
import pathlib
import random
import sqlite3
import sys

import pandas as pd
from pydantic import BaseModel, field_validator

# 上溯找到含 bri_enums.py 的仓库根（与 eval/metrics.py 同模式），保证 -m / 直接运行都能 import
_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
import bri_enums  # noqa: E402

SEED = 42  # 固定随机种子，保证可复现
SUPPLIER_INVOLVEMENT = "设备供应商"  # 设备供应商参与类型（供应商难度桶）
MIN_PER_CATEGORY = 5  # 每个 category 至少抽几个 source_link
NEGATIVE_TARGET = 15  # 负样本桶（raw_news 中无提取结果）目标条数
UNNAMED_GROUP = "(未命名)"  # project_name 为空时的分组键（输出 ai_project_name 留空）
# P0.1 一行一项目：article 级字段（status 全文复用）+ 项目级字段（取项目组首行）
# 每个金标准字段同时有 ai_<field> 只读列 与 <field> 人工修正空列
GOLD_FIELDS = (
    "status", "project_name", "category", "capacity_mw", "project_stage", "location",
    "total_investment_usd", "financing_model", "entities",
)

_LOG_PATH = pathlib.Path(__file__).resolve().parent / "build_draft.log"


def _setup_logging() -> logging.Logger:
    """配置 logging：同时写入 eval/build_draft.log 与控制台（含时间戳/模块/级别）。"""
    logger = logging.getLogger("eval.build_draft")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    fh = logging.FileHandler(_LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


log = _setup_logging()


def _default_projects_db() -> str:
    """projects.db 的根目录相对默认路径（worktree 中可能不存在，实跑用绝对覆盖）。"""
    return str(_root / "s01b_project_extractor" / "data" / "projects.db")


def _default_raw_news_db() -> str:
    """raw_news.db 的根目录相对默认路径（缺失只跳过负样本桶）。"""
    return str(_root / "s01a_news_crawler" / "data" / "raw_news.db")


class DraftArgs(BaseModel):
    """CLI 参数入口校验（项目规则 #4）：n>0、out 父目录存在、projects-db 文件存在。"""
    n: int
    out: pathlib.Path
    projects_db: pathlib.Path
    raw_news_db: pathlib.Path | None

    @field_validator("n")
    @classmethod
    def _n_positive(cls, v: int) -> int:
        """n 必须为正。"""
        if v <= 0:
            raise ValueError(f"--n 必须 > 0，收到 {v}")
        return v

    @field_validator("out")
    @classmethod
    def _out_parent_exists(cls, v: pathlib.Path) -> pathlib.Path:
        """输出路径的父目录必须已存在。"""
        if not v.parent.exists():
            raise ValueError(f"--out 父目录不存在：{v.parent}")
        return v

    @field_validator("projects_db")
    @classmethod
    def _projects_db_exists(cls, v: pathlib.Path) -> pathlib.Path:
        """projects DB 文件必须存在。"""
        if not v.exists():
            raise ValueError(f"--projects-db 文件不存在：{v}")
        return v


def _load_rows(db: pathlib.Path) -> list[dict]:
    """读取 extracted_projects 全部相关列为 dict 列表；DB 异常记录上下文后重抛。"""
    cols = (
        "id, record_type, project_name, project_summary, country, location, category, "
        "capacity_mw, project_stage, total_investment_usd, financing_model, "
        "involvement_type, implementing_entity, source_link"
    )
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(f"SELECT {cols} FROM extracted_projects")]
        conn.close()
    except sqlite3.Error as e:
        log.error("读取 extracted_projects 失败 db=%s: %s", db, e)
        raise
    log.info("已读取 extracted_projects %d 行", len(rows))
    return rows


def _group_by_source(rows: list[dict]) -> dict[str, list[dict]]:
    """把行按 source_link 分组（一篇文章可能多行：项目×实体）。"""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        src = r.get("source_link")
        if not src:
            continue
        groups.setdefault(src, []).append(r)
    return groups


def _is_empty(v) -> bool:
    """判断字段是否空（None / 空串 / 'Unknown' / 'nan'）。"""
    if v is None:
        return True
    s = str(v).strip()
    return s == "" or s.lower() == "nan"


def _ai_entities(group: list[dict]) -> list[str]:
    """聚合一个项目组内去重的 名字(角色) 对（排除 Unknown/空实体；角色空则只写名字）。"""
    seen: list[str] = []
    for r in group:
        e = r.get("implementing_entity")
        if _is_empty(e) or str(e).strip() == "Unknown":
            continue
        name = str(e).strip()
        role = r.get("involvement_type")
        pair = name if _is_empty(role) else f"{name}({str(role).strip()})"
        if pair not in seen:
            seen.append(pair)
    return seen


def _group_by_project(article_rows: list[dict]) -> dict[str, list[dict]]:
    """把一篇文章的行按 project_name 分组（空名归入 UNNAMED_GROUP），保持出现顺序。"""
    groups: dict[str, list[dict]] = {}
    for r in article_rows:
        name = r.get("project_name")
        key = UNNAMED_GROUP if _is_empty(name) else str(name).strip()
        groups.setdefault(key, []).append(r)
    return groups


def _buckets_for(group: list[dict]) -> set[str]:
    """计算某 source_link 命中的难度桶集合（capacity-missing/data-center/equipment-supplier/irrelevant）。"""
    buckets: set[str] = set()
    main = group[0]  # 主项目（P0 “只取主项目” 简化）
    cat = main.get("category")
    # capacity-missing：该类目应有容量（非 no_capacity）且主项目 capacity_mw 为空
    if cat and not bri_enums.is_no_capacity(cat) and _is_empty(main.get("capacity_mw")):
        buckets.add("capacity-missing")
    # data-center：项目名或摘要含“数据中心”
    blob = " ".join(str(main.get(k) or "") for k in ("project_name", "project_summary"))
    if "数据中心" in blob:
        buckets.add("data-center")
    # equipment-supplier：本文任一行 involvement_type 含设备供应商
    if any(SUPPLIER_INVOLVEMENT in str(r.get("involvement_type") or "") for r in group):
        buckets.add("equipment-supplier")
    # irrelevant：本文任一行 record_type='irrelevant'
    if any(str(r.get("record_type") or "") == "irrelevant" for r in group):
        buckets.add("irrelevant")
    return buckets


def _select_sources(groups: dict[str, list[dict]], n: int, rng: random.Random) -> dict[str, set[str]]:
    """分层选 source_link → {source_link: 命中桶标签集合}，目标 ~n 条。"""
    # 主项目类目映射
    src_cat = {src: g[0].get("category") or "Unknown" for src, g in groups.items()}
    src_buckets = {src: _buckets_for(g) for src, g in groups.items()}

    selected: dict[str, set[str]] = {}

    def _add(src: str, label: str) -> None:
        """选中 src 并追加桶标签。"""
        selected.setdefault(src, set()).add(label)

    # 1. category 分层：每个 DB 中出现的 category 至少 MIN_PER_CATEGORY 个
    by_cat: dict[str, list[str]] = {}
    for src, cat in src_cat.items():
        by_cat.setdefault(cat, []).append(src)
    for cat, srcs in by_cat.items():
        pick = rng.sample(srcs, min(MIN_PER_CATEGORY, len(srcs)))
        for src in pick:
            _add(src, f"category:{cat}")

    # 2. 难度桶：每个桶保证若干个（取至多 8 个，足够人工标注观察）
    bucket_members: dict[str, list[str]] = {}
    for src, bs in src_buckets.items():
        for b in bs:
            bucket_members.setdefault(b, []).append(src)
    for b, srcs in bucket_members.items():
        pick = rng.sample(srcs, min(8, len(srcs)))
        for src in pick:
            _add(src, b)

    # 3. 按比例/随机补足到 n（给已选 source 也补充其命中的全部桶标签）
    remaining = [s for s in groups if s not in selected]
    rng.shuffle(remaining)
    while len(selected) < n and remaining:
        src = remaining.pop()
        _add(src, f"category:{src_cat[src]}")

    # 为所有已选 source 补全其命中的难度桶标签（便于 _bucket 列完整反映原因）
    for src in selected:
        for b in src_buckets.get(src, set()):
            selected[src].add(b)

    log.info("分层选出 %d 个 source_link（目标 %d）", len(selected), n)
    return selected


def _url_to_article_id(raw_news_db: pathlib.Path | None) -> dict[str, int]:
    """读 raw_news 建立 url→id 映射；DB 缺失/不可读则记 warning 返回空 dict。"""
    if raw_news_db is None or not raw_news_db.exists():
        log.warning("raw_news DB 不可用（%s），跳过 article_id 关联与负样本桶", raw_news_db)
        return {}
    try:
        conn = sqlite3.connect(raw_news_db)
        m = {url: rid for rid, url in conn.execute("SELECT id, url FROM raw_news")}
        conn.close()
    except sqlite3.Error as e:
        log.warning("读取 raw_news 失败 db=%s: %s（跳过负样本桶）", raw_news_db, e)
        return {}
    log.info("已读取 raw_news url→id 映射 %d 条", len(m))
    return m


def _negative_sources(
    raw_news_db: pathlib.Path | None, extracted_urls: set[str], rng: random.Random
) -> list[str]:
    """从 raw_news 取 passed_keyword_filter=1 且未产出提取结果的 url，作为负样本桶（≈NEGATIVE_TARGET）。"""
    if raw_news_db is None or not raw_news_db.exists():
        return []
    try:
        conn = sqlite3.connect(raw_news_db)
        cand = [u for (u,) in conn.execute("SELECT url FROM raw_news WHERE passed_keyword_filter=1") if u]
        conn.close()
    except sqlite3.Error as e:
        log.warning("读取 raw_news 负样本候选失败 db=%s: %s（跳过）", raw_news_db, e)
        return []
    cand = [u for u in cand if u not in extracted_urls]
    pick = rng.sample(cand, min(NEGATIVE_TARGET, len(cand)))
    log.info("负样本桶候选 %d，选出 %d", len(cand), len(pick))
    return pick


def build_draft(args: DraftArgs) -> int:
    """主流程：抽样 source_link → 每项目一行组装 → 写 utf-8-sig CSV；返回写出的行数。"""
    rng = random.Random(SEED)
    rows = _load_rows(args.projects_db)
    groups = _group_by_source(rows)

    selected = _select_sources(groups, args.n, rng)
    url_to_id = _url_to_article_id(args.raw_news_db)
    neg = _negative_sources(args.raw_news_db, set(groups.keys()), rng)

    out_records: list[dict] = []
    n_articles = len(selected) + len(neg)  # 抽中文章数（提取桶 + 负样本桶）

    # 提取结果 source_link 行：一篇文章按 project_name 拆成多行（每项目一行）
    for src, labels in selected.items():
        article_rows = groups[src]
        # ai_status 为 article 级：本文有 irrelevant 记录则 irrelevant，否则 valid（复用每行）
        ai_status = (
            "irrelevant"
            if any(str(r.get("record_type") or "") == "irrelevant" for r in article_rows)
            else "valid"
        )
        article_meta = {
            "source_link": src,
            "article_id": url_to_id.get(src, ""),
            "_bucket": ";".join(sorted(labels)),
        }
        for pname, pgroup in _group_by_project(article_rows).items():
            head = pgroup[0]  # 项目级字段取项目组首行
            ai_vals = {
                "status": ai_status,
                "project_name": "" if pname == UNNAMED_GROUP else pname,
                "category": head.get("category") or "",
                "capacity_mw": head.get("capacity_mw") or "",
                "project_stage": head.get("project_stage") or "",
                "location": head.get("location") or "",
                "total_investment_usd": head.get("total_investment_usd") or "",
                "financing_model": head.get("financing_model") or "",
                "entities": "; ".join(_ai_entities(pgroup)),
            }
            rec = dict(article_meta)
            for f in GOLD_FIELDS:
                rec[f"ai_{f}"] = ai_vals[f]
                rec[f] = ""  # 人工修正列留空
            rec["notes"] = ""
            out_records.append(rec)

    # 负样本桶行（无 AI 字段值）
    for src in neg:
        rec = {
            "source_link": src,
            "article_id": url_to_id.get(src, ""),
            "_bucket": "negative:no_extraction",
        }
        for f in GOLD_FIELDS:
            rec[f"ai_{f}"] = ""
            rec[f] = ""
        rec["notes"] = ""
        out_records.append(rec)

    # 列顺序：固定头 + 每字段 (ai_<f>, <f>) + notes
    columns = ["source_link", "article_id", "_bucket"]
    for f in GOLD_FIELDS:
        columns += [f"ai_{f}", f]
    columns.append("notes")

    df = pd.DataFrame(out_records, columns=columns)
    try:
        df.to_csv(args.out, index=False, encoding="utf-8-sig")  # utf-8-sig 防 Excel 中文乱码
    except OSError as e:
        log.error("写出 CSV 失败 out=%s: %s", args.out, e)
        raise
    log.info("抽中 %d 篇文章 → 写出 %d 行 → %s", n_articles, len(df), args.out)
    return len(df)


def _parse_args(argv: list[str] | None = None) -> DraftArgs:
    """解析命令行并构造经 Pydantic 校验的 DraftArgs。"""
    p = argparse.ArgumentParser(description="P0.1 分层抽样草稿生成器（一文多行/每项目一行）")
    p.add_argument("--n", type=int, default=150, help="目标文章数（distinct source_link）")
    p.add_argument("--out", default="eval/draft.csv", help="输出 CSV 路径")
    p.add_argument("--projects-db", default=_default_projects_db(), help="extracted_projects DB 路径")
    p.add_argument("--raw-news-db", default=_default_raw_news_db(), help="raw_news DB 路径（可选）")
    a = p.parse_args(argv)
    return DraftArgs(
        n=a.n,
        out=pathlib.Path(a.out),
        projects_db=pathlib.Path(a.projects_db),
        raw_news_db=pathlib.Path(a.raw_news_db) if a.raw_news_db else None,
    )


def main(argv: list[str] | None = None) -> None:
    """CLI 顶层入口：校验参数 → 生成草稿 → 打印汇总。"""
    args = _parse_args(argv)
    n_written = build_draft(args)
    msg = f"草稿生成完成：{n_written} 行 → {args.out}"
    log.info(msg)
    print(msg)  # 顶层人机交互提示（已有 logging 备份）


if __name__ == "__main__":
    main()
