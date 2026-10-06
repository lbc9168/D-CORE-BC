"""
BRI RE Database — 完整流水线编排（纯 Python 顺序编排）

架构（当前已实现路径）：
    路径一（完整）：s01a → s01b → s02a → s02b
    路径二（跳过爬虫）：s01b → s02a → s02b（使用已有 raw_news.db）

预留路径（后续扩展）：
    ... → s02c（交叉验证，可选）→ s03（导出）

用法：
    cd pipeline
    uv run run_pipeline          # 路径一：完整流水线（s01a→s01b→s02a→s02b）
    uv run run_from_s01b         # 路径二：跳过爬虫，使用已有 raw_news.db（s01b→s02a→s02b）
    uv run run_export_only       # 仅重新导出（s03，数据库已有时使用）

直接调用：
    from bri_pipeline.flow import run_pipeline, PipelineConfig
    run_pipeline()                                     # 路径一（默认）
    run_pipeline(PipelineConfig(skip_s01a=True))       # 路径二
"""

import json
import logging
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# ── 加载根目录 .env（API Keys）──────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # BRI_RE_Database_v2/
load_dotenv(_PROJECT_ROOT / ".env")

# ── 各子项目根目录（绝对路径，保证跨 CWD 一致性）──────────────────────────
_S01A = _PROJECT_ROOT / "s01a_news_crawler"
_S01B = _PROJECT_ROOT / "s01b_project_extractor"
_S02A = _PROJECT_ROOT / "s02a_duplicate_merger"
_S02B = _PROJECT_ROOT / "s02b_entity_normalizer"
_S02C = _PROJECT_ROOT / "s02c_cross_verification"   # 保留备用（后续路径）
_S03  = _PROJECT_ROOT / "s03_data_exporter"         # 保留备用（后续路径）

# 延迟导入：必须在 load_dotenv 之后，避免子项目在 API Key 加载前初始化
from s01a_news_crawler.main import run_single as _run_s01a                              # noqa: E402
from s01b_project_extractor.main import run_bulk_extract as _run_s01b                    # noqa: E402
from s02a_duplicate_merger.tools.duplicate_merger_tool import DuplicateMergerTool        # noqa: E402
from s02b_entity_normalizer.entity_normalizer import run as _run_entity_suggestions      # noqa: E402


# ════════════════════════════════════════════════════════════════
#  流水线配置
# ════════════════════════════════════════════════════════════════

@dataclass
class PipelineConfig:
    """流水线配置：路径参数 + 跑哪些段的布尔标志（替代原 crewAI PipelineState）。"""
    source_url: str = "https://news.goalfore.cn/topstories/project"
    start_date: str | None = None
    end_date: str | None = None
    metadata_path: str = str(_PROJECT_ROOT / "s04_web_visualization/public/data/metadata.json")
    keywords:   str = "能源,电力,电站,MW,GW,风电,水电,光伏,储能"
    raw_data_dir:     str = str(_S01A / "data" / "raw")
    raw_news_db_path: str = str(_S01A / "data" / "raw_news.db")
    # input_db 为旧配置别名，必须与实际 S01B 输出一致。
    projects_db_path: str = str(_S01B / "data" / "projects.db")
    review_db_path:   str = str(_S01B / "data" / "review.db")
    input_db: str | None = None
    db_path:  str = str(_S02A / "data" / "db" / "bri_re_database.db")
    # ── 跑哪些段（标志位；与原 PipelineState 同名同义）──
    skip_s01a:              bool = False   # True=跳过爬虫，用已有 raw_news.db
    run_entity_intake:      bool = True    # 每轮汇总未入册/待核实企业；只读，不自动改名录
    run_entity_suggestions: bool = False   # True=只读导出疑似别名；不写主库
    run_entity_normalize:   bool = False   # 兼容旧配置名；True 也只生成建议，不再归一写库
    run_location_normalize: bool = True    # False=跳过地点归一化（s02b 第二步，写 location_norm）
    run_developer_normalize: bool = True   # False=跳过业主归一化（s02b 第三步，写 project_developer）
    run_en_backfill:        bool = True    # False=跳过英文名回填（归一后按缓存口径重算 *_en 列）
    run_txmf_backfill:      bool = True    # False=跳过 TX/MF 专用指标回填（voltage_kv 等 5 列，fill-if-null）
    run_residual_translate: bool = True    # False=跳过剩余名称翻译回填（2B-4 miss 的长尾名 LLM 翻译+残余缓存）
    run_storage_subcat_backfill: bool = True  # False=跳过严格储能容量与光伏形态回填（不再新增“综合能源/光储”）
    run_energy_taxonomy_shadow: bool = True  # False=跳过能源构成影子分类与内部差异报告
    run_cross_verification: bool = False   # True=跑 s02c（预留段）
    run_source_finder:      bool = True    # 持久队列发现新增/变更事件；无预算仅登记待处理
    source_finder_config: str | None = None
    run_export:             bool = False   # True=跑 s03 导出（预留段）

    raw_ids: list[int] | None = None
    cached_results_path: str | None = None
    max_extract_workers: int = 4
    export_dir: str | None = None
    export_web_dir: str | None = None
    export_cache_path: str | None = None
    export_allow_model_calls: bool = True
    export_external_references: str | None = None
    export_reference_config: str | None = None

    def __post_init__(self):
        if self.export_external_references is not None and self.export_reference_config is not None:
            raise ValueError("只指定 export_external_references 或 export_reference_config 之一")
        for field in ("start_date", "end_date"):
            value = getattr(self, field)
            if value is not None:
                _iso_date(value, field)
        if self.start_date is not None and self.end_date is not None and self.start_date > self.end_date:
            raise ValueError("start_date 不能晚于 end_date")
        if type(self.max_extract_workers) is not int or not 1 <= self.max_extract_workers <= 24:
            raise ValueError("max_extract_workers 必须是 1 到 24 的文章并发数")
        if self.raw_ids is not None and (not self.raw_ids or any(type(v) is not int or v <= 0 for v in self.raw_ids)):
            raise ValueError("raw_ids 必须为非空正整数列表")
        default_projects = (_S01B / "data/projects.db").resolve()
        if self.input_db is not None:
            if Path(self.projects_db_path).resolve() != default_projects and Path(self.input_db).resolve() != Path(self.projects_db_path).resolve():
                raise ValueError("input_db 必须与 S01B projects_db_path 相同")
            self.projects_db_path = self.input_db
        self.input_db = self.projects_db_path
        paths = [Path(p).resolve() for p in (self.raw_news_db_path, self.projects_db_path, self.review_db_path, self.db_path)]
        if len(set(paths)) != len(paths):
            raise ValueError("流水线各数据库路径必须相互独立")
        # 这两个旧入口尚不支持参数化；隔离运行不得意外访问默认生产库。
        if self.run_cross_verification and paths[-1] != (_S02A / "data/db/bri_re_database.db").resolve():
            raise ValueError("自定义主库暂不支持旧 S02C 固定路径入口")
        if self.run_export and paths[-1] != (_S02A / "data/db/bri_re_database.db").resolve():
            if not self.export_dir or not self.export_web_dir:
                raise ValueError("自定义主库导出必须显式指定 export_dir 和 export_web_dir")


# ════════════════════════════════════════════════════════════════
#  顺序编排
# ════════════════════════════════════════════════════════════════

def run_pipeline(cfg: "PipelineConfig | None" = None) -> dict:
    """纯 Python 顺序编排：按 cfg 标志位决定跑哪些段，阶段间靠 SQLite 文件接力。"""
    cfg = cfg or PipelineConfig()
    stage_reports: dict = {}

    # 阶段一A：默认从已发布快照次日续接，显式日期允许历史补抓。
    if cfg.skip_s01a:
        stage_reports["collection"] = {"status": "skipped", "reason": "skip_s01a"}
        print("\n[Pipeline] ⏭  阶段一A：跳过（使用已有 raw_news.db）")
    else:
        collection = _resolve_crawl_window(cfg)
        stage_reports["collection"] = collection
        if collection["status"] == "skipped":
            print(f"\n[Pipeline] 阶段一A无新增日期：续接起点 {collection['start_date']} 晚于截止日 {collection['end_date']}，继续处理已有文章。")
        else:
            _print_stage_header("1A", "新闻采集", f"请求区间 {collection['start_date']} → {collection['end_date']}")
            print(f"[Pipeline] 完整覆盖后的计划续接日：{collection['next_start_if_complete']}；本轮尚未确认覆盖。")
            _archive_s01a_files(Path(cfg.raw_data_dir))
            result = _run_s01a(
                start_date=collection["start_date"], end_date=collection["end_date"],
                source_url=cfg.source_url, keywords=cfg.keywords,
                raw_data_dir=cfg.raw_data_dir, raw_news_db_path=cfg.raw_news_db_path,
            )
            if not isinstance(result, str) or result.lstrip().startswith(("错误", "失败", "Error")):
                raise RuntimeError(f"S01A 未完成，请从 {collection['start_date']} 重试；请求截止 {collection['end_date']} 不代表已覆盖: {result!r}")
            collection["status"] = "returned"
            collection["message"] = result
            # S01A 暂无逐日覆盖契约，完成文字不能证明列表/详情均已成功。
            print(f"\n[Pipeline] s01a 返回: {result}；覆盖未自动确认，保守续接起点 {collection['next_start']}。")

    _validate_database(cfg.raw_news_db_path, {"raw_news": 1}, "S01B 输入")
    source_urls = _source_urls_for_raw_ids(cfg.raw_news_db_path, cfg.raw_ids)

    # 阶段一B：提取；真实输出与下游输入共用一条路径契约。
    _print_stage_header("1B", "项目信息 AI 提取")
    extraction = _run_s01b(raw_news_db_path=cfg.raw_news_db_path,
                          projects_db_path=cfg.projects_db_path, review_db_path=cfg.review_db_path,
                          raw_ids=cfg.raw_ids, max_extract_workers=cfg.max_extract_workers,
                          cached_results_path=cfg.cached_results_path)
    if not isinstance(extraction, dict) or extraction.get("status") not in {"completed", "partial"}:
        raise RuntimeError(f"S01B 未提供有效阶段完成结果: {extraction!r}")
    if Path(extraction.get("projects_db_path", "")).resolve() != Path(cfg.input_db).resolve():
        raise RuntimeError("S01B 实际产物路径与 S02A 输入不一致")
    # 没有关键词命中且首次运行还未生成输出库，是合法空批次。
    if not extraction.get("articles") and not Path(cfg.input_db).exists():
        return {"status": "completed", "extraction": extraction, "merge": "no_input", "stage_reports": stage_reports}
    _validate_database(cfg.input_db, {"extracted_projects": 0}, "S02A 输入")

    if cfg.run_entity_intake:
        from s01b_project_extractor.tools.entity_intake import export_intake
        _print_stage_header("1B-2", "企业待补录清单（只读）")
        report_dir = Path(cfg.input_db).resolve().parent / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        report_path = report_dir / f"entity_intake_{stamp}.json"
        export_intake(cfg.input_db, str(report_path))
        print(f"[Pipeline] 企业待补录清单：{report_path}")

    # 阶段二A：去重合并入库（直调 tool，沿用原 crew kickoff 的绝对路径，保证 s02a→s02b 接力不变）
    _print_stage_header("2A", "数据去重合并与入库")
    main_backup = None
    if Path(cfg.db_path).exists():
        _validate_database(cfg.db_path, {"canonical_projects": 0, "project_events": 0, "chinese_entities": 0}, "S02A 既有主库")
        main_backup = _backup_database(cfg.db_path)
    merger = DuplicateMergerTool()
    merge_message = merger._run(input_db=cfg.input_db, db_path=cfg.db_path, source_urls=source_urls)
    merge_result = getattr(merger, "last_result", None)
    if not isinstance(merge_result, dict) or merge_result.get("status") not in {"completed", "partial"}:
        raise RuntimeError(f"S02A 未提供有效阶段完成结果: {merge_message!r}")
    if not Path(cfg.db_path).exists() and merge_result.get("input_rows") == 0:
        return {"status": extraction["status"], "extraction": extraction, "merge": merge_result, "stage_reports": stage_reports}
    _validate_database(cfg.db_path, {"canonical_projects": 0, "project_events": 0, "chinese_entities": 0}, "S02B 输入")

    # 阶段二B：实体别名候选发现（只读、显式开启；正式归类已在 S01B 确定性完成）
    if cfg.run_entity_suggestions or cfg.run_entity_normalize:
        _print_stage_header("2B", "中国实体别名候选发现（只读）")
        _run_entity_suggestions(db_path=cfg.db_path)
    else:
        print("\n[Pipeline] ⏭  阶段二B：跳过实体别名候选发现（默认）")

    # 阶段二B-2：地点归一化（除非关闭）
    if cfg.run_location_normalize:
        from s02b_entity_normalizer.location_normalizer import run as _run_locnorm
        _print_stage_header("2B-2", "项目地点归一化（location_norm）")
        _run_locnorm(db_path=cfg.db_path)
    else:
        print("\n[Pipeline] ⏭  阶段二B-2：跳过地点归一化")

    # 阶段二B-3：业主归一化（除非关闭）
    if cfg.run_developer_normalize:
        from s02b_entity_normalizer.developer_normalizer import run as _run_devnorm
        _print_stage_header("2B-3", "业主(project_developer)归一化")
        _run_devnorm(db_path=cfg.db_path, apply=True)
    else:
        print("\n[Pipeline] ⏭  阶段二B-3：跳过业主归一化")

    # 阶段二B-4：英文名回填（归一化改了中文名后，*_en 列按缓存 en_abbr 优先口径重算；miss 只报告不改写）
    if cfg.run_en_backfill:
        from s02b_entity_normalizer.en_backfill import run_backfill as _run_en_backfill
        _print_stage_header("2B-4", "公司英文名回填（缓存口径）")
        _n_backfilled = _run_en_backfill(cfg.db_path)
        print(f"[Pipeline] 英文名回填 {_n_backfilled} 行")
    else:
        print("\n[Pipeline] ⏭  阶段二B-4：跳过英文名回填")

    # 阶段二B-5：TX/MF 专用指标回填（voltage_kv/line_length_km/transmission_capacity_mw/
    # production_capacity_value/production_capacity_unit，fill-if-null，除非关闭）
    if cfg.run_txmf_backfill:
        from s01b_project_extractor.tools.txmf_backfill import run as _run_txmf_backfill
        _print_stage_header("2B-5", "TX/MF 专用指标回填")
        _txmf_report = _run_txmf_backfill(cfg.db_path, apply=True)
        print(f"[Pipeline] TX/MF 回填 would_change={_txmf_report['would_change']}，"
              f"misses={_txmf_report['misses']}")
    else:
        print("\n[Pipeline] ⏭  阶段二B-5：跳过 TX/MF 专用指标回填")

    # 阶段二B-6：剩余名称翻译回填（2B-4 未命中的长尾中文名 → LLM 翻译进残余缓存，fill-if-null 回填 *_en）
    if cfg.run_residual_translate:
        from s02b_entity_normalizer.en_translate_residual import run_residual_translate as _run_residual
        _print_stage_header("2B-6", "剩余名称翻译回填（LLM+残余缓存）")
        _res_report = _run_residual(cfg.db_path, apply=True)
        stage_reports["residual_translate"] = _res_report
        print(f"[Pipeline] 剩余翻译：新翻 {_res_report['parts_translated']} 部件"
              f"（失败 {_res_report['parts_failed']}），回填 {_res_report['rows_updated']} 行")
    else:
        print("\n[Pipeline] ⏭  阶段二B-6：跳过剩余名称翻译回填")

    # 阶段二B-7/8：严格能源事实回填 + 只写 shadow 表的分类与内部报告。
    run_energy_taxonomy_stages(
        cfg.db_path,
        run_fact_backfill=cfg.run_storage_subcat_backfill,
        run_shadow=cfg.run_energy_taxonomy_shadow,
    )

    # 阶段二C：交叉验证（预留段，默认关）
    if cfg.run_cross_verification:
        from s02c_cross_verification.main import run as _run_s02c
        _print_stage_header("2C", "外部信源交叉验证（GDELT）")
        _run_s02c()

    # S02E 自行比较持久业务指纹，失败重跑仍能发现已经由 S02A 提交的事件。
    if cfg.run_source_finder:
        from s02e_source_finder.recurring import run_incremental
        _print_stage_header("2E", "增量参考链接与容量候选")
        try:
            source_finder = run_incremental(
                main_db=Path(cfg.db_path), raw_db=Path(cfg.raw_news_db_path),
                config_path=Path(cfg.source_finder_config) if cfg.source_finder_config else None,
            )
            if not isinstance(source_finder, dict) or source_finder.get("status") not in {"completed", "partial", "awaiting_budget"}:
                raise RuntimeError("S02E 未提供有效阶段完成结果")
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            # 参考链接失败不会撤销已提交数据；持久队列在下轮恢复，导出继续使用已核验快照。
            logging.getLogger(__name__).error("S02E incomplete error_type=%s", type(exc).__name__)
            source_finder = {"status": "partial", "reason": "source_finder_failed",
                             "error_type": type(exc).__name__, "reference_snapshot": None}
        stage_reports["s02e"] = source_finder
        print(f"[Pipeline] 参考链接 status={source_finder['status']}")

    # 阶段三：导出（预留段，默认关）
    if cfg.run_export:
        from s03_data_exporter.main import run as _run_s03
        _print_stage_header("3", "数据库整理与前端导出")
        # 用户显式指定的快照/配置优先；否则使用本轮汇总，再由 S03 处理默认指针。
        reference_snapshot = cfg.export_external_references
        if not reference_snapshot and not cfg.export_reference_config:
            reference_snapshot = stage_reports.get("s02e", {}).get("reference_snapshot")
        stage_reports["export"] = _run_s03(src_db=cfg.db_path, dst_dir=cfg.export_dir, web_data_dir=cfg.export_web_dir,
                 province_cache=cfg.export_cache_path, allow_model_calls=cfg.export_allow_model_calls,
                 external_references=Path(reference_snapshot) if reference_snapshot else None,
                 reference_config=Path(cfg.export_reference_config) if cfg.export_reference_config else None)

    status = extraction["status"]
    if merge_result.get("status") == "partial":
        status = "partial"
    if stage_reports.get("residual_translate", {}).get("parts_failed", 0):
        status = "partial"
    if stage_reports.get("s02e", {}).get("status") in {"partial", "awaiting_budget"}:
        status = "partial"
    export_result = stage_reports.get("export")
    if isinstance(export_result, dict) and export_result.get("status") == "partial":
        status = "partial"
    print(f"\n[Pipeline] 流水线 status={status}；数据库：{cfg.db_path}")
    return {"status": status, "extraction": extraction, "merge": merge_result,
            "db_path": cfg.db_path, "main_backup": main_backup, "stage_reports": stage_reports}


# ════════════════════════════════════════════════════════════════
#  辅助函数
# ════════════════════════════════════════════════════════════════

def _iso_date(value: str, field: str) -> date:
    """只接受 YYYY-MM-DD，避免宽松解析悄悄改变采集范围。"""
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError(value)
        return parsed
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须为 YYYY-MM-DD: {value!r}") from exc


def _resolve_crawl_window(cfg: PipelineConfig, *, today: date | None = None) -> dict:
    """解析请求日期；只计划续接，不声称旧爬虫已证明逐日完整覆盖。"""
    end = _iso_date(cfg.end_date, "end_date") if cfg.end_date is not None else (today or date.today())
    automatic = cfg.start_date is None
    if automatic:
        try:
            metadata = json.loads(Path(cfg.metadata_path).read_text(encoding="utf-8"))
            last_updated = metadata["last_updated"]
            start = _iso_date(last_updated, "metadata.last_updated") + timedelta(days=1)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"无法从 {cfg.metadata_path} 读取有效 last_updated；请显式指定 start_date: {exc}") from exc
    else:
        start = _iso_date(cfg.start_date, "start_date")
    if start > end and not automatic:
        raise ValueError("start_date 不能晚于 end_date（默认截止日为本地今天）")
    return {
        "status": "skipped" if start > end else "planned",
        "reason": "no_new_dates" if start > end else "metadata_resume" if automatic else "explicit_range",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "coverage_confirmed": False, "next_start": start.isoformat(),
        "next_start_if_complete": (end + timedelta(days=1)).isoformat() if start <= end else start.isoformat(),
    }


def _source_urls_for_raw_ids(raw_db: str, raw_ids: list[int] | None) -> list[str] | None:
    """Carry the selected source scope into S02A, including previously extracted rows."""
    if raw_ids is None:
        return None
    if not raw_ids:
        return []
    ids = sorted(set(raw_ids))
    with closing(sqlite3.connect(Path(raw_db).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        rows = conn.execute("SELECT id,url FROM raw_news WHERE id IN (" + ",".join("?" for _ in ids) + ")", ids).fetchall()
    if {row[0] for row in rows} != set(ids):
        raise ValueError("raw_ids contains articles missing from raw_news")
    if any(not isinstance(url, str) or not url.strip() for _, url in rows):
        raise ValueError("Selected raw_news articles must have non-empty URLs")
    return sorted({url for _, url in rows})


def _backup_database(db_path: str) -> str:
    """写入阶段前创建包含 WAL 的 SQLite 快照；失败直接停止。"""
    source = Path(db_path).resolve()
    backup = source.with_name(source.name + datetime.now().strftime(".bak.%Y%m%d_%H%M%S_%f"))
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(str(backup)) as dest:
            src.backup(dest)
            if dest.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError(f"主库备份校验失败: {backup}")
    return str(backup)


def _validate_database(db_path: str, tables: dict[str, int], stage: str) -> dict[str, int]:
    """只读验证接力产物，避免 SQLite 创建空库或误把缺表当作空批次。"""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{stage}: 数据库不存在: {path}")
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            counts = {table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}
        for table, minimum in tables.items():
            if counts[table] < minimum:
                raise ValueError(f"{stage}: {path} 表 {table} 记录数 {counts[table]} 小于 {minimum}")
        return counts
    except sqlite3.Error as exc:
        raise RuntimeError(f"{stage}: 无效数据库/关键表: {path}: {exc}") from exc


def _print_stage_header(stage_id, title: str, subtitle: str = ""):
    """打印阶段分隔标题，便于在日志中区分各流水线阶段。"""
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"[Pipeline] 阶段 {stage_id}：{title}")
    if subtitle:
        print(f"       {subtitle}")
    print(sep)


def run_energy_taxonomy_stages(
    db_path: str,
    *,
    run_fact_backfill: bool = True,
    run_shadow: bool = True,
    report_dir: str | None = None,
) -> dict:
    """执行严格能源事实回填与影子分类，并返回两阶段报告。"""
    result: dict = {"facts": None, "shadow": None}
    if run_fact_backfill:
        from s01b_project_extractor.tools.storage_subcat_backfill import run as run_facts

        _print_stage_header("2B-7", "能源事实/储能容量/光伏形态回填")
        result["facts"] = run_facts(db_path, apply=True)
        print(
            f"[Pipeline] 能源事实回填 would_change={result['facts']['would_change']}，"
            f"misses={result['facts']['misses']}"
        )
    else:
        print("\n[Pipeline] ⏭  阶段二B-7：跳过能源事实回填")

    if run_shadow:
        from s01b_project_extractor.tools.energy_taxonomy_shadow import run as run_shadow_taxonomy

        _print_stage_header("2B-8", "能源构成影子分类")
        output_dir = Path(report_dir) if report_dir else Path(db_path).resolve().parent / "reports"
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        result["shadow"] = run_shadow_taxonomy(
            db_path,
            apply=True,
            out_csv=str(output_dir / f"energy_taxonomy_shadow_{stamp}.csv"),
            out_json=str(output_dir / f"energy_taxonomy_shadow_{stamp}.json"),
        )
        print(
            f"[Pipeline] 影子分类 proposed_diff={result['shadow']['would_change']}，"
            f"needs_review={result['shadow']['needs_review']}"
        )
    else:
        print("\n[Pipeline] ⏭  阶段二B-8：跳过能源构成影子分类")
    return result


def _archive_s01a_files(raw_dir: Path):
    """将 s01a data/raw/ 下的旧爬虫文件移入 Archived/ 子目录。"""
    if not raw_dir.exists():
        # 首次运行或目录尚未创建时直接跳过归档，避免 FileNotFoundError 中断流程。
        return
    archived = raw_dir / "Archived"
    archived.mkdir(parents=True, exist_ok=True)
    for f in raw_dir.iterdir():
        if f.is_file() and f.name != ".gitkeep":
            shutil.move(str(f), str(archived / f.name))


# ════════════════════════════════════════════════════════════════
#  入口函数（供 pyproject.toml scripts 调用）
# ════════════════════════════════════════════════════════════════

def run_full_pipeline():
    """一键全程：s01a → s01b → s02a → s02b。"""
    run_pipeline(PipelineConfig())


def run_from_s01b():
    """跳过爬虫：s01b → s02a → s02b（用已有 raw_news.db）。"""
    run_pipeline(PipelineConfig(skip_s01a=True))


def run_export_only():
    """仅跑 s03 导出（数据库已有时）。"""
    from s03_data_exporter.main import run as _run_s03

    _print_stage_header("3", "数据库整理与前端导出（独立运行）")
    _run_s03()
    print("\n[Pipeline] ✅ 阶段三导出完毕")
