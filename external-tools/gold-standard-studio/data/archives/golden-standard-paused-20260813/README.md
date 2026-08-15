# Golden Standard Studio 暂停快照

本目录冻结暂停时的独立复核库与标注规则。原有代码、40 篇候选、人工草稿和 `pilot-v0.1.1` 均保留原位；本快照没有按 storage taxonomy v2 改写任何 Gold。

## 恢复

1. 先对 `manifest.json` 中的文件重新计算 SHA-256；不得在 hash 不一致时静默继续。
2. 停止 Studio 后，备份当前 `data/review.db`，再将本目录 `review.db` 复制回该位置。
3. 使用本目录 `golden_standard_annotation.md` 作为旧规则 `2026-08-12` 的唯一恢复口径。
4. 重新启动 Studio 并检查文章 23627 为 revision 25、未确认；核对队列、Terra/Sol 候选和 `pilot-v0.1.1` manifest。
5. 若要采用新的光储分类口径，必须新建 guideline/release 版本，不得覆盖本快照或旧 Gold。

此目录不包含服务进程状态；恢复前后均由操作者显式控制服务。
