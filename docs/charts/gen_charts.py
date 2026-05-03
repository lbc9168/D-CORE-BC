"""Generate GCC renewable energy charts for Oxford Energy Forum article."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import sqlite3

# ── font setup ──────────────────────────────────────────────────────────
import matplotlib.font_manager as fm
cjk_candidates = ['Microsoft YaHei', 'SimHei', 'SimSun', 'Arial Unicode MS', 'DejaVu Sans']
available = {f.name for f in fm.fontManager.ttflist}
chosen = next((f for f in cjk_candidates if f in available), None)
if chosen:
    plt.rcParams['font.family'] = chosen
plt.rcParams['axes.unicode_minus'] = False

OUT = "E:/Projects/BRI_RE_Database_v2/docs/charts"
DB = "E:/Projects/BRI_RE_Database_v2/s02a_duplicate_merger/data/db/bri_re_database.db"
GCC_NAMES = {'SAU': 'Saudi Arabia', 'ARE': 'UAE', 'OMN': 'Oman',
             'BHR': 'Bahrain', 'KWT': 'Kuwait', 'QAT': 'Qatar'}

conn = sqlite3.connect(DB)
conn.text_factory = str
c = conn.cursor()


# ─────────────────────────────────────────────────────────────────────
# CHART 1: GCC countries × energy type (stacked bar)
# ─────────────────────────────────────────────────────────────────────
c.execute("""
SELECT cp.country_iso, cp.category, COUNT(*) as n
FROM canonical_projects cp
WHERE cp.country_iso IN ('SAU','ARE','OMN','BHR','KWT','QAT')
  AND cp.category NOT IN ('火电', 'Unknown')
GROUP BY cp.country_iso, cp.category ORDER BY cp.country_iso, n DESC
""")
rows = c.fetchall()

cats_order = ['光伏', '综合能源', '风电', '新能源制造业', '储能', '输变电建设']
iso_order = ['SAU', 'ARE', 'OMN', 'BHR', 'KWT', 'QAT']
data_c1 = {iso: {cat: 0 for cat in cats_order} for iso in iso_order}
for iso, cat, n in rows:
    if cat in cats_order:
        data_c1[iso][cat] += n

cat_labels_en = {
    '光伏': 'Solar PV', '综合能源': 'Hybrid/Integrated', '风电': 'Wind',
    '新能源制造业': 'RE Manufacturing', '储能': 'Storage', '输变电建设': 'Grid/T&D'
}
colors_c1 = ['#f4a828', '#2196F3', '#4CAF50', '#FF5722', '#00BCD4', '#9C27B0']

fig, ax = plt.subplots(figsize=(10, 5.5))
bottoms = np.zeros(len(iso_order))
for i, cat in enumerate(cats_order):
    vals = np.array([data_c1[iso][cat] for iso in iso_order])
    ax.bar([GCC_NAMES[iso] for iso in iso_order], vals, bottom=bottoms,
           color=colors_c1[i], label=cat_labels_en[cat], edgecolor='white', linewidth=0.5)
    for j, v in enumerate(vals):
        if v > 2:
            ax.text(j, bottoms[j] + v / 2, str(v), ha='center', va='center',
                    fontsize=7.5, color='white', fontweight='bold')
    bottoms += vals

ax.set_title('Chinese Renewable Energy Projects in GCC Countries by Technology',
             fontsize=13, fontweight='bold', pad=12)
ax.set_ylabel('Number of Projects', fontsize=11)
ax.legend(loc='upper right', fontsize=8.5, framealpha=0.9, ncol=2)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.set_ylim(0, max(bottoms) * 1.22)
plt.tight_layout()
plt.savefig(f"{OUT}/chart1_gcc_country_technology.png", dpi=150, bbox_inches='tight')
plt.close()
print("Chart 1 saved")


# ─────────────────────────────────────────────────────────────────────
# CHART 2: Chinese company role distribution — stacked SOE vs non-SOE
# ─────────────────────────────────────────────────────────────────────
# (role, soe_count, non_soe_count) — sorted ascending by total
roles_data2 = [
    ('BOT/BOOT',                  6,  1),
    ('Investment+EPC',            3,  6),
    ('Financier',                15, 11),
    ('Design / Construction Mgmt',24,  8),
    ('Equity Investor',          22, 28),
    ('Equipment Supplier',       11, 71),
    ('EPC Contractor',          183, 65),
]
labels_r2  = [r[0] for r in roles_data2]
soe_vals   = np.array([r[1] for r in roles_data2])
priv_vals  = np.array([r[2] for r in roles_data2])
totals_r2  = soe_vals + priv_vals
grand_total = totals_r2.sum()

SOE_COL  = '#1565C0'
PRIV_COL = '#F57C00'

fig, ax = plt.subplots(figsize=(10, 4.5))
bars_soe  = ax.barh(labels_r2, soe_vals,  color=SOE_COL,  edgecolor='white', height=0.65, label='SOE')
bars_priv = ax.barh(labels_r2, priv_vals, color=PRIV_COL, edgecolor='white', height=0.65,
                    left=soe_vals, label='Private / Mixed')

# label each segment if wide enough
for i, (s, p) in enumerate(zip(soe_vals, priv_vals)):
    y = i
    h = 0.65
    if s >= 8:
        ax.text(s / 2, y, str(s), ha='center', va='center', fontsize=8,
                color='white', fontweight='bold')
    if p >= 8:
        ax.text(s + p / 2, y, str(p), ha='center', va='center', fontsize=8,
                color='white', fontweight='bold')

# total + share label at bar end
for i, tot in enumerate(totals_r2):
    ax.text(tot + 3, i, f'{tot}  ({tot / grand_total * 100:.0f}%)',
            va='center', fontsize=9)

ax.set_title("Chinese Firms' Roles in GCC RE Projects (SOE vs Private)",
             fontsize=13, fontweight='bold', pad=12)
ax.set_xlabel('Number of Entity-Event Records', fontsize=10)
ax.set_xlim(0, 310)
soe_patch  = mpatches.Patch(color=SOE_COL,  label='State-owned Enterprise (SOE)')
priv_patch = mpatches.Patch(color=PRIV_COL, label='Private / Mixed Enterprise')
ax.legend(handles=[soe_patch, priv_patch], loc='lower right', fontsize=9)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig(f"{OUT}/chart2_gcc_role_distribution.png", dpi=150, bbox_inches='tight')
plt.close()
print("Chart 2 saved")


# ─────────────────────────────────────────────────────────────────────
# CHART 3: Top 12 Chinese companies in GCC (SOE vs private)
# ─────────────────────────────────────────────────────────────────────
companies = [
    ('Sungrow', 5, 0),
    ('TBEA', 6, 0),
    ('State Grid (SGCC)', 6, 1),
    ('CHN Energy', 6, 1),
    ('China State Construction', 7, 1),
    ('Goldwind', 9, 0),
    ('Sinomach', 12, 1),
    ('JinkoSolar / JinkoPower', 40, 0),
    ('Shanghai Electric', 28, 0),
    ('SPIC', 34, 1),
    ('PowerChina', 59, 1),
    ('CEEC (China Energy Eng.)', 109, 1),
]
companies.sort(key=lambda x: x[1])
comp_labels, comp_vals, comp_soe = zip(*companies)
comp_colors = ['#1565C0' if s else '#F57C00' for s in comp_soe]

fig, ax = plt.subplots(figsize=(10, 6))
bars = ax.barh(comp_labels, comp_vals, color=comp_colors, edgecolor='white', height=0.65)
for bar, v in zip(bars, comp_vals):
    ax.text(v + 1, bar.get_y() + bar.get_height() / 2, str(v),
            va='center', fontsize=9)
ax.set_title('Top Chinese Companies in GCC Renewable Energy Projects',
             fontsize=13, fontweight='bold', pad=12)
ax.set_xlabel('Number of Project Participations', fontsize=10)
ax.set_xlim(0, 165)
soe_patch = mpatches.Patch(color='#1565C0', label='State-owned Enterprise (SOE)')
priv_patch = mpatches.Patch(color='#F57C00', label='Private / Mixed Enterprise')
ax.legend(handles=[soe_patch, priv_patch], loc='lower right', fontsize=9)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig(f"{OUT}/chart3_gcc_top_companies.png", dpi=150, bbox_inches='tight')
plt.close()
print("Chart 3 saved")


# ─────────────────────────────────────────────────────────────────────
# CHART 4: Project pipeline stage distribution
# ─────────────────────────────────────────────────────────────────────
stage_data = [
    ('Contract\nSigned', 98, '#1976D2'),
    ('Awarded /\nBid Won', 76, '#42A5F5'),
    ('Under\nDevelopment', 49, '#B0BEC5'),
    ('Operational', 37, '#4CAF50'),
    ('Under\nConstruction', 30, '#FFA726'),
    ('Construction\nStarted', 20, '#8BC34A'),
    ('Completed', 6, '#2E7D32'),
]
slabels, svals, scolors = zip(*stage_data)

fig, ax = plt.subplots(figsize=(10, 4.5))
bars = ax.bar(slabels, svals, color=scolors, edgecolor='white', width=0.65)
for bar, v in zip(bars, svals):
    ax.text(bar.get_x() + bar.get_width() / 2, v + 1.5, str(v),
            ha='center', va='bottom', fontsize=10, fontweight='bold')
ax.set_title('GCC Project Pipeline: Distribution by Stage',
             fontsize=13, fontweight='bold', pad=12)
ax.set_ylabel('Number of Project Events', fontsize=10)
ax.set_ylim(0, 130)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig(f"{OUT}/chart4_gcc_project_stages.png", dpi=150, bbox_inches='tight')
plt.close()
print("Chart 4 saved")


# ─────────────────────────────────────────────────────────────────────
# CHART 5: Annual GCC RE project count & contracted capacity by technology
#          Top panel: project count (all stages); Bottom: capacity GW (signed+)
# ─────────────────────────────────────────────────────────────────────
years5  = ['2018','2019','2020','2021','2022','2023','2024','2025','2026*']
n = len(years5)

# ── technology order & colors (consistent with chart1) ───────────────
cats5 = ['光伏','综合能源','风电','新能源制造业','储能','输变电建设']
cat_en5 = {'光伏':'Solar PV','综合能源':'Hybrid/Integrated','风电':'Wind',
           '新能源制造业':'RE Manufacturing','储能':'Storage','输变电建设':'Grid/T&D'}
colors5 = ['#f4a828','#2196F3','#4CAF50','#FF5722','#00BCD4','#9C27B0']

# ── project count matrix ─────────────────────────────────────────────
count_raw = {
    '光伏':          [1,  4, 13,  7, 17, 29, 31, 40,  9],
    '综合能源':      [4,  0,  4,  8,  9, 10, 22, 21,  4],
    '风电':          [0,  1,  0,  0,  1,  4, 11,  8,  5],
    '新能源制造业':  [0,  0,  0,  1,  0,  3, 15,  2,  1],
    '储能':          [0,  0,  0,  1,  0,  0,  3, 10,  2],
    '输变电建设':    [0,  1,  2,  1,  1,  0,  6,  6,  3],
}

# ── contracted capacity matrix (GW, signed+, <=5GW/project) ──────────
cap_raw = {
    '光伏':          [0,     1.55,  10.49,  2.919, 15.477, 17.543, 34.255, 41.593,  6.620],
    '综合能源':      [0.10,  0,      2.85,  3.711,  6.590, 10.596,  2.610,  8.863,  5.500],
    '风电':          [0,     0.400,  0,      0,      0,     1.995,  2.304,  8.179,  5.000],
    '新能源制造业':  [0,     0,      0,      0,      0,     0,      0,      0,      0    ],
    '储能':          [0,     0,      0,      0,      0,     0,      0,      0,      0    ],
    '输变电建设':    [0,     0,      0,      0,      0,     0,      0,      0,      0    ],
}

x = np.arange(n)
w = 0.70

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
fig.subplots_adjust(hspace=0.08)

# ── Top: project count ────────────────────────────────────────────────
btm1 = np.zeros(n)
for cat, col in zip(cats5, colors5):
    vals = np.array(count_raw[cat], dtype=float)
    ax1.bar(x, vals, w, bottom=btm1, color=col, edgecolor='white',
            linewidth=0.4, label=cat_en5[cat])
    for i, v in enumerate(vals):
        if v >= 4:
            ax1.text(i, btm1[i] + v / 2, str(int(v)), ha='center', va='center',
                     fontsize=7, color='white', fontweight='bold')
    btm1 += vals

for i, tot in enumerate(btm1):
    ax1.text(i, tot + 0.8, str(int(tot)), ha='center', va='bottom',
             fontsize=8.5, fontweight='bold', color='#333333')

ax1.set_title('Annual Chinese RE Project Activity in GCC: Count and Contracted Capacity by Technology',
              fontsize=13, fontweight='bold', pad=12)
ax1.set_ylabel('Number of Projects', fontsize=10)
ax1.set_ylim(0, max(btm1) * 1.20)
ax1.legend(loc='upper left', fontsize=8.5, ncol=2, framealpha=0.9)
ax1.spines['top'].set_visible(False)
ax1.spines['right'].set_visible(False)

# ── Bottom: contracted capacity GW ────────────────────────────────────
btm2 = np.zeros(n)
for cat, col in zip(cats5, colors5):
    vals = np.array(cap_raw[cat], dtype=float)
    ax2.bar(x, vals, w, bottom=btm2, color=col, edgecolor='white',
            linewidth=0.4, label=cat_en5[cat])
    for i, v in enumerate(vals):
        if v >= 2.5:
            ax2.text(i, btm2[i] + v / 2, f'{v:.0f}', ha='center', va='center',
                     fontsize=7, color='white', fontweight='bold')
    btm2 += vals

ax2.set_ylabel('Contracted Capacity (GW)', fontsize=10)
ax2.set_xticks(x)
ax2.set_xticklabels(years5, fontsize=10)
ax2.set_ylim(0, max(btm2) * 1.15)
ax2.spines['top'].set_visible(False)
ax2.spines['right'].set_visible(False)

plt.tight_layout()
fig.subplots_adjust(bottom=0.07)
fig.text(0.5, 0.01,
         '* 2026 partial year  |  Capacity: signed/awarded/construction/operational projects only; single projects capped at 5 GW',
         ha='center', va='bottom', fontsize=7.5, color='gray')
plt.savefig(f"{OUT}/chart5_gcc_timeline.png", dpi=150, bbox_inches='tight')
plt.close()
print("Chart 5 saved")


# ─────────────────────────────────────────────────────────────────────
# CHART 6: GCC vs World — Annual Solar PV project count & capacity
#          (grouped color bar: GCC=dark blue, Rest of World=light blue)
#          Top panel: project count; Bottom panel: contracted capacity GW
# ─────────────────────────────────────────────────────────────────────
years6 = ['2018', '2019', '2020', '2021', '2022', '2023', '2024', '2025', '2026*']
# Project count (signed+ stage)
world_count = [44, 97, 92, 80, 137, 247, 257, 265, 83]
gcc_count   = [0,   3,  10,  7,  16,  27,  25,  34,   8]
row_count   = [w - g for w, g in zip(world_count, gcc_count)]

# Contracted capacity GW (signed+ stage, <=5GW per project)
world_cap_mw = [4078, 16052, 26515, 18733, 32622, 77947, 78684, 80002, 19029]
gcc_cap_mw   = [0,    1550, 10490,  2919, 15477, 17543, 34255, 41593,  6620]
row_cap_mw   = [w - g for w, g in zip(world_cap_mw, gcc_cap_mw)]
world_cap_gw = [v / 1000 for v in world_cap_mw]
gcc_cap_gw   = [v / 1000 for v in gcc_cap_mw]
row_cap_gw   = [v / 1000 for v in row_cap_mw]

x = np.arange(len(years6))
w = 0.65

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
fig.subplots_adjust(hspace=0.08)

# ── Top: project count ─────────────────────────────────────────────
b1 = ax1.bar(x, row_count, w, color='#90CAF9', label='Rest of World')
b2 = ax1.bar(x, gcc_count, w, bottom=row_count, color='#0D47A1', label='GCC')

for i, (g, tot) in enumerate(zip(gcc_count, world_count)):
    pct = g / tot * 100 if tot else 0
    if pct > 0:
        ax1.text(i, tot + 1.2, f'{pct:.0f}%', ha='center', va='bottom',
                 fontsize=8, color='#0D47A1', fontweight='bold')

ax1.set_ylabel('Number of Projects (signed+)', fontsize=10)
ax1.set_title("GCC Share of Chinese Overseas Solar PV Projects: Annual Count and Contracted Capacity",
              fontsize=13, fontweight='bold', pad=12)
ax1.legend(loc='upper left', fontsize=9)
ax1.spines['top'].set_visible(False)
ax1.spines['right'].set_visible(False)
ax1.set_ylim(0, max(world_count) * 1.25)

# ── Bottom: contracted capacity GW ───────────────────────────────
b3 = ax2.bar(x, row_cap_gw, w, color='#90CAF9', label='Rest of World')
b4 = ax2.bar(x, gcc_cap_gw, w, bottom=row_cap_gw, color='#0D47A1', label='GCC')

for i, (g, tot) in enumerate(zip(gcc_cap_gw, world_cap_gw)):
    pct = g / tot * 100 if tot else 0
    if pct > 0:
        ax2.text(i, tot + 0.5, f'{pct:.0f}%', ha='center', va='bottom',
                 fontsize=8, color='#0D47A1', fontweight='bold')

ax2.set_ylabel('Contracted Capacity (GW)', fontsize=10)
ax2.set_xlabel('')
ax2.set_xticks(x)
ax2.set_xticklabels(years6, fontsize=10)
ax2.legend(loc='upper left', fontsize=9)
ax2.spines['top'].set_visible(False)
ax2.spines['right'].set_visible(False)
ax2.set_ylim(0, max(world_cap_gw) * 1.25)

plt.tight_layout()
fig.subplots_adjust(bottom=0.08)
fig.text(0.5, 0.01,
         '* 2026 partial year  |  Capacity: signed/awarded/construction/operational projects only; single projects capped at 5 GW',
         ha='center', va='bottom', fontsize=7.5, color='gray')
plt.savefig(f"{OUT}/chart6_gcc_vs_world_pv.png", dpi=150, bbox_inches='tight')
plt.close()
print("Chart 6 saved")

conn.close()
print("\nAll 6 charts saved to:", OUT)
