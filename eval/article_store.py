"""article_store：从 raw_news 库按 URL 取原文，并缓存提取结果（按 url+版本 键）以避免重复调用付费 API。"""
import hashlib
import json
import logging
import pathlib
import sqlite3

logger = logging.getLogger(__name__)

# 通过向上搜索含 bri_enums.py 的目录定位仓库根，再拼出真实 raw_news 库路径（库在主仓库 s01a 下）。
_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
DEFAULT_RAW_NEWS = _root / "s01a_news_crawler" / "data" / "raw_news.db"
DEFAULT_CACHE_DIR = _root / "eval" / "cache"


def get_article(url: str, db_path=DEFAULT_RAW_NEWS) -> dict | None:
    """按 URL 从 raw_news 库取原文，返回 {title, content, publish_date} 或无记录时返回 None。"""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT title, content, publish_date FROM raw_news WHERE url=?", (url,)
        ).fetchone()
    except sqlite3.Error:
        logger.error("get_article 查询失败 url=%s db=%s", url, db_path, exc_info=True)
        raise
    finally:
        conn.close()
    if row is None:
        return None
    return {"title": row[0], "content": row[1], "publish_date": row[2]}


def cache_key(url: str, extractor_version: str) -> str:
    """根据 url + 提取器版本生成稳定的 sha1 缓存键。"""
    return hashlib.sha1((url + extractor_version).encode("utf-8")).hexdigest()


def cache_get(key: str, cache_dir=DEFAULT_CACHE_DIR) -> dict | None:
    """按键读取缓存的提取结果 JSON，缺失时返回 None；JSON 损坏时记录并抛出。"""
    path = pathlib.Path(cache_dir) / f"{key}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        logger.error("cache_get 缓存文件损坏 path=%s", path, exc_info=True)
        raise


def cache_put(key: str, value: dict, cache_dir=DEFAULT_CACHE_DIR) -> None:
    """按键将提取结果写入缓存目录（不存在则创建），utf-8、保留中文。"""
    cache_dir = pathlib.Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{key}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False)
