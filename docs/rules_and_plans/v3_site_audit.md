# v3 爬虫选站盘点（候选站清单与分级建议）

> 日期：2026-07-15
> 状态：✅ Tier-1 已圈定 —— 2026-07-16 用户拍板：**全部 6 站进入首批试点**（含索比光伏）
> 背景：v3 目标是从单一源（goalfore.cn 国富咨询）转向多源爬取。goalfore 长期保留为**内部召回基线+交叉验证源**（硬约束：其链接不得作为对外 source_link）；新源承担可引用来源。不回爬历史，只监控增量。

## 一、方法

1. **域名频次聚合**：汇总三路已有链接数据——`external-tools/external-link-adder/projects_by_source_filled.xlsx`（Serper+Gemini 为 6389 行项目找的链接）、同目录 `search_log.txt`（6178 行搜索日志）、主库 `project_events` 的 official/non_official_website_links 字段——共提取出 **884 个域名**（分析脚本为一次性 scratchpad 脚本，未入库）。
2. **实地巡检**：对频次头部 + 主题相关的 18 个站做逐站考察（robots.txt / 新闻列表页 / 渲染方式 / 分页规律 / 更新频率 / 海外新能源相关度）。巡检由 4 个 subagent（WebFetch）+ 内置浏览器复核完成。
3. **已知偏差**：频次数据来自"拿已知项目搜链接"，天然偏向确认能力强的站，对"发现新项目能力"的评估靠巡检补充；北极星、光伏們等原创媒体的频次被系统性低估。

## 二、选站标准（按权重排序）

1. **可引用性**：能否作为对外 source_link（官方新闻稿/官方媒体/正规行业媒体 → 高；转载聚合 → 中）。
2. **发现能力**：有无持续更新的项目新闻列表页，海外新能源项目浓度。
3. **可爬性与合规**：robots.txt 明确禁止（如仅允许百度）→ 排除或观察；**反爬强（WAF/UA 过滤/需 headless）→ 降一级优先级**（用户拍板规则）；服务端渲染+URL 分页 → 加分。
4. **更新频率**与转载来源标注（标注规范的聚合站可"顺藤摸瓜"发现一手源，兼作选站飞轮情报）。

## 三、Tier-1（用户已圈定：以下 6 站全部进入首批试点）

| # | 站点 | 频次 | 巡检结论 | 理由 |
|---|------|-----|---------|------|
| 1 | **yidaiyilu.gov.cn** 一带一路网（项目动态 `/list/w/xmdt` + 企业 `/list/w/qiye`） | 268 | robots 明确允许+官方 sitemap；服务端渲染；`?page=N`；近日更 | 合规最干净、主题全站最贴（开工/并网类中企海外项目条目直接命中）、官方门户可引用性强 |
| 2 | **powerchina.cn** 电建集团（国际项目栏目 `col/col7449`） | 1516 | 大汉 CMS（列表数据在原始 HTML 的 record 块，需专用解析，浏览器已验证可渲染）；近日更；60 页存量；页脚有 RSS 入口 | 纯海外项目里程碑专栏（并网/移交/签约），一手官方信源；频次第 2 |
| 3 | **powerchina-intl.com** 电建国际（资讯中心 `/list/9.html`） | 1292 | 服务端渲染；`?page=N`；无 robots 限制；5 天 6 条 | 巡检中唯一"零障碍直爬"的央企站；海外 EPC 新闻浓度高；频次第 3 |
| 4 | **imsilkroad.com** 新华丝路 | 134 | 无 robots 限制；服务端渲染；`?page=N`；有**"中企海外项目周报"**专栏 | 周报逐条汇总中企海外项目且每条括注一手来源（新华社/企业公众号）——既是可引用信源（新华社背景）又是选站飞轮的情报源 |
| 5 | **seetao.com** 见道网（新能源/一带一路频道） | 258 | robots 完全开放；浏览器验证可达、小时级更新 | 定位即"一带一路工程资讯"，海外项目里程碑浓度高，与主题最对口的商业媒体 |
| 6 | **solarbe.com** 索比光伏（`news.solarbe.com/guoji/`） | 94 | news 子域 robots 无限制；服务端渲染；`?page=N`；约日更 | 光伏垂直媒体的国际频道，转载标注规范可溯源；光伏是库里最大类别，值得一个垂直源 |

## 四、Tier-2（2026-07-16 第二批处置：curl_cffi 复测后分为「已加入」与「技术受阻 defer」）

**已加入爬虫（4 项，均 curl_cffi 可达、发现层实测通过）**：
| 站点 | 处置 |
|------|------|
| sasac.gov.cn 央企联播 | ✅ 加入（`sasac.yaml`）——curl_cffi 200 可达（WebFetch 曾被 WAF 拦，非真不可达）；分页为 JS，首页列表已含最新批，max_pages=1；聚合全体央企广度无可替代 |
| cpnn.com.cn 中国能源新闻网·国际 | ✅ 加入（`cpnn.yaml`）——官方媒体可引用；分页是 index_{n-1} 偏移当前模板不支持，单页够增量 |
| nengyuanjie.net 能源界 | ✅ 加入（`nengyuanjie.yaml`）——国际45+非洲48/49 三频道，`?page=N` 确认 |
| solarbe 企业/项目频道 | ✅ 加入（并入 `solarbe.yaml`，qiye+xiangmu）——直接补「民企出海」缺口 |

**技术受阻，本轮 defer（下一轮再攻）**：
| 站点 | defer 原因 |
|------|-----------|
| bjx.com.cn 北极星 | **2026-07-16 已接入（headless，discover_only）**：瑞数分层墙——隐身 headless 过列表页拿到 133 URL，但详情页触发滑块验证码隐身过不了；现停在 URL-发现级，正文 defer（详见 changes 2026-W29b + s01a2.md） |
| pvmen.com 光伏們 | curl_cffi TLS 握手报错（OpenSSL internal）→ 待换 http/1.1 或普通 requests 重试 |
| ceec.net.cn col52604 国际业务 | 与电建 col7449 不同，**非** JPaaS（壳页仅 6KB 无 unitbuild.js），结构未明 |
| in-en.com 国际能源网 | newenergy 子站文章链接可见但分页机制未定；keylist.php 为搜索式 |
| cgnpc.com.cn 中广核 | 文章在首页可见但分页为 JS(createPageHTML)；核电为主海外浓度中等，优先级低 |

## 五、排除/暂缓（含原因）

| 站点 | 原因 |
|------|------|
| ctg.com.cn / ctgi.cn 三峡系 | robots.txt 仅允许 Baiduspider、对其余 UA 全站 Disallow —— 合规红线，暂不爬；ctgi（三峡国际）主题贴合，若日后拿到授权或政策变化可升级 |
| chinapower.com.cn 中国电力网 | 疑似 cpnn.com.cn 同源镜像 + 连接极不稳，单列无增量价值 |
| spic.com.cn 国家电投 | robots.txt 两次 502 无法确认合规；集团要闻栏目海外浓度低；待从服务器环境复查其国际栏目 |
| sinomach.com.cn 国机集团 | 列表页 URL/渲染方式未能确认，数据不足；下轮再查 |
| intl.ceec.net.cn 能建国际 | 当前网络环境完全不可达（疑地域限制）；部署到服务器后复查 |
| pv-magazine-china.com | robots 明确屏蔽 AI 类 UA + Crawl-delay 10；内容为全球行业动态翻译，非中企出海专项——只作背景参考，不入爬取名单 |
| sina/sohu/163/qq/eastmoney 等门户 | 纯转载末端，无原发价值，一律不爬 |
| 厂商官网（jinkosolar/trinasolar/goldwind/longi 等） | 频次高但只报自家项目、公关性强；不作常爬源，留作定向补源（s02e 式兜底搜索会自然命中） |

## 六、巡检遗留问题

1. intl.ceec.net.cn、spic 国际栏目、sinomach、ceec col52604（国际业务）——本网络环境未能定论，**部署到服务器（尤其若在境内）后复查**。
2. powerchina.cn 页脚的 RSS 订阅入口未验证，实施爬虫时优先确认（RSS 是最稳的增量监控方式）。
3. 大汉 CMS（powerchina/ceec，政企站常见）需实现一个通用的 record 块解析器；先验证纯 HTTP 抓取能否拿到 record 数据，不行再上 headless。
4. 北极星等强反爬站若未来要爬，需 headless + 浏览器 UA，成本高——保持 Tier-2 直到飞轮数据证明其增量价值。

## 七、下一步（待用户圈定 Tier-1 后）

1. 通用爬虫框架 + 每站适配器配置（无状态定时批任务，见 v3 讨论记录）；
2. 试点站跑 1 个月，量化：每站新增文章量、与 goalfore 重叠率（真实增量价值）、抽取质量；
3. 召回对照表（goalfore 有而新源无的事件 → 统计其原始出处域名 → 驱动下一轮选站）。
