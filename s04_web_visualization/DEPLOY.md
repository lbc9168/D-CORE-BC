# s04 网站部署清单（D-CORE）

> 目的：每次更新一次性、可靠地把网站发上线，**避免再出现"下载 CSV 404"那类漏文件问题**。
> 核心原则一句话：**永远上传整份 `dist/` 文件夹（含里面的 `data/`），不要只挑几个文件传。**

---

## 背景：为什么要"整份上传"

网站是纯前端静态站（无后端）。所有数据都是 `dist/data/` 里的静态文件：
- `projects_flat.json`、`countries-110m.json`、3 个 stats JSON —— 页面渲染用；
- **`events_export.csv`** —— 点「Export CSV」时下载用。

页面 JS 运行时去 `./data/xxx` 取这些文件。**只要服务器上少了任何一个，对应功能就 404 失效**
（上次就是漏传了 `data/events_export.csv` → 下载报 404）。所以部署不能"挑文件"，必须整份 `dist/` 覆盖。

数据是**自动进构建产物**的：`s03 run_export` 把 CSV/JSON 写入 `public/data/` → `npm run build` 时
vite 自动把整个 `public/` 拷进 `dist/`。所以你**不需要手动打包数据文件**，只要按下面顺序走即可。

---

## 部署步骤

### ① （数据有更新时）重新导出数据
> 数据没变可跳过这步，直接用 `public/data/` 里现有的文件。
> 注意：`run_export` 需要当前主数据库，应在**主数据库所在的工作区**跑（不是随便哪个 worktree）。

```bash
# 在 s03 项目里
uv run run_export
```
完成后会自动写入：
- `s03_data_exporter/data/output/`（s03 自己留档）
- `s04_web_visualization/public/data/`（前端用，自动同步，无需手工复制）

### ② 构建前端
```bash
cd s04_web_visualization
npm install        # 首次或依赖有变时
npm run build      # 产出 dist/（vite 自动把 public/ 含 data/ 拷进去）
```

### ③ 构建后自检（30 秒，强烈建议）
确认 `dist/data/` 里该有的文件都在：
```bash
ls dist/data/
# 应至少看到：events_export.csv  projects_flat.json  countries-110m.json  （+ 3 个 stats json）
```
> 如果 `dist/data/events_export.csv` 不存在 → 说明 `public/data/` 里没有它 → 回到步骤①重新导出再 build。

### ④ 上传到服务器（**整份 dist/**）
把 **`dist/` 文件夹里的全部内容**上传到服务器上网站对应目录：
```
<网站根目录>/BRI_RE_Database/
```
即让服务器上的 `BRI_RE_Database/` 目录内容 = 本地 `dist/` 内容。

- ✅ **正确**：整个 `dist/`（`index.html` + `assets/` + `data/` + `geo/` + `reports/` …）一起覆盖上去。
- ❌ **错误**：只传 `index.html` 和 `assets/`，漏掉 `data/`（→ 重现 404）。
- 子路径无忧：vite 配的是相对 `base: "./"`，站点放在 `/BRI_RE_Database/` 这种子目录下也能正常工作。

### ⑤ 上线后验证（必做）
1. 强制刷新页面（Ctrl+F5），确认地图/筛选/列表正常。
2. 地址栏直接访问 `<线上网址>/BRI_RE_Database/data/events_export.csv` → 能打开/下载（**不是 404**）。
3. 进「数据表格」→ 点「Export CSV」→ 填表 → 正常下载 `dcore_events_YYYY-MM-DD.csv`，Excel 打开中文不乱码。
4. 若下载仍失败：按 F12 看控制台是否还有 `下载 events_export.csv 失败`，并核对第②③④步 `data/events_export.csv` 是否真的传上去了。

---

## 常见坑速查

| 现象 | 原因 | 处理 |
|---|---|---|
| 点 Export CSV 报 `404 …/data/events_export.csv` | 服务器漏传 `data/events_export.csv` | 重传整份 `dist/`（确保含 `data/`）；或临时只补这一个文件 |
| 下载的 CSV 是旧数据 | build 前没跑 `run_export`，`public/data` 是旧的 | 步骤①重新导出 → 重新 build → 重新上传 |
| `dist/data/` 里没有 CSV | `*.csv` 在 `.gitignore`，干净 clone 后没跑导出 | 必须本地跑过 `run_export` 再 build（数据文件不进 git，靠导出生成） |
| 页面正常但下载没反应、无报错 | 旧版本（已修复）静默失败 | 新版会弹红色失败提示；确认线上是最新构建 |

> 备注：`events_export.csv` 等数据文件**故意不纳入 git**（`.gitignore` 第 20 行 `*.csv` + `public/data/*.json`），
> 它们是 `run_export` 的生成物。因此"靠 git 同步数据"行不通，部署必须走"本地导出 → build → 传 dist"这条链。
