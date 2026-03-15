"""
BRI RE Database — 完整流水线编排（crewAI Flows）

架构：
    阶段一 (s01) → 阶段二A (s02a) ─┬─→ 阶段二B (s02b，可选）─┐
                                └─→（跳过交叉验证）────────┘
                                                       ↓
                                         阶段三 (s03) 数据库整理与导出

用法：
    cd pipeline
    uv run run_pipeline                         # 完整流水线（含交叉验证 + 导出）
    uv run run_collect_and_merge                # 仅采集 + 去重合并，不导出（阶段一 → 阶段二A）
    uv run run_merge_only                       # 采集 + 去重 + 导出（跳过交叉验证）
    uv run run_export_only                      # 仅重新导出（数据库已有）

直接调用：
    from bri_pipeline.flow import BRIPipelineFlow
    flow = BRIPipelineFlow()
    flow.kickoff()                              # 使用默认参数
    flow.kickoff(inputs={"run_cross_verification": False})  # 跳过交叉验证
    flow.kickoff(inputs={"skip_collection": True})          # 跳过采集（数据已有）
    flow.kickoff(inputs={"run_export": False})              # 跳过阶段三导出
"""

import shutil
import warnings
from pathlib import Path

from crewai.flow.flow import Flow, listen, router, start
from dotenv import load_dotenv
from pydantic import BaseModel

# ── 加载根目录 .env（API Keys）──────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # BRI_RE_Database_v2/
load_dotenv(_PROJECT_ROOT / ".env")

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

# ── 各子项目根目录（绝对路径，保证跨 CWD 一致性）──────────────────────────
_S01  = _PROJECT_ROOT / "s01_data_collector_filter"
_S02A = _PROJECT_ROOT / "s02a_duplicate_merger"
_S02B = _PROJECT_ROOT / "s02b_cross_verification"
_S03  = _PROJECT_ROOT / "s03_data_exporter"

# 延迟导入：必须在 load_dotenv 之后，避免 crewai 在 API Key 加载前初始化
from s01_data_collector_filter.crew import DataCollectorFilter  # noqa: E402
from s02a_duplicate_merger.crew import DuplicateMerger          # noqa: E402
from s02b_cross_verification.crew import CrossVerification      # noqa: E402
from s03_data_exporter.main import run as _run_s03              # noqa: E402


# ════════════════════════════════════════════════════════════════
#  Flow 状态模型
# ════════════════════════════════════════════════════════════════

class PipelineState(BaseModel):
    # ── 阶段一：数据采集 ────────────────────────────────────────
    source_url:          str   = "https://news.goalfore.cn/topstories/project"
    start_date:          str   = "2018-01-01"
    end_date:            str   = "2026-03-14"
    keywords:            str   = "能源,电力,电站,MW,GW,风电,水电,光伏,储能"
    raw_data_dir:        str   = str(_S01 / "data" / "raw")
    projects_db_path:    str   = str(_S01 / "data" / "projects.db")
    review_db_path:      str   = str(_S01 / "data" / "review.db")

    # ── 阶段二A：去重合并 ────────────────────────────────────────
    input_db:            str   = str(_S01 / "data" / "projects.db")
    db_path:             str   = str(_S02A / "data" / "db" / "bri_re_database.db")

    # ── 阶段二B：交叉验证（可选）────────────────────────────────
    cache_dir:           str   = str(_S02B / "data" / "verification_cache")
    output_dir:          str   = str(_S02B / "data" / "manual_review")
    gdelt_months:        int   = 24
    similarity_threshold: float = 0.65
    use_news_api:        bool  = False

    # ── 流程控制 ─────────────────────────────────────────────────
    skip_collection:        bool = False   # True = 已有采集数据，跳过阶段一
    run_cross_verification:  bool = True   # False = 跳过阶段二B
    run_export:              bool = True   # False = 跳过阶段三导出


# ════════════════════════════════════════════════════════════════
#  Flow 定义
# ════════════════════════════════════════════════════════════════

class BRIPipelineFlow(Flow[PipelineState]):
    """BRI RE Database 完整数据流水线"""

    # ── 阶段一：数据采集 ─────────────────────────────────────────

    @start()
    def stage_01_collect(self):
        if self.state.skip_collection:
            print("\n[Flow] ⏭  阶段一：跳过（已有采集数据）")
            return

        _print_stage_header(1, "数据采集与筛选",
                            f"{self.state.start_date} → {self.state.end_date}")

        # 将旧文件归档，避免累积
        _archive_s01_files(Path(self.state.raw_data_dir))

        DataCollectorFilter().crew().kickoff(inputs={
            "source_url":       self.state.source_url,
            "start_date":       self.state.start_date,
            "end_date":         self.state.end_date,
            "keywords":         self.state.keywords,
            "raw_data_dir":     self.state.raw_data_dir,
            "projects_db_path": self.state.projects_db_path,
            "review_db_path":   self.state.review_db_path,
        })

    # ── 阶段二A：去重合并 ─────────────────────────────────────────

    @listen(stage_01_collect)
    def stage_02a_merge(self):
        _print_stage_header("2A", "数据去重合并与入库")

        DuplicateMerger().crew().kickoff(inputs={
            "input_db": self.state.input_db,
            "db_path":  self.state.db_path,
        })

    # ── 路由：是否运行交叉验证 ────────────────────────────────────

    @router(stage_02a_merge)
    def route_cross_verification(self):
        if self.state.run_cross_verification:
            return "run_verify"
        print("\n[Flow] ⏭  阶段二B：跳过交叉验证")
        return "done"

    # ── 阶段二B：交叉验证（可选）─────────────────────────────────

    @listen("run_verify")
    def stage_02b_verify(self):
        _print_stage_header("2B", "外部信源交叉验证（GDELT）")

        CrossVerification().crew().kickoff(inputs={
            "db_path":              self.state.db_path,
            "cache_dir":            self.state.cache_dir,
            "output_dir":           self.state.output_dir,
            "gdelt_months":         self.state.gdelt_months,
            "similarity_threshold": self.state.similarity_threshold,
            "use_news_api":         self.state.use_news_api,
        })
        self._run_stage_03()

    # ── "done" 路由：跳过交叉验证，直接进入阶段三 ────────────────
    @listen("done")
    def finish_no_verify(self):
        print("\n[Flow] ⏭  阶段二B：已跳过交叉验证")
        self._run_stage_03()

    # ── 阶段三：数据库整理与导出（共享，两条路径均调用）─────────
    def _run_stage_03(self):
        if not self.state.run_export:
            print("\n[Flow] ⏭  阶段三：已跳过（run_export=False）")
            print(f"\n[Flow] ✅ 流水线执行完毕")
            print(f"       数据库路径：{self.state.db_path}")
            return

        _print_stage_header(3, "数据库整理与前端导出（s03）")
        _run_s03()
        print(f"\n[Flow] ✅ 完整流水线执行完毕")
        print(f"       数据库路径：{self.state.db_path}")


# ════════════════════════════════════════════════════════════════
#  辅助函数
# ════════════════════════════════════════════════════════════════

def _print_stage_header(stage_id, title: str, subtitle: str = ""):
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"[Flow] 阶段 {stage_id}：{title}")
    if subtitle:
        print(f"       {subtitle}")
    print(sep)


def _archive_s01_files(raw_dir: Path):
    """将 s01 data/raw/ 下的旧爬虫文件移入 Archived/ 子目录。"""
    for data_dir in [raw_dir]:
        archived = data_dir / "Archived"
        archived.mkdir(parents=True, exist_ok=True)
        for f in data_dir.iterdir():
            if f.is_file() and f.name != ".gitkeep":
                shutil.move(str(f), str(archived / f.name))


# ════════════════════════════════════════════════════════════════
#  入口函数（供 pyproject.toml scripts 调用）
# ════════════════════════════════════════════════════════════════

def run_full():
    """完整流水线：阶段一 → 阶段二A → 阶段二B（交叉验证）→ 阶段三（导出）"""
    BRIPipelineFlow().kickoff()


def run_merge_only():
    """采集 + 去重合并 + 导出，跳过交叉验证（阶段一 → 阶段二A → 阶段三）"""
    BRIPipelineFlow().kickoff(inputs={"run_cross_verification": False})


def run_collect_and_merge():
    """仅采集 + 去重合并，不导出（阶段一 → 阶段二A）"""
    BRIPipelineFlow().kickoff(inputs={"run_cross_verification": False, "run_export": False})


def run_export_only():
    """仅重新执行阶段三导出（数据库已有时使用），跳过采集与去重"""
    _print_stage_header(3, "数据库整理与前端导出（s03，独立运行）")
    _run_s03()
    print("\n[Flow] ✅ 阶段三导出完毕")
