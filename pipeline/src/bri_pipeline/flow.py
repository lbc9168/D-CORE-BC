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

import shutil
from dataclasses import dataclass
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
from s02b_entity_normalizer.entity_normalizer import run as _run_s02b                    # noqa: E402


# ════════════════════════════════════════════════════════════════
#  流水线配置
# ════════════════════════════════════════════════════════════════

@dataclass
class PipelineConfig:
    """流水线配置：路径参数 + 跑哪些段的布尔标志（替代原 crewAI PipelineState）。"""
    source_url: str = "https://news.goalfore.cn/topstories/project"
    start_date: str = "2026-04-26"
    end_date:   str = "2026-04-30"
    keywords:   str = "能源,电力,电站,MW,GW,风电,水电,光伏,储能"
    raw_data_dir:     str = str(_S01A / "data" / "raw")
    raw_news_db_path: str = str(_S01A / "data" / "raw_news.db")
    # 保留供参考；run_bulk_extract 内部固定 projects/review 路径，当前不透传
    projects_db_path: str = str(_S01B / "data" / "projects.db")
    review_db_path:   str = str(_S01B / "data" / "review.db")
    input_db: str = str(_S01B / "data" / "projects.db")
    db_path:  str = str(_S02A / "data" / "db" / "bri_re_database.db")
    # ── 跑哪些段（标志位；与原 PipelineState 同名同义）──
    skip_s01a:              bool = False   # True=跳过爬虫，用已有 raw_news.db
    run_entity_normalize:   bool = True    # False=跳过 s02b
    run_location_normalize: bool = True    # False=跳过地点归一化（s02b 第二步，写 location_norm）
    run_cross_verification: bool = False   # True=跑 s02c（预留段）
    run_export:             bool = False   # True=跑 s03 导出（预留段）


# ════════════════════════════════════════════════════════════════
#  顺序编排
# ════════════════════════════════════════════════════════════════

def run_pipeline(cfg: "PipelineConfig | None" = None) -> None:
    """纯 Python 顺序编排：按 cfg 标志位决定跑哪些段，阶段间靠 SQLite 文件接力。"""
    cfg = cfg or PipelineConfig()

    # 阶段一A：新闻采集（除非 skip_s01a）
    if cfg.skip_s01a:
        print("\n[Pipeline] ⏭  阶段一A：跳过（使用已有 raw_news.db）")
    else:
        _print_stage_header("1A", "新闻采集", f"{cfg.start_date} → {cfg.end_date}")
        _archive_s01a_files(Path(cfg.raw_data_dir))
        result = _run_s01a(
            start_date=cfg.start_date, end_date=cfg.end_date,
            source_url=cfg.source_url, keywords=cfg.keywords,
            raw_data_dir=cfg.raw_data_dir, raw_news_db_path=cfg.raw_news_db_path,
        )
        print(f"\n[Pipeline] s01a 完成: {result}")

    # 阶段一B：提取（run_bulk_extract 仅接受 raw_news_db_path；projects/review 路径其内部固定）
    _print_stage_header("1B", "项目信息 AI 提取")
    _run_s01b(raw_news_db_path=cfg.raw_news_db_path)

    # 阶段二A：去重合并入库（直调 tool，沿用原 crew kickoff 的绝对路径，保证 s02a→s02b 接力不变）
    _print_stage_header("2A", "数据去重合并与入库")
    DuplicateMergerTool()._run(input_db=cfg.input_db, db_path=cfg.db_path)

    # 阶段二B：实体归一化（除非关闭）
    if cfg.run_entity_normalize:
        _print_stage_header("2B", "中国实体名称归一化")
        _run_s02b(db_path=cfg.db_path)
    else:
        print("\n[Pipeline] ⏭  阶段二B：跳过实体归一化")

    # 阶段二B-2：地点归一化（除非关闭）
    if cfg.run_location_normalize:
        from s02b_entity_normalizer.location_normalizer import run as _run_locnorm
        _print_stage_header("2B-2", "项目地点归一化（location_norm）")
        _run_locnorm(db_path=cfg.db_path)
    else:
        print("\n[Pipeline] ⏭  阶段二B-2：跳过地点归一化")

    # 阶段二C：交叉验证（预留段，默认关）
    if cfg.run_cross_verification:
        from s02c_cross_verification.main import run as _run_s02c
        _print_stage_header("2C", "外部信源交叉验证（GDELT）")
        _run_s02c()

    # 阶段三：导出（预留段，默认关）
    if cfg.run_export:
        from s03_data_exporter.main import run as _run_s03
        _print_stage_header("3", "数据库整理与前端导出")
        _run_s03()

    print(f"\n[Pipeline] ✅ 流水线执行完毕；数据库：{cfg.db_path}")


# ════════════════════════════════════════════════════════════════
#  辅助函数
# ════════════════════════════════════════════════════════════════

def _print_stage_header(stage_id, title: str, subtitle: str = ""):
    """打印阶段分隔标题，便于在日志中区分各流水线阶段。"""
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"[Pipeline] 阶段 {stage_id}：{title}")
    if subtitle:
        print(f"       {subtitle}")
    print(sep)


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
