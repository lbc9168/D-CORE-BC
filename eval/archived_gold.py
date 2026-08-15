"""将暂停封存的 Golden Standard 只读投影为可复现的 development 评测集。"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sqlite3
import sys
import uuid
from collections import Counter
from typing import Any


_ROOT = next(path for path in pathlib.Path(__file__).resolve().parents if (path / "bri_enums.py").exists())
_GOLD_STUDIO_SRC = _ROOT / "external-tools" / "gold-standard-studio" / "src"
if str(_GOLD_STUDIO_SRC) not in sys.path:
    sys.path.insert(0, str(_GOLD_STUDIO_SRC))

from gold_standard_studio.draft_store import validate_for_confirmation  # noqa: E402
from gold_standard_studio.pilot_release import _legacy_s01b_record  # noqa: E402


ARCHIVED_DEVELOPMENT_SCHEMA = "paused-development-gold-v1"


def _sha256_file(path: pathlib.Path) -> str:
    """分块计算文件 SHA-256，避免把封存数据库整体载入内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect_readonly(path: pathlib.Path) -> sqlite3.Connection:
    """以 SQLite query-only 模式打开封存复核库。"""
    if not path.is_file():
        raise FileNotFoundError(f"封存复核库不存在：{path}")
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _write_jsonl(path: pathlib.Path, records: list[dict[str, Any]]) -> None:
    """原子写入 UTF-8 JSONL，防止中断留下半成品。"""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def _write_frozen_raw_news(path: pathlib.Path, articles: list[dict[str, Any]]) -> None:
    """把封存标题和正文写成 run-local raw_news.db，确保模型输入与人工复核一致。"""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    connection = sqlite3.connect(temporary)
    try:
        connection.execute(
            "CREATE TABLE raw_news (url TEXT PRIMARY KEY, title TEXT, content TEXT, publish_date TEXT)"
        )
        connection.executemany(
            "INSERT INTO raw_news(url,title,content,publish_date) VALUES (?,?,?,?)",
            [
                (
                    article["url"],
                    article.get("title"),
                    article.get("content"),
                    article.get("publish_date") or article.get("published_at"),
                )
                for article in articles
            ],
        )
        connection.commit()
    finally:
        connection.close()
    os.replace(temporary, path)


def _normalize_eval_contract(record: dict[str, Any]) -> dict[str, Any]:
    """把封存的未提及空值等义投影为当前 strict evaluator 使用的 Unknown 枚举。"""
    for project in record.get("projects") or []:
        if project.get("financing_model") in (None, ""):
            project["financing_model"] = "Unknown"
    return record


def build_archived_development_set(review_db: pathlib.Path, output_dir: pathlib.Path) -> dict[str, Any]:
    """仅选择 confirmed 草稿，并生成旧 evaluator Gold、冻结原文库和可审计 manifest。"""
    review_db = review_db.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    articles: list[dict[str, Any]] = []
    article_entries: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    with _connect_readonly(review_db) as connection:
        rows = connection.execute(
            "SELECT d.article_id,d.revision,d.confirmed,d.updated_at,d.payload_json,"
            "s.article_sha256,s.payload_json AS article_payload "
            "FROM drafts d JOIN article_snapshots s USING(article_id) ORDER BY d.article_id"
        ).fetchall()
        for row in rows:
            if not row["confirmed"]:
                excluded.append(
                    {"article_id": row["article_id"], "revision": row["revision"], "reason": "unconfirmed"}
                )
                continue
            payload = json.loads(row["payload_json"])
            article = json.loads(row["article_payload"])["article"]
            url = article.get("url") or article.get("source_link") or ""
            if not url:
                raise ValueError(f"文章 {row['article_id']} 的封存 URL 为空")
            content_hash = hashlib.sha256(str(article.get("content") or "").encode("utf-8")).hexdigest()
            if content_hash != row["article_sha256"]:
                raise ValueError(f"文章 {row['article_id']} 的封存正文 SHA-256 不一致")
            blockers = validate_for_confirmation(
                payload,
                [str(article.get("title") or ""), str(article.get("content") or "")],
            )
            if blockers:
                raise ValueError(f"文章 {row['article_id']} 已确认但复核校验失败：{'；'.join(blockers)}")

            rich_record = {
                "article_id": row["article_id"],
                "article": {
                    "title": article.get("title"),
                    "source_link": url,
                    "publish_date": article.get("publish_date") or article.get("published_at"),
                    "content_sha256": row["article_sha256"],
                },
                "review": {
                    "revision": row["revision"],
                    "updated_at": row["updated_at"],
                    "payload": payload,
                },
            }
            records.append(_normalize_eval_contract(_legacy_s01b_record(rich_record)))
            articles.append({**article, "url": url})
            article_entries.append(
                {
                    "article_id": row["article_id"],
                    "revision": row["revision"],
                    "status": records[-1]["status"],
                    "project_count": len(records[-1]["projects"]),
                    "source_link": url,
                    "article_sha256": row["article_sha256"],
                }
            )

    gold_path = output_dir / "gold.jsonl"
    raw_news_path = output_dir / "raw_news.db"
    _write_jsonl(gold_path, records)
    _write_frozen_raw_news(raw_news_path, articles)
    manifest = {
        "schema_version": ARCHIVED_DEVELOPMENT_SCHEMA,
        "benchmark_role": "development",
        "blind_holdout": False,
        "source_review_db": str(review_db),
        "source_review_db_sha256": _sha256_file(review_db),
        "selection": "confirmed_only",
        "article_count": len(records),
        "project_count": sum(len(record["projects"]) for record in records),
        "status_counts": dict(sorted(Counter(record["status"] for record in records).items())),
        "articles": article_entries,
        "excluded_articles": excluded,
        "policy_notes": [
            "该数据集来自暂停封存快照，不是新的正式 release，也不是 blind holdout。",
            "category 与储能相关字段保留 2026-08-12 旧口径；当前 taxonomy v2.1 对比应单独解释。",
            "模型输入仅来自封存 article_snapshots 的标题和正文，不读取后来更新的 raw_news.db。",
            "评测传输层将 financing_mode 的 not_mentioned/null 等义映射为 strict evaluator 的 Unknown；封存草稿不变。",
        ],
        "artifacts": {
            "gold.jsonl": _sha256_file(gold_path),
            "raw_news.db": _sha256_file(raw_news_path),
        },
    }
    manifest_path = output_dir / "manifest.json"
    temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex}.tmp")
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_manifest, manifest_path)
    return {**manifest, "gold_path": gold_path, "raw_news_path": raw_news_path, "manifest_path": manifest_path}
