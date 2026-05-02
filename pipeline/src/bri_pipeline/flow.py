"""
BRI RE Database — 完整流水线编排（crewAI Flows）

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
    from bri_pipeline.flow import BRIPipelineFlow
    flow = BRIPipelineFlow()
    flow.kickoff()                                     # 路径一（默认）
    flow.kickoff(inputs={"skip_s01a": True})           # 路径二
"""

import shutil
import warnings
from pathlib import Path

from crewai.flow.flow import Flow, listen, start
from dotenv import load_dotenv
from pydantic import BaseModel

# ── 加载根目录 .env（API Keys）──────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # BRI_RE_Database_v2/
load_dotenv(_PROJECT_ROOT / ".env")

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

# ── 各子项目根目录（绝对路径，保证跨 CWD 一致性）──────────────────────────
_S01A = _PROJECT_ROOT / "s01a_news_crawler"
_S01B = _PROJECT_ROOT / "s01b_project_extractor"
_S02A = _PROJECT_ROOT / "s02a_duplicate_merger"
_S02B = _PROJECT_ROOT / "s02b_entity_normalizer"
_S02C = _PROJECT_ROOT / "s02c_cross_verification"   # 保留备用（后续路径）
_S03  = _PROJECT_ROOT / "s03_data_exporter"         # 保留备用（后续路径）

# 延迟导入：必须在 load_dotenv 之后，避免 crewai 在 API Key 加载前初始化
from s01a_news_crawler.main import run_single as _run_s01a                  # noqa: E402
from s01b_project_extractor.crew import ProjectExtractor                    # noqa: E402
from s02a_duplicate_merger.crew import DuplicateMerger                      # noqa: E402
from s02b_entity_normalizer.entity_normalizer import run as _run_s02b       # noqa: E402


# ════════════════════════════════════════════════════════════════
#  Flow 状态模型
# ════════════════════════════════════════════════════════════════

class PipelineState(BaseModel):
    # ── 阶段一A：新闻采集 ────────────────────────────────────────
    source_url:          str   = "https://news.goalfore.cn/topstories/project"
    start_date:          str   = "2026-04-26"
    end_date:            str   = "2026-04-30"
    keywords:            str   = "能源,电力,电站,MW,GW,风电,水电,光伏,储能"
    raw_data_dir:        str   = str(_S01A / "data" / "raw")
    raw_news_db_path:    str   = str(_S01A / "data" / "raw_news.db")

    # ── 阶段一B：项目信息提取 ────────────────────────────────────
    projects_db_path:    str   = str(_S01B / "data" / "projects.db")
    review_db_path:      str   = str(_S01B / "data" / "review.db")

    # ── 阶段二A：去重合并 ────────────────────────────────────────
    input_db:            str   = str(_S01B / "data" / "projects.db")
    db_path:             str   = str(_S02A / "data" / "db" / "bri_re_database.db")

    # ── 阶段二C：交叉验证（后续路径，预留）──────────────────────
    cache_dir:           str   = str(_S02C / "data" / "verification_cache")
    output_dir:          str   = str(_S02C / "data" / "manual_review")
    gdelt_months:        int   = 24
    similarity_threshold: float = 0.65
    use_news_api:        bool  = False

    # ── 流程控制 ─────────────────────────────────────────────────
    skip_s01a:              bool = False   # True = 跳过 s01a，使用已有 raw_news.db（路径二）
    run_entity_normalize:   bool = True    # False = 跳过 s02b 实体归一化（一般不建议）
    run_cross_verification: bool = False   # True = 运行 s02c（后续路径，预留）
    run_export:             bool = False   # True = 运行 s03 导出（后续路径，预留）


# ════════════════════════════════════════════════════════════════
#  Flow 定义
# ════════════════════════════════════════════════════════════════

class BRIPipelineFlow(Flow[PipelineState]):
    """BRI RE Database 数据流水线（路径一 & 路径二）"""

    # ── 阶段一A：新闻采集 ─────────────────────────────────────────

    @start()
    def stage_01a_crawl(self):
        if self.state.skip_s01a:
            print("\n[Flow] ⏭  阶段一A：跳过（使用已有 raw_news.db）")
            return

        _print_stage_header("1A", "新闻采集",
                            f"{self.state.start_date} → {self.state.end_date}")

        # 将旧爬虫文件归档，避免累积
        _archive_s01a_files(Path(self.state.raw_data_dir))

        result = _run_s01a(
            start_date=self.state.start_date,
            end_date=self.state.end_date,
            source_url=self.state.source_url,
            keywords=self.state.keywords,
            raw_data_dir=self.state.raw_data_dir,
            raw_news_db_path=self.state.raw_news_db_path,
        )
        print(f"\n[Flow] s01a 完成: {result}")

    # ── 阶段一B：项目信息提取 ─────────────────────────────────────

    @listen(stage_01a_crawl)
    def stage_01b_extract(self):
        _print_stage_header("1B", "项目信息 AI 提取")

        ProjectExtractor().crew().kickoff(inputs={
            "raw_news_db_path": self.state.raw_news_db_path,
            "projects_db_path": self.state.projects_db_path,
            "review_db_path":   self.state.review_db_path,
        })

    # ── 阶段二A：去重合并 ─────────────────────────────────────────

    @listen(stage_01b_extract)
    def stage_02a_merge(self):
        _print_stage_header("2A", "数据去重合并与入库")

        DuplicateMerger().crew().kickoff(inputs={
            "input_db": self.state.input_db,
            "db_path":  self.state.db_path,
        })

    # ── 阶段二B：中国实体名称归一化 ──────────────────────────────

    @listen(stage_02a_merge)
    def stage_02b_normalize(self):
        if not self.state.run_entity_normalize:
            print("\n[Flow] ⏭  阶段二B：跳过实体归一化（run_entity_normalize=False）")
        else:
            _print_stage_header("2B", "中国实体名称归一化")
            _run_s02b(db_path=self.state.db_path)

        print(f"\n[Flow] ✅ 流水线执行完毕")
        print(f"       数据库路径：{self.state.db_path}")

    # ── 阶段二C & 三：后续路径（预留，暂未注册监听）─────────────
    #
    # def stage_02c_verify(self):
    #     """交叉验证（GDELT）— 后续路径扩展时启用"""
    #     _print_stage_header("2C", "外部信源交叉验证（GDELT）")
    #     CrossVerification().crew().kickoff(inputs={...})
    #
    # def stage_03_export(self):
    #     """数据库整理与前端导出 — 后续路径扩展时启用"""
    #     _print_stage_header(3, "数据库整理与前端导出（s03）")
    #     _run_s03()


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
    """路径一（完整）：s01a → s01b → s02a → s02b"""
    BRIPipelineFlow().kickoff()


def run_from_s01b():
    """路径二（跳过爬虫）：s01b → s02a → s02b（使用已有 raw_news.db）"""
    BRIPipelineFlow().kickoff(inputs={"skip_s01a": True})


def run_export_only():
    """仅重新执行阶段三导出（数据库已有时使用），预留备用"""
    # 延迟导入，避免不运行 s03 时给主流水线增加额外导入依赖风险。
    from s03_data_exporter.main import run as _run_s03

    _print_stage_header(3, "数据库整理与前端导出（s03，独立运行）")
    _run_s03()
    print("\n[Flow] ✅ 阶段三导出完毕")
