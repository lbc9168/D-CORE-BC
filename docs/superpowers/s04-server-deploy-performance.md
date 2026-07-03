# s04 网页部署到自有服务器的加载性能设置

> 适用场景：将 s04 的静态站点（`dist/`）部署到**自有服务器**（nginx / Apache / 云主机 / CDN），而非 GitHub Pages。
> 目标：在服务器端把加载时间压到最低。**本文只覆盖服务器端设置**，不涉及前端代码或数据文件本身的改动。
> 最后更新：2026-06-30。

---

## 站点特点（决定了优化重点）

- 纯静态：`index.html` + 带内容 hash 的 `assets/*.js|css`（已做路由懒加载与 vendor 分包）+ `data/` 下静态 JSON/CSV + `geo/` 省级地图 + `reports/` PDF。
- **一个大文件是加载瓶颈**：`data/projects_flat.json` 原始约 **8.5 MB**，启动时一次性下载。
  - gzip 后约 **1.0 MB**、brotli 后约 **0.85 MB**（实测数量级）。
- 决定"下载时间"的是**压缩后**的体积，所以服务器端优化的第一要务是压缩，其次是缓存与传输层。

---

## 1. 压缩（最重要）

**原则：gzip 是必须守住的基线，brotli 是相对基线的增量。**

- ⚠️ 若服务器**未配压缩**，会把原始 8.5 MB 直接发出，下载慢约 **8.6 倍**——这是最容易踩的坑，换服务器后第一件事就是确认压缩生效。
- gzip 对 JSON/JS/CSS 这类文本压缩比很高（约 8–9 倍）；brotli 对文本通常比 gzip 再小约 **15%**。
- 注意：压缩只减小**下载**，浏览器解压后仍需解析完整文本——解析那部分服务器端管不了。

### nginx 示例

```nginx
# --- gzip（基线，务必开启）---
gzip              on;
gzip_comp_level   6;
gzip_min_length   1024;
gzip_vary         on;
gzip_types        application/json application/javascript text/css
                  text/plain image/svg+xml application/wasm;

# --- brotli（需编译 ngx_brotli 模块）---
brotli            on;
brotli_comp_level 6;
brotli_types      application/json application/javascript text/css
                  text/plain image/svg+xml;

# --- 静态预压缩（可选，最省 CPU）---
# 若 dist 里已存在 .br / .gz 预压缩文件，优先直接发送，零运行时压缩开销。
# （预压缩文件需在构建期生成，属构建步骤，本文不展开。不生成也可，靠上面的 on 运行时压缩。）
brotli_static     on;
gzip_static       on;
```

> Apache 对应：`mod_deflate`（gzip）+ `mod_brotli`；`AddOutputFilterByType BROTLI_COMPRESS application/json application/javascript text/css`。

---

## 2. 缓存头

目标：带 hash 的资源永久缓存；`index.html` 与数据文件每次校验，保证发新版/更新数据后能及时刷新。

```nginx
# 带内容 hash 的 JS/CSS：内容变则文件名变，可永久强缓存
location ~* ^/assets/.*\.(js|css)$ {
    add_header Cache-Control "public, max-age=31536000, immutable";
}

# index.html：不缓存正文，每次向服务器校验（发新版立即生效）
location = /index.html {
    add_header Cache-Control "no-cache";
}

# 数据文件（文件名固定、内容会更新）：走协商缓存
# nginx 默认对静态文件带 ETag / Last-Modified；no-cache = 每次 If-None-Match 校验，
# 未变返回 304（不重传 8.5MB），变了才重下。
location /data/ {
    add_header Cache-Control "no-cache";
}

# 省级 geo（内容基本不变）：可较长缓存
location /geo/ {
    add_header Cache-Control "public, max-age=604800";
}
```

要点：
- 前端资源已做**内容 hash**（`index-XXXXXXXX.js`），所以对 `assets/` 用 `immutable` 强缓存是安全的——回访几乎零下载。
- 大 JSON 文件名固定（`projects_flat.json`），**不能**强缓存，否则数据更新后用户拿不到新版；用 `no-cache` + ETag 走 304 校验最稳。

---

## 3. 传输层：HTTP/2 或 HTTP/3

多路复用能让首屏的多个 chunk（index / react / simple-maps）与省级 geo 多文件并行下载，明显优于 HTTP/1.1。

```nginx
server {
    listen 443 ssl;
    http2  on;              # nginx ≥ 1.25.1

    # HTTP/3 / QUIC（nginx ≥ 1.25 且编译了 http_v3 模块，可选）
    # listen 443 quic reuseport;
    # add_header Alt-Svc 'h3=":443"; ma=86400';
}
```

---

## 4. CDN / 就近节点

- 把静态站点套一层 CDN（Cloudflare 等）：边缘缓存 + 自带 brotli + HTTP/3 + 就近回源，对全球用户降延迟最直接。
- ⚠️ **受众在中国大陆**：GitHub Pages 及多数海外 CDN 在国内访问常慢/不稳。若面向国内用户，应使用**国内可访问的 CDN 或国内镜像/服务器**（在中国大陆服务器托管通常需 ICP 备案）。这是相对"部署在哪"影响最大的一项。

---

## 5. 部署后验证清单

```bash
HOST=https://<your-domain>

# ① 压缩生效？（应看到 content-encoding: br 或 gzip）
curl -sI -H "Accept-Encoding: br, gzip" "$HOST/data/projects_flat.json" | grep -i content-encoding

# ② hash 资源强缓存？（应含 immutable）
curl -sI "$HOST/assets/"index-*.js | grep -i cache-control

# ③ index.html 不强缓存？（应为 no-cache）
curl -sI "$HOST/index.html" | grep -i cache-control

# ④ 协议版本？（应为 HTTP/2 或 HTTP/3）
curl -sI --http2 "$HOST/" | head -1
```

---

## 优先级小结

1. **压缩**（gzip 保底 + brotli 增量）——不做则慢约 8.6 倍，做了下载即降到 ~1MB 量级。
2. **CDN / 国内节点**——按受众地理位置，通常是感知提升最大的一项。
3. **缓存头**——主要惠及回访与数据更新场景。
4. **HTTP/2/3**——多文件并行的锦上添花。

> 说明：真正的"下载大头"是 8.5MB 的 `projects_flat.json`，服务器端只能靠压缩把它的传输体积降下来；进一步从源头减小该文件（数据瘦身）属 s03 数据导出层，不在本文范围。
