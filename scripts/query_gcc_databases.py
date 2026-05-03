"""Temporary script to explore GCC data in both databases."""
import sqlite3
import sys

# GCC country ISO codes
GCC_ISO = ['SAU', 'ARE', 'QAT', 'KWT', 'BHR', 'OMN']
GCC_NAMES = {
    'SAU': 'Saudi Arabia', 'ARE': 'United Arab Emirates', 'QAT': 'Qatar',
    'KWT': 'Kuwait', 'BHR': 'Bahrain', 'OMN': 'Oman'
}

def query_bri_db(path):
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    
    # Get tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cursor.fetchall()]
    print("=" * 80)
    print(f"Database: {path}")
    print(f"Tables: {tables}")
    
    # Explore each table
    for table in tables:
        cursor.execute(f"PRAGMA table_info({table})")
        cols = [(r[1], r[2]) for r in cursor.fetchall()]
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"\n--- {table} ({count} rows) ---")
        print(f"  Columns: {[c[0] for c in cols]}")
    
    # GCC queries
    print("\n" + "=" * 80)
    print("GCC COUNTRY QUERIES")
    print("=" * 80)
    
    for table in tables:
        # Check for country_iso column
        cursor.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in cursor.fetchall()]
        has_iso = 'country_iso' in cols
        
        if has_iso:
            placeholders = ','.join(['?' for _ in GCC_ISO])
            cursor.execute(f"SELECT country_iso, COUNT(*) FROM {table} WHERE country_iso IN ({placeholders}) GROUP BY country_iso", GCC_ISO)
            results = cursor.fetchall()
            if results:
                print(f"\n  {table} - by country_iso:")
                total = 0
                for iso, cnt in results:
                    name = GCC_NAMES.get(iso, iso)
                    print(f"    {iso} ({name}): {cnt}")
                    total += cnt
                print(f"    TOTAL GCC: {total}")
    
    conn.close()
    return

def query_projects_db(path):
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cursor.fetchall()]
    print("=" * 80)
    print(f"Database: {path}")
    print(f"Tables: {tables}")
    
    for table in tables:
        cursor.execute(f"PRAGMA table_info({table})")
        cols = [(r[1], r[2]) for r in cursor.fetchall()]
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"\n--- {table} ({count} rows) ---")
        print(f"  Columns: {[c[0] for c in cols]}")
    
    # GCC queries
    print("\n" + "=" * 80)
    print("GCC COUNTRY QUERIES")
    print("=" * 80)
    
    for table in tables:
        cursor.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in cursor.fetchall()]
        has_iso = 'country_iso' in cols
        
        if has_iso:
            placeholders = ','.join(['?' for _ in GCC_ISO])
            cursor.execute(f"SELECT country_iso, COUNT(*) FROM {table} WHERE country_iso IN ({placeholders}) GROUP BY country_iso", GCC_ISO)
            results = cursor.fetchall()
            if results:
                print(f"\n  {table} - by country_iso:")
                total = 0
                for iso, cnt in results:
                    name = GCC_NAMES.get(iso, iso)
                    print(f"    {iso} ({name}): {cnt}")
                    total += cnt
                print(f"    TOTAL GCC: {total}")
    
    conn.close()
    return

if __name__ == "__main__":
    query_projects_db(r"e:\Projects\BRI_RE_Database_v2\s01b_project_extractor\data\projects.db")
    print("\n\n")
    query_bri_db(r"e:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db")