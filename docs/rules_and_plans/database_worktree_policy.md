# 被忽略数据库的 Worktree 交付规则

## 为什么 PR 合并不会同步数据库

`git worktree` 共享 Git 对象，但每个 worktree 有独立工作目录。被 `.gitignore` 排除的 SQLite 不属于 commit，因此 PR、merge、push 都不会移动它；数据库文件留在被删除的 worktree 时也不会进入 Git 历史。

## 稳定交付合同

一次数据库开发只有同时满足以下三层才算完成：

1. **可重放层**：迁移脚本、审核决策/变更计划和测试进入 Git；数据库结果不得是唯一事实源。
2. **稳定库层**：在 Main Worktree 对隔离副本验证后，用 `database_worktree_guard.py promote` 备份并事务式推广。
3. **消费层**：更新 `supporting_docs/database_state.json`，再重建 s03/s04 静态数据；导出脚本会先校验稳定库逻辑指纹。

## 标准流程

```powershell
# 1. 在开发 worktree 比较候选库与 Main 稳定库；返回码 2 表示存在未推广差异
python scripts/database_worktree_guard.py audit `
  --candidate-db <worktree-db> `
  --stable-db E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db

# 2. 在隔离副本重放迁移并完成 integrity/FK/ID/幂等验收后，先 inspect 取得逻辑指纹
python scripts/database_worktree_guard.py inspect --db <validated-candidate-db>

# 3. 显式确认该逻辑指纹，推广到 Main；工具自动留下推广前备份和 receipt
python scripts/database_worktree_guard.py promote `
  --candidate-db <validated-candidate-db> `
  --stable-db E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db `
  --confirm-logical-sha256 <logical_sha256>

# 4. 更新并提交状态合同
python scripts/database_worktree_guard.py write-state `
  --db E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db `
  --database-path s02a_duplicate_merger/data/db/bri_re_database.db `
  --output supporting_docs/database_state.json

# 5. 发布前校验；离线导出也会自动执行同一校验
python scripts/database_worktree_guard.py verify `
  --db E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db `
  --manifest supporting_docs/database_state.json
```

## 为什么不只依赖 Git hook

Git 没有 `worktree remove` 专用 hook，而且本地 hook 可被跳过、不会随仓库自动启用。pre-push 也只能看到 commit，无法把 ignored SQLite 带进 PR。因此 hook 最多作为提醒；真正的安全边界是删除前 `audit`、推广时备份/回读、发布前状态清单三道 fail-closed 门禁。
