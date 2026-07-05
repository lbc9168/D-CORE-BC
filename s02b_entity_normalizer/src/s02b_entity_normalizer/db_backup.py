"""db_backup.py — SQLite 落库前备份工具：WAL 安全快照 + 与最新备份内容相同时去重复用。

背景：旧实现 shutil.copy2 只拷主文件，WAL 模式下会丢已提交未 checkpoint 的数据；
且分块循环调用 --apply 时每次都新增备份（2026-07-01 一晚产生 ~70 个重复副本）。
"""
import hashlib
import logging
import os
import sqlite3
from datetime import datetime
from glob import glob

logger = logging.getLogger(__name__)


def _sha1(path: str) -> str:
    """分块计算文件内容 sha1，避免整读大文件。"""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_db(db_path: str) -> str:
    """对 db 做 WAL 安全快照到 <db>.bak.<时间戳>；若与最新已有备份内容一致则删快照、复用旧备份。返回备份路径。"""
    bak = db_path + datetime.now().strftime(".bak.%Y%m%d_%H%M%S")
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(bak)
        try:
            src.backup(dst)  # 原生 backup API：含 WAL 中已提交数据，快照自身为普通 journal 模式文件
        finally:
            dst.close()
    finally:
        src.close()
    prior = sorted(p for p in glob(db_path + ".bak.*") if p != bak)
    if prior:
        latest = prior[-1]
        if os.path.getsize(latest) == os.path.getsize(bak) and _sha1(latest) == _sha1(bak):
            os.remove(bak)
            logger.info("库内容与最新备份一致，复用 %s", latest)
            return latest
    logger.info("库已备份 %s", bak)
    return bak
