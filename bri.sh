#!/usr/bin/env bash
# BRI_RE_Database_v2 — 命令快捷入口
# 用法：bash bri.sh <命令>  或  ./bri.sh <命令>
# （在项目根目录下执行）

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cmd="${1:-help}"

case "$cmd" in
  help|"")
    echo ""
    echo "  BRI RE Database — 可用命令"
    echo "  ──────────────────────────────────────────────"
    echo "  ./bri.sh pipeline     完整流水线（s01→s02a→s02b→s03）"
    echo "  ./bri.sh merge        采集+去重+导出，跳过交叉验证"
    echo "  ./bri.sh export-only  仅重新导出（数据库已有时）"
    echo "  ./bri.sh collect      仅采集（s01）"
    echo "  ./bri.sh s03          仅运行 s03 导出"
    echo "  ./bri.sh dev          启动 s04 前端开发服务器"
    echo "  ./bri.sh build        构建 s04 前端（生产包）"
    echo "  ./bri.sh geo          下载省级地图数据（首次）"
    echo "  ──────────────────────────────────────────────"
    ;;
  pipeline)
    uv run --directory "$ROOT/pipeline" run_pipeline ;;
  merge)
    uv run --directory "$ROOT/pipeline" run_merge_only ;;
  export-only)
    uv run --directory "$ROOT/pipeline" run_export_only ;;
  collect)
    uv run --directory "$ROOT/s01_data_collector_filter" s01_data_collector_filter ;;
  s03)
    uv run --directory "$ROOT/s03_data_exporter" run_export ;;
  dev)
    cd "$ROOT/s04_web_visualization" && npm run dev ;;
  build)
    cd "$ROOT/s04_web_visualization" && npm run build ;;
  geo)
    cd "$ROOT/s04_web_visualization" && python scripts/download_geo.py ;;
  *)
    echo "未知命令: $cmd，运行 ./bri.sh help 查看可用命令" && exit 1 ;;
esac
