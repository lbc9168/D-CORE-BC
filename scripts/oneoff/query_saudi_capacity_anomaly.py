"""分析沙特装机容量异常数据 - 找出哪些记录存在异常值"""
import sqlite3

def fmt_cap(val):
    """Format capacity value safely, handling string values"""
    if val is None:
        return "NULL"
    if isinstance(val, (int, float)):
        return f"{val:>12,.0f}"
    return f"{str(val):>12}"

def fmt_inv(val):
    """Format investment value safely"""
    if val is None:
        return "Unknown"
    if isinstance(val, (int, float)):
        return f"${val:,.0f}M"
    return str(val)

def analyze_s01b():
    conn = sqlite3.connect(r"e:\Projects\BRI_RE_Database_v2\s01b_project_extractor\data\projects.db")
    c = conn.cursor()
    
    print("=" * 80)
    print("S01B EXTRACTED_PROJECTS - 沙特容量数据分析")
    print("=" * 80)
    
    # 1. 容量分布统计
    print("\n### 1. 沙特项目容量值分布 Top50 ###")
    c.execute("""
        SELECT capacity_mw, COUNT(*) 
        FROM extracted_projects 
        WHERE country_iso = 'SAU' AND capacity_mw IS NOT NULL AND capacity_mw > 0
        GROUP BY capacity_mw 
        ORDER BY capacity_mw DESC 
        LIMIT 50
    """)
    rows = c.fetchall()
    for cap, cnt in rows:
        print(f"  {fmt_cap(cap)} MW: {cnt} 个记录")
    
    # 2. 分档统计
    print("\n### 2. 容量分档统计 ###")
    c.execute("""
        SELECT 
            CASE 
                WHEN capacity_mw IS NULL OR capacity_mw <= 0 THEN '未知/零值'
                WHEN capacity_mw <= 100 THEN '0-100 MW'
                WHEN capacity_mw <= 500 THEN '100-500 MW'
                WHEN capacity_mw <= 1000 THEN '500-1000 MW'
                WHEN capacity_mw <= 5000 THEN '1000-5000 MW'
                WHEN capacity_mw <= 10000 THEN '5000-10000 MW'
                WHEN capacity_mw <= 100000 THEN '10000-100000 MW'
                WHEN capacity_mw <= 1000000 THEN '10万-100万 MW'
                ELSE '> 100万 MW'
            END as bucket,
            COUNT(*) as cnt,
            SUM(capacity_mw) as total_mw
        FROM extracted_projects 
        WHERE country_iso = 'SAU'
        GROUP BY bucket
        ORDER BY MIN(capacity_mw)
    """)
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]} 条, 合计 {row[2]:,.0f} MW")
    
    # 3. 所有异常容量记录
    print("\n### 3. 容量 > 10,000 MW 的异常记录 ###")
    c.execute("""
        SELECT id, project_name, capacity_mw, capacity_details, project_stage, category, 
               involvement_type, implementing_entity, total_investment_usd
        FROM extracted_projects 
        WHERE country_iso = 'SAU' AND capacity_mw IS NOT NULL 
          AND CAST(capacity_mw AS REAL) > 10000
        ORDER BY CAST(capacity_mw AS REAL) DESC
    """)
    rows = c.fetchall()
    for r in rows:
        print(f"  ID:{r[0]} | {fmt_cap(r[2])} MW | {r[4]} | {r[5]} | {str(r[1])[:40]}")
        print(f"     Involve:{r[6]} | Entity:{r[7]}")
        print(f"     Details: {r[3]}")
        print()
    
    # 4. 按类别统计容量
    print("\n### 4. 按类别统计沙特容量 ###")
    c.execute("""
        SELECT category, COUNT(*) as cnt, 
               SUM(CASE WHEN capacity_mw IS NOT NULL AND capacity_mw > 0 THEN CAST(capacity_mw AS REAL) ELSE 0 END) as total_mw
        FROM extracted_projects 
        WHERE country_iso = 'SAU'
        GROUP BY category 
        ORDER BY total_mw DESC
    """)
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]} 条, 累计 {fmt_cap(row[2])} MW")
    
    # 5. 光伏项目容量分布
    print("\n### 5. 沙特光伏项目容量分布 ###")
    c.execute("""
        SELECT capacity_mw, COUNT(*) as cnt
        FROM extracted_projects 
        WHERE country_iso = 'SAU' AND category = '光伏' AND capacity_mw IS NOT NULL AND capacity_mw > 0
        GROUP BY capacity_mw 
        ORDER BY CAST(capacity_mw AS REAL) DESC
        LIMIT 30
    """)
    for cap, cnt in c.fetchall():
        print(f"  {fmt_cap(cap)} MW: {cnt} 条")
    
    conn.close()

def analyze_s02a():
    conn = sqlite3.connect(r"e:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db")
    c = conn.cursor()
    
    print("\n\n" + "=" * 80)
    print("S02A BRI_RE_DATABASE - 沙特容量数据分析")
    print("=" * 80)
    
    # 1. 按canonical project统计唯一容量值
    print("\n### 1. 沙特canonical项目容量分布 Top50 ###")
    c.execute("""
        SELECT pe.capacity_mw, COUNT(DISTINCT cp.id) as canonical_cnt
        FROM project_events pe 
        JOIN canonical_projects cp ON pe.canonical_id = cp.id
        WHERE cp.country_iso = 'SAU' 
          AND pe.capacity_mw IS NOT NULL AND pe.capacity_mw > 0
        GROUP BY pe.capacity_mw 
        ORDER BY CAST(pe.capacity_mw AS REAL) DESC
        LIMIT 50
    """)
    for row in c.fetchall():
        print(f"  {fmt_cap(row[0])} MW: {row[1]} canonical项目")
    
    # 2. 按canonical project分档
    print("\n### 2. 沙特canonical项目分档统计 ###")
    c.execute("""
        SELECT 
            CASE 
                WHEN pe.capacity_mw IS NULL OR pe.capacity_mw <= 0 THEN '未知/零值'
                WHEN CAST(pe.capacity_mw AS REAL) <= 100 THEN '0-100 MW'
                WHEN CAST(pe.capacity_mw AS REAL) <= 500 THEN '100-500 MW'
                WHEN CAST(pe.capacity_mw AS REAL) <= 1000 THEN '500-1000 MW'
                WHEN CAST(pe.capacity_mw AS REAL) <= 5000 THEN '1000-5000 MW'
                WHEN CAST(pe.capacity_mw AS REAL) <= 10000 THEN '5000-10000 MW'
                WHEN CAST(pe.capacity_mw AS REAL) <= 100000 THEN '10000-100000 MW'
                WHEN CAST(pe.capacity_mw AS REAL) <= 1000000 THEN '10万-100万 MW'
                ELSE '> 100万 MW'
            END as bucket,
            COUNT(DISTINCT cp.id) as canonical_cnt,
            SUM(pe.capacity_mw) as total_mw
        FROM project_events pe 
        JOIN canonical_projects cp ON pe.canonical_id = cp.id
        WHERE cp.country_iso = 'SAU'
        GROUP BY bucket
        ORDER BY MIN(CAST(pe.capacity_mw AS REAL))
    """)
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]} canonical项目, 合计 {fmt_cap(row[2])} MW")
    
    # 3. 列出所有容量异常的canonical项目
    print("\n### 3. 容量 > 10,000 MW 的异常记录详情 ###")
    c.execute("""
        SELECT cp.id, cp.project_name, cp.project_name_en, cp.category,
               pe.capacity_mw, pe.capacity_details, pe.project_stage,
               pe.total_investment_usd, ce.implementing_entity_en
        FROM project_events pe 
        JOIN canonical_projects cp ON pe.canonical_id = cp.id
        LEFT JOIN chinese_entities ce ON ce.event_id = pe.id AND ce.id = (
            SELECT MIN(id) FROM chinese_entities WHERE event_id = pe.id
        )
        WHERE cp.country_iso = 'SAU' 
          AND pe.capacity_mw IS NOT NULL AND CAST(pe.capacity_mw AS REAL) > 10000
        ORDER BY CAST(pe.capacity_mw AS REAL) DESC
    """)
    rows = c.fetchall()
    for r in rows:
        name_en = r[2] if r[2] else "(无英文名)"
        print(f"  CP#{r[0]} | {fmt_cap(r[4])} MW | {r[6]} | {r[3]}")
        print(f"    Name: {r[1]}")
        print(f"    NameEn: {name_en}")
        print(f"    Details: {r[5]}")
        print(f"    Investment: {fmt_inv(r[7])}")
        print(f"    Entity: {r[8]}")
        print()
    
    # 4. 正常范围项目统计
    print("\n### 4. 正常范围项目统计（容量 ≤ 5000 MW）###")
    c.execute("""
        SELECT COUNT(DISTINCT cp.id) as normal_projects,
               SUM(pe.capacity_mw) as total_normal_mw,
               AVG(CAST(pe.capacity_mw AS REAL)) as avg_mw
        FROM project_events pe 
        JOIN canonical_projects cp ON pe.canonical_id = cp.id
        WHERE cp.country_iso = 'SAU' 
          AND pe.capacity_mw IS NOT NULL AND pe.capacity_mw > 0 AND CAST(pe.capacity_mw AS REAL) <= 5000
    """)
    row = c.fetchone()
    print(f"  项目数: {row[0]}")
    print(f"  合计容量: {fmt_cap(row[1])} MW")
    print(f"  平均容量: {row[2]:,.0f} MW" if row[2] else "  平均容量: NULL")
    
    # 5. 容量最大的前20正常项目
    print("\n### 5. 沙特容量最大的正常范围项目（Top 20）###")
    c.execute("""
        SELECT cp.project_name_en, cp.category, pe.capacity_mw, pe.project_stage
        FROM project_events pe 
        JOIN canonical_projects cp ON pe.canonical_id = cp.id
        WHERE cp.country_iso = 'SAU' 
          AND pe.capacity_mw IS NOT NULL AND pe.capacity_mw > 0 AND CAST(pe.capacity_mw AS REAL) <= 5000
        ORDER BY CAST(pe.capacity_mw AS REAL) DESC
        LIMIT 20
    """)
    for r in c.fetchall():
        name = r[0] if r[0] else "(无英文名)"
        print(f"  {r[1]:>8} | {fmt_cap(r[2])} MW | {r[3]:>6} | {name[:60]}")
    
    conn.close()

if __name__ == "__main__":
    analyze_s01b()
    analyze_s02a()