# S3 实施计划：编号稳定 + 排序 bug 修复 + display_order

> 上级计划：[v2_plan.md](../v2_plan.md) §三 S3
> 性质：**纯代码，零数据迁移**（见下方设计偏离说明）。低风险，单会话。
> 执行：TDD。完成后更新 `docs/status` 与 `docs/changes`。

**目标**：① 修 `_gen_canonical_id` / `_gen_event_id` 的字符串排序 bug（>9999 / >99 后产生重复编号）；② 提供"老项目在前"的展示序 `display_order`，且 **`canonical_id` 保持稳定不重排**。

---

## 设计偏离（相对 v2_plan §三 S3，已做更优取舍）

v2_plan 原写"`canonical_projects` 新增 `display_order`，迁移脚本一次性回填 + 入库逻辑增量维护"。**改为：`display_order` 在 s03 flat_builder 导出时按"项目最早事件日期"升序实时计算，写入 `projects_flat`。**

理由：
- display_order 是纯展示量，s03 每次全量重建 `projects_flat`，实时计算永远正确。
- 避免"新插入一个老日期项目需要把所有人 display_order 重排"的脆弱增量维护（v2_plan 的增量方案在中间插入时无法保持有序）。
- s02a 的 `canonical_projects` 不必加列、不必迁移主库 → S3 零数据操作、零风险。
- `canonical_id` 仍然稳定（本计划只改"未来新编号怎么生成"，不动任何现有 id）。

---

## 文件结构

- **Modify** `s02a_duplicate_merger/src/s02a_duplicate_merger/tools/duplicate_merger_tool.py`：`_gen_canonical_id`(L218-225)、`_gen_event_id`(L228-235) 数值排序。
- **Modify** `s03_data_exporter/src/s03_data_exporter/flat_builder.py`：`projects_flat` 加 `display_order` 列、按最早事件日期计算并回填。
- **Modify** `s04_web_visualization/src/types/index.ts`：`Project` 加 `display_order`。
- **Modify** `s04_web_visualization/src/components/projects/ProjectList.tsx`、`src/pages/TablePage.tsx`：默认按 display_order 升序（老项目在前）。
- **Modify** `s04_web_visualization/src/utils/csvExport.ts`：导出加"序号"列（可选小改）。
- **Test** `tests/test_id_generation.py`（新建，针对 s02a 编号）。

---

## Task 1：修编号生成的字符串排序 bug（s02a，TDD）

**Files:** Create `tests/test_id_generation.py`; Modify `duplicate_merger_tool.py:218-235`

- [ ] **Step 1：写失败测试**——构造内存库，插入跨 9999/99 边界的 id，断言下一个编号正确（不重复）。

```python
"""s02a 编号生成：数值排序，跨 9999/99 边界不产生重复。"""
import sqlite3
from s02a_duplicate_merger.tools.duplicate_merger_tool import (
    _gen_canonical_id, _gen_event_id, SCHEMA_SQL,
)

def _db():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA_SQL)
    return c

def test_canonical_id_numeric_order_past_9999():
    c = _db()
    for n in (9, 10, 9999, 10000):  # 含跨 9999 边界
        c.execute("INSERT INTO canonical_projects(id, category) VALUES (?, '光伏')",
                  (f"IND-PV-{n:04d}",))
    # 字符串 DESC 会误把 'IND-PV-9999' 当最大 → 重复生成 10000；正确应为 10001
    assert _gen_canonical_id(c, "IND", "PV") == "IND-PV-10001"

def test_canonical_id_first_of_kind():
    c = _db()
    assert _gen_canonical_id(c, "BRA", "WD") == "BRA-WD-0001"

def test_event_id_numeric_order_past_99():
    c = _db()
    c.execute("INSERT INTO canonical_projects(id, category) VALUES ('IND-PV-0001','光伏')")
    for n in (9, 10, 99, 100):
        c.execute("INSERT INTO project_events(id, canonical_id) VALUES (?, 'IND-PV-0001')",
                  (f"IND-PV-0001-{n:02d}",))
    assert _gen_event_id(c, "IND-PV-0001") == "IND-PV-0001-101"

def test_event_id_first_of_kind():
    c = _db()
    c.execute("INSERT INTO canonical_projects(id, category) VALUES ('IND-PV-0001','光伏')")
    assert _gen_event_id(c, "IND-PV-0001") == "IND-PV-0001-01"
```

- [ ] **Step 2：跑测试确认失败**
  Run: `uv --project s02a_duplicate_merger run python -m pytest tests/test_id_generation.py -v`
  Expected: FAIL（`test_*_past_*` 断言失败，返回 10000 / 100 而非 10001 / 101）

- [ ] **Step 3：实现数值排序**——替换两个函数：

```python
def _gen_canonical_id(conn: sqlite3.Connection, iso: str, cat_code: str) -> str:
    """生成新 canonical_id，格式 {ISO3}-{CAT}-{NNNN}；按数值取最大尾号 +1（修字符串排序 bug）。"""
    prefix = f"{iso}-{cat_code}-"
    row = conn.execute(
        "SELECT MAX(CAST(substr(id, ?) AS INTEGER)) FROM canonical_projects WHERE id LIKE ?",
        (len(prefix) + 1, prefix + "%"),
    ).fetchone()
    new_num = (row[0] or 0) + 1
    return f"{prefix}{new_num:04d}"


def _gen_event_id(conn: sqlite3.Connection, canonical_id: str) -> str:
    """生成新 event_id，格式 {canonical_id}-{NN}；按数值取最大尾号 +1（修字符串排序 bug）。"""
    prefix = f"{canonical_id}-"
    row = conn.execute(
        "SELECT MAX(CAST(substr(id, ?) AS INTEGER)) FROM project_events "
        "WHERE canonical_id = ? AND id LIKE ?",
        (len(prefix) + 1, canonical_id, prefix + "%"),
    ).fetchone()
    new_num = (row[0] or 0) + 1
    return f"{prefix}{new_num:02d}"
```

> 说明：`substr(id, len(prefix)+1)` 取尾号子串（prefix 长度可变，因 cat_code 有 2/3 字符如 UNK），`CAST(... AS INTEGER)` 后 `MAX` 取真正最大数值。超过 9999/99 时 `:04d`/`:02d` 自然变长（如 "10001"），仍数值可排。

- [ ] **Step 4：跑测试转绿。** Run 同 Step 2。Expected: PASS（4/4）
- [ ] **Step 5：提交。** `git add tests/test_id_generation.py s02a_duplicate_merger/.../duplicate_merger_tool.py && git commit -m "fix(s02a): 编号生成改数值排序，修 >9999/99 重复 bug"`

---

## Task 2：s03 计算 display_order 写入 projects_flat

**Files:** Modify `s03_data_exporter/src/s03_data_exporter/flat_builder.py`

- [ ] **Step 6**：`CREATE_TABLE_SQL`（L44 起）在 `project_id` 之后加列：

```sql
    project_id          TEXT PRIMARY KEY,
    display_order       INTEGER,
```

- [ ] **Step 7**：在 row 构建循环内，为每行计算"最早事件日期"并暂存。在 `rows.append({...})` 的字典里加一个临时键 `_earliest`（取该项目所有事件 `publish_date or investment_date` 的非空最小值，无则置 `"9999-99"` 让其排末尾）：

```python
        _dates = [
            d for e in proj_events
            for d in (e.get("publish_date") or e.get("investment_date"),)
            if d
        ]
        _earliest = min(_dates) if _dates else "9999-99"
```

并在 append 的字典中加入 `"_earliest": _earliest,`（与 `"project_id": pid,` 同级）。

- [ ] **Step 8**：所有 row 收集完、写库（`to_sql`）之前，按 `_earliest` 升序排序并赋 `display_order`，再删除临时键：

```python
    # display_order：按项目最早事件日期升序（老项目在前）；canonical_id 保持稳定不受影响
    rows.sort(key=lambda r: r["_earliest"])
    for i, r in enumerate(rows, start=1):
        r["display_order"] = i
        del r["_earliest"]
```

- [ ] **Step 9**：跑 s03 导出（对当前主库），确认 `projects_flat` 有 `display_order` 列、最早项目=1、且导出的 `projects_flat.json` 含该字段。
  Run: `uv run run_export`（按子项目入口）
  Expected: 成功；`SELECT project_id, display_order FROM projects_flat ORDER BY display_order LIMIT 3` 为最老三个项目。
- [ ] **Step 10**：确认 `json_exporter.py` 导出 projects_flat 时包含新列（若它 `SELECT *` 则自动包含；否则补列）。提交。

---

## Task 3：前端按 display_order 排序

**Files:** Modify `s04_web_visualization/src/types/index.ts`, `components/projects/ProjectList.tsx`, `pages/TablePage.tsx`

- [ ] **Step 11**：`types/index.ts` 的 `Project` 接口（L19 起）加字段：

```typescript
  project_id: string;
  display_order: number | null;
```

- [ ] **Step 12**：`ProjectList.tsx` 渲染前按 display_order 升序（老项目在前）。在 map 之前对传入列表排序：

```typescript
  const ordered = [...projects].sort(
    (a, b) => (a.display_order ?? Number.MAX_SAFE_INTEGER) - (b.display_order ?? Number.MAX_SAFE_INTEGER)
  );
```
并把后续 `.map` 的数据源改为 `ordered`。

- [ ] **Step 13**：`TablePage.tsx` 默认排序改为 display_order 升序：`useState<keyof Project>("display_order")`（L24）、`useState(true)`（L25，asc）。其余排序交互不变。
- [ ] **Step 14**：`csvExport.ts` 列定义加 `{ key: "display_order", label: "Order" }`（放首列，可选）。
- [ ] **Step 15**：`npm run build` + `npx tsc --noEmit` 通过；浏览器实测：列表/表格默认老项目在前；点其它列排序仍正常。提交。

---

## Task 4：收尾

- [ ] **Step 16**：`pytest tests/`（含 S1/S2/S3）全绿；`npm run build` 通过。
- [ ] **Step 17**：更新 `docs/status`（s02a/s03/s04）+ `docs/changes` 记录 S3。提交。

---

## 验证清单（DoD）

- [ ] `tests/test_id_generation.py` 4/4 绿；跨 9999/99 边界不产生重复编号。
- [ ] 现有 canonical_id / event_id **未发生任何变化**（编号稳定）。
- [ ] `projects_flat.display_order` 按最早事件日期升序，最老项目=1；导出 JSON 含该字段。
- [ ] 前端列表/表格默认"老项目在前"，其它列排序仍可用。
- [ ] docs/status 与 docs/changes 已更新。

## 风险与回滚

- 纯代码、不动主库数据；出错 `git reset` 即可。
- 唯一行为变化：前端默认排序由 event_year 改为 display_order；如不满意可在 TablePage 默认值切回。
- display_order 每次导出重算，属预期（非稳定键，仅展示用）。
