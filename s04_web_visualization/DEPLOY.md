# s04 网站部署清单（D-CORE）

> 目的：每次更新一次性、可靠地把网站发上线，避免"下载 CSV 404 / 数据缺失"那类漏文件问题。

## 当前部署目标

1. **GitHub Pages**：仓库 `lbc9168/BRI_RE_Database`，源 = **`gh-pages` 分支根目录**（path `/`）。
   - **线上地址**：<https://lbc9168.github.io/BRI_RE_Database/>
   - vite `base: "./"`（相对路径），子路径 `/BRI_RE_Database/` 下正常工作。
2. **公司 IGF 服务器**：暂不更新；待数据库与网页稳定后再恢复。下方保留历史流程供届时复核，当前发布不得执行。

## ⚠️ 不要用 `npm run deploy`（当前是坏的）
`package.json` 里的 `npm run deploy`（`gh-pages -d dist`）目前**行为异常**：它不发布 `dist/`，而是把 `.gitignore` + 几个空 `.gitkeep` 推上去 → 线上缺 `index.html`/`data/` → **下载 404**。清缓存也没用。**改用下面第④步的 git 手动发布**，可靠且确定。（想以后恢复一条命令，再单独排查修 gh-pages 工具。）

---

## 部署步骤

### ⓪ 数据库状态门禁

```bash
python scripts/database_worktree_guard.py verify \
  --db s02a_duplicate_merger/data/db/bri_re_database.db \
  --manifest supporting_docs/database_state.json
```

校验失败时禁止重新导出或发布；物理文件 SHA 会受 SQLite 页布局影响，发布合同以逻辑 SHA、schema、行数、taxonomy 统计、integrity 和 FK 为准。

### ① （数据有更新时）重新导出数据
> 数据没变可跳过。`run_export` 需当前主数据库，应在**主数据库所在工作区**跑。
```bash
uv --project s03_data_exporter run run_export
```
自动写入 `s03_data_exporter/data/output/` 和 `s04_web_visualization/public/data/`（含 `events_export.csv` + 各 JSON，自动同步，无需手工复制）。

### ② 构建前端
```bash
cd s04_web_visualization
npm install        # 首次或依赖有变时
npm run build      # 产出 dist/（vite 自动把 public/ 含 data/、geo/、reports/ 拷进去）
```

### ③ 构建后自检（务必）
确认 `dist/` 是**完整站点**（不是只有几个文件）：
```bash
ls dist/                       # 应有：index.html  assets  data  geo  reports
ls dist/data/                  # 应有：events_export.csv + projects_flat.json + 4 个 stats/countries json
```
> `dist/data/events_export.csv` 不在 → 回步骤①重新导出再 build（CSV 被 .gitignore，干净 clone 后必须本地导出生成）。

### ④ 发布 dist/ 到 gh-pages（手动 git，可靠）
在 **`dist/` 目录里**新建一个临时 git 仓库（这样父级 `.gitignore` 不会误伤 `*.csv`/`*.json`），整份强推到 `gh-pages`：
```bash
cd s04_web_visualization/dist
rm -rf .git
git init -q && git checkout -q -b gh-pages
git add -A
git commit -q -m "Deploy: <这次改了什么>"
git push -f "https://github.com/lbc9168/BRI_RE_Database.git" gh-pages:gh-pages
rm -rf .git          # 清掉临时仓库，别让 dist 变成嵌套 repo
```
- `git add -A` 在全新 `git init` 里会加上 `dist/` 全部文件（约 260+ 个：index.html、assets、data/*、geo/*、reports）。
- `-f` 强推会用本次 `dist/` **整份替换** gh-pages（部署分支，历史无所谓）。依赖已缓存的 HTTPS 凭据（同 `git push origin main`）。
- 若 git 没配全局用户名，临时加：`git -c user.name="Bingcai Liu" -c user.email="liu.3444.osu@gmail.com" commit ...`

### ⑤ 上线后验证（GitHub Pages 重建约 1 分钟）
```bash
curl -s -o /dev/null -w "%{http_code}\n" "https://lbc9168.github.io/BRI_RE_Database/"                       # 200
curl -s -o /dev/null -w "%{http_code}\n" "https://lbc9168.github.io/BRI_RE_Database/data/events_export.csv" # 200（不是 404）
curl -s "https://lbc9168.github.io/BRI_RE_Database/" | grep -oE 'assets/index-[A-Za-z0-9_]+\.js'            # 应等于本次 dist/assets 里的 .js 哈希
```
浏览器 Ctrl+F5 强刷，进「数据表格」点 Export CSV → 正常下载，中文不乱码。
（查 Pages 构建状态：`gh api repos/lbc9168/BRI_RE_Database/pages/builds/latest`）

---

## 暂停流程：公司服务器（d-core.iigf.com.cn）

> 当前不得执行；仅作为恢复 IGF 发布时的历史参考。

前提：本地 `~/.ssh/shipyard.pem` 密钥（**私钥绝不进 git**）；②③步的 dist 已构建并自检。

```bash
# 1. 打包（-C 进 dist 打包内容本身，含 .nojekyll 等隐藏文件）
tar -czf dist_YYYYMMDD.tar.gz -C s04_web_visualization/dist .

# 2. 上传
scp -i ~/.ssh/shipyard.pem dist_YYYYMMDD.tar.gz root@39.104.17.72:/home/app/ydyl/

# 3. 服务器上：备份 → 解压到新目录 → 瞬间切换（近零停机）→ 清理
ssh -i ~/.ssh/shipyard.pem root@39.104.17.72 'cd /home/app/ydyl \
  && rm -rf dist_bak && cp -r dist dist_bak \
  && mkdir dist_new && tar -xzf dist_YYYYMMDD.tar.gz -C dist_new \
  && cp dist/README_部署说明.txt dist_new/ 2>/dev/null; cd /home/app/ydyl \
  && mv dist dist_old && mv dist_new dist && rm -rf dist_old dist_YYYYMMDD.tar.gz'

# 4. 公网验证（首页 / PDF / CSV / geo 全 200，asset 哈希 = 本次 dist）
curl -s -o /dev/null -w "%{http_code}\n" "https://d-core.iigf.com.cn/data/events_export.csv"
curl -s "https://d-core.iigf.com.cn/" | grep -oE 'assets/index-[A-Za-z0-9_-]+\.js'
```

- 管理员规矩：**部署前必先备份**（`cp -r dist dist_bak`，先 `rm -rf` 旧备份，否则 cp 会嵌套进去）。
- 服务器上 `dist.zip` / `dist.zip.bak` 是管理员的历史产物，**不要动**。
- nginx 配置在 `/usr/local/nginx/conf/nginx.conf`（443 端口 server 块），纯静态无需改配置、无需重载。

---

## 常见坑速查
| 现象 | 原因 | 处理 |
|---|---|---|
| Export CSV 报 `404 …/data/events_export.csv` | gh-pages 上缺 `data/events_export.csv` | 按④重发整份 dist（确认③里 CSV 在） |
| 整站打不开 / 结构错乱 | 用了 `npm run deploy` 发错内容 | 用④的手动 git 发布替代 |
| 下载的 CSV 是旧数据 | build 前没跑 `run_export` | ①重新导出 → ②build → ④发布 |
| `dist/data/` 没有 CSV | `*.csv` 被 .gitignore，干净 clone 没跑导出 | 必须本地 `run_export` 再 build |

> 备注：`events_export.csv` 等数据文件**故意不进 git**（`.gitignore` 的 `*.csv` + `public/data/*.json`），是 `run_export` 的生成物——靠 git 同步数据行不通，必须走"导出 → build → 发布 dist"这条链。若将来站点也镜像到别的服务器，同样原则：**整份 dist 一起传，别只挑文件**。
