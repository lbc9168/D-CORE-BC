## 项目说明
- 项目当前状态在docs/status中

## 代码注释规则
- 每一个function和主要模块，用一句话简述该function和模块的功能

## 改动规则
- 每当对项目代码进行改动后，同步更新docs/status里的项目状态，以及docs/changes里的修改日志

## 编程规则（v2 起生效，源自 coding_rules_draft）
1. 禁止 `except: pass` 或裸 `except: pass`；捕获异常必须指定具体类型，并用 `logging.error()` 记录信息与上下文。唯一允许沉默的情况是已知无害边界（如 ALTER TABLE 列已存在），且必须加注释说明。
2. 运行时输出统一用 `logging` 并写入文件（含时间戳/模块名/级别/内容）；`print()` 仅限脚本顶层入口的人机交互提示，关键节点可 print 但必须有 logging 备份。
3. 批量写库必须用事务包裹，禁止循环内逐条 `commit()`；单次写入超过 50 条用 `executemany()`。
4. 所有对外暴露的函数入口参数用 Pydantic `BaseModel` 校验（日期格式、数值范围、路径存在性）；非法参数在入口立即抛出，不得进入业务逻辑。
5. 所有 HTTP 响应必须检查 `status_code`：404 跳过不重试，429 等待≥30秒后重试，403 记录日志并终止，5xx 指数退避重试；未校验的响应禁止传入解析逻辑。
6. 所有从外部来源（JSON 文件、API 响应）加载的数据，进入业务逻辑前必须用 `zod` 或等效库做运行时结构校验，失败立即抛错。
7. 多线程共享的可变模块级变量必须用 `threading.Lock()` 保护，读写都在 `with lock:` 内完成。
8. `pipeline/flow.py` 每个阶段入口必须验证上一阶段输出（库文件存在、关键表非空、记录数合理），无效则主动抛带描述的异常。
9. API Key/凭据禁止出现在任何提交到 git 的文件；通过环境变量传入（本地 `.env`，CI 用 GitHub Secrets），`.env` 必在 `.gitignore`。

## v2 专属约束
- 枚举（类别/阶段/参与方式/融资模式）的中英文映射只能改 `enums_mapping.json`；禁止在任何 .py/.ts 里硬编码枚举值。
- 改 schema 必须同步 s01b → s02a → s03 三处，并提供迁移脚本；先在 `.db` 副本验证，动主库前先 `.bak` 备份。
- `canonical_id` 是稳定主键，禁止重排；“老项目在前”的展示需求用 `display_order` 实现。
- 数值字段（容量/投资额/分包额）一律用 REAL 存储并保留 `*_raw` 原文列；禁止用 TEXT 存数值。
- 每个 session 结束同步更新 `docs/status` 与 `docs/changes`。
- v2 范围外的事（多 agent 提取重构、后端 API、多领域 domain、定时自动运行、访问统计）一律不碰；需要时停下确认是否推迟到 v2.1/v3。