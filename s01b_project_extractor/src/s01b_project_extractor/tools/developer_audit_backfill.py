"""Apply a complete, evidence-checked developer audit to the main database with a dry-run gate."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MAIN_DB = _ROOT / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"
DEFAULT_RAW_DB = _ROOT / "s01a_news_crawler" / "data" / "raw_news.db"
DEFAULT_PROJECTS_FLAT = _ROOT / "s04_web_visualization" / "public" / "data" / "projects_flat.json"
DEFAULT_REVIEW_CSV = _ROOT / "supporting_docs" / "developer_audit_2025_luna.csv"
_DEV_SEP_RE = re.compile(r"[;；、，,/]+")


class BackfillConfig(BaseModel):
    """Validate all external paths and the lower date bound before database access."""

    db_path: Path
    raw_db_path: Path
    projects_flat_path: Path
    review_csv_path: Path
    out_path: Path
    date_from: date = Field(default=date(2025, 1, 1))
    apply: bool = False

    @field_validator(
        "db_path", "raw_db_path", "projects_flat_path", "review_csv_path", mode="after"
    )
    @classmethod
    def _existing_file(cls, value: Path) -> Path:
        """Reject missing or non-file input paths at the CLI boundary."""
        resolved = value.resolve()
        if not resolved.is_file():
            raise ValueError(f"输入文件不存在: {resolved}")
        return resolved

    @field_validator("out_path", mode="after")
    @classmethod
    def _valid_output_parent(cls, value: Path) -> Path:
        """Require an existing output directory so previews cannot land in an unintended path."""
        resolved = value.resolve()
        if not resolved.parent.is_dir():
            raise ValueError(f"输出目录不存在: {resolved.parent}")
        return resolved


class ReviewRow(BaseModel):
    """Validate one Luna review row and constrain verdict semantics."""

    event_id: str
    canonical_id: str
    project_name: str
    publish_date: str
    current_developer: str
    source_links: str = ""
    verdict: Literal["correct", "partial", "wrong", "uncertain"]
    proposed_developer: str = ""
    proposed_developer_en: str = ""
    reason: str = ""
    evidence_quote: str = ""
    issue_type: str = ""

    @field_validator("event_id", "canonical_id", "project_name", mode="after")
    @classmethod
    def _required_text(cls, value: str) -> str:
        """Reject blank identity fields in review artifacts."""
        value = str(value or "").strip()
        if not value:
            raise ValueError("事件、项目 ID 与项目名不能为空")
        return value


def _connect_ro(path: Path) -> sqlite3.Connection:
    """Open a SQLite database in fail-closed read-only URI mode."""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _visible_project_ids(projects_flat_path: Path) -> set[str]:
    """Return the exact project IDs consumed by the renewable-energy web dataset."""
    raw = json.loads(projects_flat_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("projects_flat.json 顶层必须是数组")
    ids = {str(row.get("project_id") or "").strip() for row in raw if isinstance(row, dict)}
    ids.discard("")
    return ids


def load_scope(config: BackfillConfig) -> dict[str, dict]:
    """Load every visible post-cutoff event whose developer is currently non-empty and non-Unknown."""
    visible_ids = _visible_project_ids(config.projects_flat_path)
    conn = _connect_ro(config.db_path)
    try:
        rows = conn.execute(
            """
            SELECT pe.id AS event_id, pe.canonical_id, cp.project_name, cp.project_name_en,
                   pe.publish_date,
                   pe.project_developer, pe.project_developer_en, pe.source_links
            FROM project_events pe
            JOIN canonical_projects cp ON cp.id = pe.canonical_id
            WHERE cp.excluded = 0
              AND SUBSTR(pe.publish_date, 1, 10) >= ?
              AND COALESCE(TRIM(pe.project_developer), '') NOT IN ('', 'Unknown')
            ORDER BY pe.id
            """,
            (config.date_from.isoformat(),),
        ).fetchall()
    finally:
        conn.close()
    return {
        row["event_id"]: dict(row)
        for row in rows
        if row["canonical_id"] in visible_ids
    }


def load_reviews(path: Path) -> dict[str, ReviewRow]:
    """Parse the UTF-8 review CSV and reject duplicate event IDs."""
    reviews: dict[str, ReviewRow] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for line_no, raw in enumerate(reader, start=2):
            row = ReviewRow.model_validate(raw)
            if row.event_id in reviews:
                raise ValueError(f"review CSV 第 {line_no} 行重复 event_id={row.event_id}")
            reviews[row.event_id] = row
    return reviews


def _article_contains_quote(raw_conn: sqlite3.Connection, urls: list[str], quote: str) -> bool:
    """Check that a review quote is one continuous substring of a linked title or body."""
    if not quote:
        return False
    for url in urls:
        article = raw_conn.execute(
            "SELECT title, content FROM raw_news WHERE url = ?", (url,)
        ).fetchone()
        if article and (quote in (article["title"] or "") or quote in (article["content"] or "")):
            return True
    return False


def _split_developer(value: str) -> list[str]:
    """Split developer strings using the repository-wide separator convention."""
    return [part.strip() for part in _DEV_SEP_RE.split(str(value or "")) if part.strip()]


def _derive_proposed_en(current_cn: str, current_en: str, proposed_cn: str) -> str:
    """Retain English names when a correction only removes exact components from a parallel list."""
    if not proposed_cn:
        return ""
    current_parts = _split_developer(current_cn)
    english_parts = _split_developer(current_en)
    proposed_parts = _split_developer(proposed_cn)
    if len(current_parts) != len(english_parts):
        return ""
    mapping = dict(zip(current_parts, english_parts, strict=True))
    resolved = [mapping.get(part, "") for part in proposed_parts]
    return "; ".join(resolved) if all(resolved) else ""


def build_plan(
    config: BackfillConfig,
    scope: dict[str, dict],
    reviews: dict[str, ReviewRow],
) -> list[dict]:
    """Validate complete review coverage and return only evidence-backed changed rows."""
    missing = sorted(set(scope) - set(reviews))
    extra = sorted(set(reviews) - set(scope))
    if missing or extra:
        raise ValueError(
            f"review 覆盖不完整: missing={len(missing)} extra={len(extra)} "
            f"missing_sample={missing[:5]} extra_sample={extra[:5]}"
        )

    raw_conn = _connect_ro(config.raw_db_path)
    plan: list[dict] = []
    try:
        for event_id in sorted(scope):
            current = scope[event_id]
            review = reviews[event_id]
            exact_fields = {
                "canonical_id": current["canonical_id"],
                "publish_date": current["publish_date"],
                "current_developer": current["project_developer"],
            }
            for field, expected in exact_fields.items():
                if getattr(review, field) != str(expected or ""):
                    raise ValueError(
                        f"{event_id} {field} 与主库不一致: review={getattr(review, field)!r} "
                        f"db={expected!r}"
                    )
            valid_names = {
                str(current["project_name"] or ""),
                str(current["project_name_en"] or ""),
            }
            if review.project_name not in valid_names:
                raise ValueError(
                    f"{event_id} project_name 与主库中英文名均不一致: "
                    f"review={review.project_name!r} db={sorted(valid_names)!r}"
                )
            if review.verdict not in {"wrong", "partial"}:
                continue
            if review.proposed_developer == review.current_developer:
                raise ValueError(f"{event_id} verdict={review.verdict} 但 developer 未变化")
            if not review.reason.strip() or not review.issue_type.strip():
                raise ValueError(f"{event_id} 变更行缺少 reason 或 issue_type")
            urls = json.loads(current["source_links"] or "[]")
            if not isinstance(urls, list) or not urls:
                raise ValueError(f"{event_id} 变更行没有可核验 source_links")
            if not _article_contains_quote(raw_conn, urls, review.evidence_quote):
                raise ValueError(f"{event_id} evidence_quote 不是任一关联标题/正文的连续子串")
            proposed_en = review.proposed_developer_en.strip() or _derive_proposed_en(
                review.current_developer,
                str(current["project_developer_en"] or ""),
                review.proposed_developer,
            )
            if review.proposed_developer and not proposed_en:
                raise ValueError(
                    f"{event_id} 非空 proposed_developer 无法确定英文名；请补 proposed_developer_en"
                )
            plan.append(
                {
                    "event_id": event_id,
                    "canonical_id": review.canonical_id,
                    "project_name": review.project_name,
                    "publish_date": review.publish_date,
                    "verdict": review.verdict,
                    "issue_type": review.issue_type,
                    "current_developer": review.current_developer,
                    "proposed_developer": review.proposed_developer,
                    "current_developer_en": str(current["project_developer_en"] or ""),
                    "proposed_developer_en": proposed_en,
                    "source_links": json.dumps(urls, ensure_ascii=False),
                    "reason": review.reason,
                    "evidence_quote": review.evidence_quote,
                }
            )
    finally:
        raw_conn.close()
    return plan


def write_plan(path: Path, plan: list[dict]) -> None:
    """Write a deterministic UTF-8-SIG preview for human review and Excel compatibility."""
    fields = [
        "event_id", "canonical_id", "project_name", "publish_date", "verdict", "issue_type",
        "current_developer", "proposed_developer", "current_developer_en",
        "proposed_developer_en", "source_links", "reason", "evidence_quote",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(plan)


def _table_digest(conn: sqlite3.Connection, excluded_event_columns: set[str] | None = None) -> str:
    """Hash all logical table content, optionally excluding the two developer columns from events."""
    digest = hashlib.sha256()
    excluded_event_columns = excluded_event_columns or set()
    for table in ("canonical_projects", "project_events", "chinese_entities", "non_project_records"):
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        if table == "project_events":
            columns = [column for column in columns if column not in excluded_event_columns]
        order_column = "id"
        query = f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order_column}"
        for row in conn.execute(query):
            digest.update(json.dumps(list(row), ensure_ascii=False, default=str).encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _backup_database(db_path: Path) -> Path:
    """Create and integrity-check a timestamped SQLite backup using the backup API."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.bak.developer_audit_{timestamp}")
    source = _connect_ro(db_path)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
        target.commit()
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"备份 integrity_check 失败: {result}")
    except sqlite3.Error as exc:
        logger.error("数据库备份失败 db=%s backup=%s error=%s", db_path, backup_path, exc)
        raise
    finally:
        target.close()
        source.close()
    if not backup_path.is_file() or backup_path.stat().st_size == 0:
        raise RuntimeError(f"数据库备份为空: {backup_path}")
    return backup_path


def apply_plan(config: BackfillConfig, plan: list[dict]) -> tuple[int, Path]:
    """Back up the main DB, update all audited events in one transaction, and prove non-target data unchanged."""
    if not plan:
        raise ValueError("回填计划为空，不执行 apply")
    before = _connect_ro(config.db_path)
    try:
        before_counts = {
            table: before.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("canonical_projects", "project_events", "chinese_entities")
        }
        before_digest = _table_digest(
            before, {"project_developer", "project_developer_en"}
        )
        before_values = {
            row["event_id"]: (row["current_developer"], row["current_developer_en"])
            for row in plan
        }
    finally:
        before.close()

    backup_path = _backup_database(config.db_path)
    conn = sqlite3.connect(config.db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.executemany(
            """
            UPDATE project_events
            SET project_developer = ?, project_developer_en = ?
            WHERE id = ? AND COALESCE(project_developer, '') = ?
            """,
            [
                (
                    row["proposed_developer"],
                    row["proposed_developer_en"],
                    row["event_id"],
                    before_values[row["event_id"]][0],
                )
                for row in plan
            ],
        )
        if cursor.rowcount != len(plan):
            raise RuntimeError(f"更新行数异常: expected={len(plan)} actual={cursor.rowcount}")
        conn.commit()
    except (sqlite3.Error, RuntimeError) as exc:
        conn.rollback()
        logger.error("developer 回填失败，事务已回滚 backup=%s error=%s", backup_path, exc)
        raise
    finally:
        conn.close()

    after = _connect_ro(config.db_path)
    try:
        if after.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("apply 后 integrity_check 失败")
        if after.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("apply 后 foreign_key_check 非空")
        after_counts = {
            table: after.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("canonical_projects", "project_events", "chinese_entities")
        }
        if after_counts != before_counts:
            raise RuntimeError(f"表行数发生变化: before={before_counts} after={after_counts}")
        after_digest = _table_digest(after, {"project_developer", "project_developer_en"})
        if after_digest != before_digest:
            raise RuntimeError("developer 两列之外的逻辑数据发生变化")
        for row in plan:
            actual = after.execute(
                "SELECT project_developer, project_developer_en FROM project_events WHERE id = ?",
                (row["event_id"],),
            ).fetchone()
            expected = (row["proposed_developer"], row["proposed_developer_en"])
            if tuple(actual) != expected:
                raise RuntimeError(
                    f"{row['event_id']} 回读不一致: expected={expected!r} actual={tuple(actual)!r}"
                )
    finally:
        after.close()
    logger.info("developer 回填完成 rows=%d backup=%s", len(plan), backup_path)
    return len(plan), backup_path


def main() -> None:
    """CLI entry: always produce a preview; mutate only when --apply is explicitly supplied."""
    parser = argparse.ArgumentParser(description="2025+ 网页 developer 全量审计回填")
    parser.add_argument("--db", default=str(DEFAULT_MAIN_DB))
    parser.add_argument("--raw-db", default=str(DEFAULT_RAW_DB))
    parser.add_argument("--projects-flat", default=str(DEFAULT_PROJECTS_FLAT))
    parser.add_argument("--review-csv", default=str(DEFAULT_REVIEW_CSV))
    parser.add_argument("--date-from", default="2025-01-01")
    parser.add_argument("--out", default=str(_ROOT / "supporting_docs" / "developer_audit_2025_preview.csv"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = BackfillConfig(
        db_path=Path(args.db),
        raw_db_path=Path(args.raw_db),
        projects_flat_path=Path(args.projects_flat),
        review_csv_path=Path(args.review_csv),
        out_path=Path(args.out),
        date_from=date.fromisoformat(args.date_from),
        apply=args.apply,
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(config.out_path.with_suffix(".log"), encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    scope = load_scope(config)
    reviews = load_reviews(config.review_csv_path)
    plan = build_plan(config, scope, reviews)
    write_plan(config.out_path, plan)
    print(f">>> 审计覆盖 {len(reviews)}/{len(scope)}；回填候选 {len(plan)}；预览 {config.out_path}")
    if config.apply:
        count, backup_path = apply_plan(config, plan)
        print(f">>> 已回填 {count} 个事件；备份 {backup_path}")
    else:
        print(">>> dry-run 完成（未写库）；确认预览后加 --apply")


if __name__ == "__main__":
    main()
