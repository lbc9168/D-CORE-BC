# BRI_RE_Database_v2 — 常用命令快捷入口
# 用法：make <目标>  （在项目根目录下执行）
#
# 依赖：make（git bash / MSYS2 / WSL 均内置）

.PHONY: help pipeline from-s01b export-only s03 dev build geo

# ── 默认：显示帮助 ────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  BRI RE Database — 可用命令"
	@echo "  ──────────────────────────────────────────────"
	@echo "  make pipeline     完整流水线（s01a→s01b→s02a→s02b）"
	@echo "  make from-s01b    跳过爬虫，使用已有新闻库（s01b→s02a→s02b）"
	@echo "  make export-only  仅重新导出（s03，数据库已有时）"
	@echo "  make s03          仅运行 s03 导出"
	@echo "  make dev          启动 s04 前端开发服务器"
	@echo "  make build        构建 s04 前端（生产包）"
	@echo "  make geo          下载省级地图数据（首次）"
	@echo "  ──────────────────────────────────────────────"

# ── 流水线（pipeline/ 子项目）────────────────────────────────────────────────
pipeline:
	uv run --directory pipeline run_pipeline

from-s01b:
	uv run --directory pipeline run_from_s01b

export-only:
	uv run --directory pipeline run_export_only

# ── 单独运行各阶段 ────────────────────────────────────────────────────────────
s03:
	uv run --directory s03_data_exporter run_export

# ── 前端（s04_web_visualization/）────────────────────────────────────────────
dev:
	cd s04_web_visualization && npm run dev

build:
	cd s04_web_visualization && npm run build

geo:
	cd s04_web_visualization && python scripts/download_geo.py
