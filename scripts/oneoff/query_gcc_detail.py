"""Detailed GCC data exploration."""
import sqlite3

GCC_ISO = ['SAU', 'ARE', 'QAT', 'KWT', 'BHR', 'OMN']
PLACEHOLDERS = ','.join(['?'] * len(GCC_ISO))

def query_projects_db():
    conn = sqlite3.connect(r"e:\Projects\BRI_RE_Database_v2\s01b_project_extractor\data\projects.db")
    c = conn.cursor()
    tbl = "extracted_projects"
    base = f"WHERE country_iso IN ({PLACEHOLDERS})"
    
    def q(sql_template, *extra_params):
        """Execute query with GCC_ISO params plus any extra params."""
        c.execute(sql_template, tuple(GCC_ISO) + extra_params)
        return c.fetchall()
    
    print("=" * 80)
    print("S01B EXTRACTED_PROJECTS - GCC DETAILED ANALYSIS")
    print("=" * 80)
    
    # 1. By country and category
    print("\n### 1. GCC by country x category ###")
    for row in q(f"SELECT country_iso, category, COUNT(*) FROM {tbl} {base} GROUP BY country_iso, category ORDER BY country_iso, COUNT(*) DESC"):
        print(f"  {row[0]} | {row[1]}: {row[2]}")
    
    # 2. By category (aggregate)
    print("\n### 2. GCC by category (total) ###")
    for row in q(f"SELECT category, COUNT(*) FROM {tbl} {base} GROUP BY category ORDER BY COUNT(*) DESC"):
        print(f"  {row[0]}: {row[1]}")
    
    # 3. By involvement_type
    print("\n### 3. GCC by involvement_type ###")
    for row in q(f"SELECT involvement_type, COUNT(*) FROM {tbl} {base} GROUP BY involvement_type ORDER BY COUNT(*) DESC"):
        print(f"  {row[0]}: {row[1]}")
    
    # 4. By project_stage
    print("\n### 4. GCC by project_stage ###")
    for row in q(f"SELECT project_stage, COUNT(*) FROM {tbl} {base} GROUP BY project_stage ORDER BY COUNT(*) DESC"):
        print(f"  {row[0]}: {row[1]}")
    
    # 5. By record_type
    print("\n### 5. GCC by record_type ###")
    for row in q(f"SELECT record_type, COUNT(*) FROM {tbl} {base} GROUP BY record_type ORDER BY COUNT(*) DESC"):
        print(f"  {row[0]}: {row[1]}")
    
    # 6. By is_central_soe
    print("\n### 6. GCC by is_central_soe ###")
    for row in q(f"SELECT is_central_soe, COUNT(*) FROM {tbl} {base} GROUP BY is_central_soe ORDER BY COUNT(*) DESC"):
        label = {1: "是央企", 0: "非央企", None: "未知"}.get(row[0], str(row[0]))
        print(f"  {label}: {row[1]}")
    
    # 7. By involvement x SOE
    print("\n### 7. GCC: EPC involvement x is_central_soe ###")
    for row in q(f"SELECT involvement_type, is_central_soe, COUNT(*) FROM {tbl} {base} AND involvement_type LIKE '%EPC%' GROUP BY involvement_type, is_central_soe ORDER BY COUNT(*) DESC"):
        soe_label = {1: "央企", 0: "非央企", None: "未知"}.get(row[1], str(row[1]))
        print(f"  {row[0]} | {soe_label}: {row[2]}")
    
    # 8. EPC including all involvement sub-types
    print("\n### 8. GCC: EPC vs non-EPC total ###")
    for row in q(f"SELECT SUM(CASE WHEN involvement_type LIKE '%EPC%' THEN 1 ELSE 0 END) as epc, SUM(CASE WHEN involvement_type NOT LIKE '%EPC%' THEN 1 ELSE 0 END) as non_epc, COUNT(*) as total FROM {tbl} {base}"):
        print(f"  EPC-related: {row[0]} ({row[0]/row[2]*100:.1f}%), Non-EPC: {row[1]} ({row[1]/row[2]*100:.1f}%), Total: {row[2]}")
    
    # 9. Top implementing entities
    print("\n### 9. GCC: Top 15 implementing entities ###")
    for row in q(f"SELECT implementing_entity, COUNT(*) as cnt FROM {tbl} {base} AND implementing_entity != 'Unknown' GROUP BY implementing_entity ORDER BY cnt DESC LIMIT 15"):
        print(f"  {row[0]}: {row[1]}")
    
    # 10. Financing models
    print("\n### 10. GCC: Financing models ###")
    for row in q(f"SELECT financing_model, COUNT(*) FROM {tbl} {base} GROUP BY financing_model ORDER BY COUNT(*) DESC"):
        print(f"  [{row[0] or 'NULL'}]: {row[1]}")
    
    # 11. Capacity stats by country
    print("\n### 11. GCC: Capacity stats by country ###")
    for row in q(f"SELECT country_iso, COUNT(*) as projects, SUM(CASE WHEN capacity_mw IS NOT NULL AND capacity_mw > 0 THEN capacity_mw ELSE 0 END) as total_mw, AVG(CASE WHEN capacity_mw IS NOT NULL AND capacity_mw > 0 THEN capacity_mw ELSE NULL END) as avg_mw FROM {tbl} {base} GROUP BY country_iso ORDER BY total_mw DESC"):
        print(f"  {row[0]}: {row[1]} projects, {row[2]:.0f} MW total, {row[3]:.0f} MW avg (where known)")
    
    # 12. Total investment
    print("\n### 12. GCC: Total investment by country (USD millions) ###")
    for row in q(f"SELECT country_iso, COUNT(*) as projects, SUM(CASE WHEN total_investment_usd IS NOT NULL AND total_investment_usd > 0 THEN total_investment_usd ELSE 0 END) as total_inv, COUNT(CASE WHEN total_investment_usd IS NOT NULL AND total_investment_usd > 0 THEN 1 ELSE NULL END) as known FROM {tbl} {base} GROUP BY country_iso ORDER BY total_inv DESC"):
        print(f"  {row[0]}: {row[1]} projects, ${row[2]:.0f}M total (known: {row[3]})")
    
    conn.close()

def query_bri_db_detail():
    conn = sqlite3.connect(r"e:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db")
    c = conn.cursor()
    
    print("\n\n")
    print("=" * 80)
    print("S02A BRI_RE_DATABASE - GCC DETAILED ANALYSIS")
    print("=" * 80)
    
    # canonical_projects by country x category
    print("\n### Canonical Projects - GCC by country x category ###")
    c.execute(f"SELECT country_iso, category, COUNT(*) FROM canonical_projects WHERE country_iso IN ({PLACEHOLDERS}) GROUP BY country_iso, category ORDER BY country_iso, COUNT(*) DESC", GCC_ISO)
    for row in c.fetchall():
        print(f"  {row[0]} | {row[1]}: {row[2]}")
    
    print("\n### Canonical Projects - GCC by category (total) ###")
    c.execute(f"SELECT category, COUNT(*) FROM canonical_projects WHERE country_iso IN ({PLACEHOLDERS}) GROUP BY category ORDER BY COUNT(*) DESC", GCC_ISO)
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]}")
    
    # project_events by stage
    print("\n### Project Events - GCC by stage ###")
    c.execute(f"SELECT pe.project_stage, COUNT(*) FROM project_events pe JOIN canonical_projects cp ON pe.canonical_id = cp.id WHERE cp.country_iso IN ({PLACEHOLDERS}) GROUP BY pe.project_stage ORDER BY COUNT(*) DESC", GCC_ISO)
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]}")
    
    # involvement_type from chinese_entities
    print("\n### Chinese Entities - GCC by involvement_type ###")
    c.execute(f"SELECT ce.involvement_type, COUNT(*) FROM chinese_entities ce JOIN project_events pe ON ce.event_id = pe.id JOIN canonical_projects cp ON pe.canonical_id = cp.id WHERE cp.country_iso IN ({PLACEHOLDERS}) GROUP BY ce.involvement_type ORDER BY COUNT(*) DESC", GCC_ISO)
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]}")
    
    # EPC vs non-EPC in chinese_entities
    print("\n### Chinese Entities - GCC EPC vs non-EPC ###")
    c.execute(f"SELECT SUM(CASE WHEN ce.involvement_type LIKE '%EPC%' THEN 1 ELSE 0 END) as epc, COUNT(*) as total FROM chinese_entities ce JOIN project_events pe ON ce.event_id = pe.id JOIN canonical_projects cp ON pe.canonical_id = cp.id WHERE cp.country_iso IN ({PLACEHOLDERS})", GCC_ISO)
    row = c.fetchone()
    print(f"  EPC-related: {row[0]} / {row[1]} ({row[0]/row[1]*100:.1f}%)")
    
    # SOE in GCC
    print("\n### Chinese Entities - GCC by is_central_soe ###")
    c.execute(f"SELECT ce.is_central_soe, COUNT(*) FROM chinese_entities ce JOIN project_events pe ON ce.event_id = pe.id JOIN canonical_projects cp ON pe.canonical_id = cp.id WHERE cp.country_iso IN ({PLACEHOLDERS}) GROUP BY ce.is_central_soe ORDER BY COUNT(*) DESC", GCC_ISO)
    for row in c.fetchall():
        label = {1: "是央企", 0: "非央企", None: "未知"}.get(row[0], str(row[0]))
        print(f"  {label}: {row[1]}")
    
    # EPC + SOE breakdown
    print("\n### Chinese Entities - GCC: EPC involvement x SOE ###")
    c.execute(f"SELECT ce.involvement_type, ce.is_central_soe, COUNT(*) FROM chinese_entities ce JOIN project_events pe ON ce.event_id = pe.id JOIN canonical_projects cp ON pe.canonical_id = cp.id WHERE cp.country_iso IN ({PLACEHOLDERS}) AND ce.involvement_type LIKE '%EPC%' GROUP BY ce.involvement_type, ce.is_central_soe ORDER BY COUNT(*) DESC", GCC_ISO)
    for row in c.fetchall():
        soe_label = {1: "央企", 0: "非央企", None: "未知"}.get(row[1], str(row[1]))
        print(f"  {row[0]} | {soe_label}: {row[2]}")
    
    # Top implementing entities in GCC
    print("\n### Top 15 Implementing Entities in GCC ###")
    c.execute(f"SELECT ce.implementing_entity, COUNT(*) as cnt FROM chinese_entities ce JOIN project_events pe ON ce.event_id = pe.id JOIN canonical_projects cp ON pe.canonical_id = cp.id WHERE cp.country_iso IN ({PLACEHOLDERS}) AND ce.implementing_entity != 'Unknown' GROUP BY ce.implementing_entity ORDER BY cnt DESC LIMIT 15", GCC_ISO)
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]}")
    
    # Capacity stats
    print("\n### Project Events - GCC capacity stats ###")
    c.execute(f"SELECT cp.country_iso, COUNT(DISTINCT cp.id) as canonicals, SUM(CASE WHEN pe.capacity_mw IS NOT NULL AND pe.capacity_mw > 0 THEN pe.capacity_mw ELSE 0 END) as total_mw FROM project_events pe JOIN canonical_projects cp ON pe.canonical_id = cp.id WHERE cp.country_iso IN ({PLACEHOLDERS}) GROUP BY cp.country_iso ORDER BY total_mw DESC", GCC_ISO)
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]} canonical projects, {row[2]:.0f} MW total")
    
    # Financing models in events
    print("\n### Project Events - GCC financing models ###")
    c.execute(f"SELECT pe.financing_model, COUNT(*) FROM project_events pe JOIN canonical_projects cp ON pe.canonical_id = cp.id WHERE cp.country_iso IN ({PLACEHOLDERS}) GROUP BY pe.financing_model ORDER BY COUNT(*) DESC", GCC_ISO)
    for row in c.fetchall():
        print(f"  [{row[0] or 'NULL'}]: {row[1]}")
    
    conn.close()

if __name__ == "__main__":
    query_projects_db()
    query_bri_db_detail()