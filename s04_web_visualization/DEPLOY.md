# s04 本地部署与线上发布清单（D-CORE）

> 目的：把“本地部署”和“线上发布”拆成两个独立工作单元，避免未经确认发布线上，也避免“下载 CSV 404 / 数据缺失”一类漏文件问题。

## 执行边界（默认规则）

- 用户说“做 S03 导出和前端部署”时，**默认只执行第一部分：本地部署**，即重新导出、构建、自检并保留本地 `dist/`；不得自动推送或上传。
- GitHub Pages 与公司主站属于**第二部分：线上发布**。只有用户明确指定发布目标并批准后，才执行对应发布步骤。
- 本地部署完成后应报告产物、验证结果和两个可选线上目标，等待用户下一条指令。

---

## 第一部分：本地部署（默认执行）

### ① 重新导出 S03 数据

> `run_export` 需当前主数据库，应在**主数据库所在工作区**跑。
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

完成到这里即为“本地部署完成”。除非用户另行明确批准，不继续执行下面的线上发布。

---

## 第二部分：线上发布（须用户明确批准）

### 发布目标 A：GitHub Pages 镜像

- 仓库：`lbc9168/BRI_RE_Database`
- 发布源：`gh-pages` 分支根目录
- 线上地址：<https://lbc9168.github.io/BRI_RE_Database/>
- Vite `base: "./"`，适配 `/BRI_RE_Database/` 子路径。

#### ⚠️ 暂时不要使用 `npm run deploy`

`package.json` 里的 `npm run deploy`（`gh-pages -d dist`）目前有过只发布 `.gitignore` 和空 `.gitkeep`、遗漏 `index.html`/`data/` 的已知故障。在该命令被单独修复并验证前，GitHub Pages 使用下面的手动 git 流程。

#### 发布 `dist/` 到 gh-pages（手动 git）
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

#### 上线后验证（GitHub Pages 重建约 1 分钟）
```bash
curl -s -o /dev/null -w "%{http_code}\n" "https://lbc9168.github.io/BRI_RE_Database/"                       # 200
curl -s -o /dev/null -w "%{http_code}\n" "https://lbc9168.github.io/BRI_RE_Database/data/events_export.csv" # 200（不是 404）
curl -s "https://lbc9168.github.io/BRI_RE_Database/" | grep -oE 'assets/index-[A-Za-z0-9_]+\.js'            # 应等于本次 dist/assets 里的 .js 哈希
```
浏览器 Ctrl+F5 强刷，进「数据表格」点 Export CSV → 正常下载，中文不乱码。
（查 Pages 构建状态：`gh api repos/lbc9168/BRI_RE_Database/pages/builds/latest`）

---

### 发布目标 B：公司服务器（d-core.iigf.com.cn）

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
