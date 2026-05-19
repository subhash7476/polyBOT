import json, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict

with open('.tmp_res.py') as f:
    data = json.load(f)

resolved = data['resolved']

dates = ['2026-04-28','2026-04-29','2026-04-30','2026-05-01','2026-05-02','2026-05-03']
date_labels = ['Apr 28','Apr 29','Apr 30','May 1','May 2','May 3']

res_by_date = defaultdict(float)
for r in resolved:
    res_by_date[r['date']] += r['pnl']

spread_by_date = {
    '2026-04-28': 28.52,
    '2026-04-29': 20.77,
    '2026-04-30': 29.70,
    '2026-05-01': 36.21,
    '2026-05-02': 62.92,
    '2026-05-03': 11.38,
}
fills_by_date = {
    '2026-04-28': 202,
    '2026-04-29': 199,
    '2026-04-30': 231,
    '2026-05-01': 460,
    '2026-05-02': 1291,
    '2026-05-03': 488,
}

res_vals   = [res_by_date.get(d, 0) for d in dates]
spread_vals= [spread_by_date.get(d, 0) for d in dates]
fill_counts= [fills_by_date.get(d, 0) for d in dates]
cum_spread = np.cumsum(spread_vals)
cum_res    = np.cumsum(res_vals)
cum_comb   = cum_spread + cum_res

BG   = '#0f1117'
PANEL= '#1a1d2e'
TXT  = '#e0e0e0'
GRID = '#2a2a3a'
GRN  = '#26a69a'
RED  = '#ef5350'
BLU  = '#5c9bff'
ORG  = '#ffb74d'
GOLD = '#ffd700'

def style(ax):
    ax.set_facecolor(BG)
    ax.tick_params(colors=TXT, labelsize=9)
    for s in ['top','right']:
        ax.spines[s].set_visible(False)
    for s in ['bottom','left']:
        ax.spines[s].set_color(GRID)
    ax.yaxis.label.set_color(TXT)
    ax.xaxis.label.set_color(TXT)
    ax.title.set_color(TXT)
    ax.grid(axis='y', color=GRID, linestyle='--', alpha=0.5)

fig = plt.figure(figsize=(18, 13), facecolor=BG)
fig.suptitle('Maker Bot Performance  |  Apr 28 - May 3, 2026  (PAPER MODE)',
             fontsize=15, color='white', fontweight='bold', y=0.99)

# ── Stats banner ────────────────────────────────────────────────
axs = fig.add_axes([0.02, 0.90, 0.96, 0.07])
axs.set_facecolor(PANEL)
axs.axis('off')
stats = [
    ('Total Fills',      '%d'       % sum(fill_counts),       BLU),
    ('Spread P&L',       '+$%.2f'   % sum(spread_vals),       GRN),
    ('Resolution P&L',   '+$%.2f'   % sum(res_vals),          GRN),
    ('Combined P&L',     '+$%.2f'   % (sum(spread_vals)+sum(res_vals)), GOLD),
    ('Avg Markout 30s',  '-0.00144',                          RED),
    ('Adverse Rate 30s', '25.8%',                             ORG),
    ('Resolved Mkts',    '136 / 152',                         TXT),
    ('Mode',             'PAPER',                             ORG),
]
for i, (lbl, val, col) in enumerate(stats):
    x = 0.02 + i * 0.125
    axs.text(x, 0.78, lbl, color='#888', fontsize=8, transform=axs.transAxes)
    axs.text(x, 0.12, val, color=col,  fontsize=11, fontweight='bold', transform=axs.transAxes)

# ── Daily P&L bars ──────────────────────────────────────────────
ax1 = fig.add_axes([0.05, 0.58, 0.55, 0.29])
style(ax1)
ax1.set_title('Daily P&L: Resolution vs Spread Capture', fontsize=11, pad=8)
xi = np.arange(len(dates))
w  = 0.35
ax1.bar(xi - w/2, spread_vals, w, label='Spread P&L',       color=BLU, alpha=0.85)
ax1.bar(xi + w/2, [max(v,0) for v in res_vals], w, label='Resolution (+)', color=GRN, alpha=0.85)
ax1.bar(xi + w/2, [min(v,0) for v in res_vals], w, label='Resolution (-)', color=RED, alpha=0.85)
ax1.set_xticks(xi)
ax1.set_xticklabels(date_labels, color=TXT)
ax1.set_ylabel('USDC', color=TXT)
ax1.axhline(0, color=GRID)
ax1.legend(loc='upper left', fontsize=8, facecolor=PANEL, labelcolor=TXT, framealpha=0.8)
for v, x0 in zip(spread_vals, xi):
    ax1.text(x0-w/2, v+0.4, '%.1f'%v, ha='center', va='bottom', color=BLU, fontsize=7.5)
for v, x0 in zip(res_vals, xi):
    c  = GRN if v>=0 else RED
    yo = 1  if v>=0 else -3
    va = 'bottom' if v>=0 else 'top'
    ax1.text(x0+w/2, v+yo, '%+.1f'%v, ha='center', va=va, color=c, fontsize=7.5)

# ── Cumulative P&L ──────────────────────────────────────────────
ax2 = fig.add_axes([0.65, 0.58, 0.32, 0.29])
style(ax2)
ax2.set_title('Cumulative P&L', fontsize=11, pad=8)
ax2.plot(date_labels, cum_spread, 'o-', color=BLU,  lw=2, label='Spread only', ms=5)
ax2.plot(date_labels, cum_res,    's--',color=GRN,  lw=2, label='Resolution',  ms=5)
ax2.plot(date_labels, cum_comb,   'D-', color=GOLD, lw=2.5,label='Combined',   ms=6)
ax2.fill_between(date_labels, 0, cum_comb, alpha=0.1, color=GOLD)
ax2.set_ylabel('USDC', color=TXT)
ax2.legend(fontsize=8, facecolor=PANEL, labelcolor=TXT, framealpha=0.8)
for i, v in enumerate(cum_comb):
    ax2.annotate('%.0f'%v, (date_labels[i], v),
                 textcoords='offset points', xytext=(0,6),
                 ha='center', fontsize=8, color=GOLD)

# ── Fill count ──────────────────────────────────────────────────
ax3 = fig.add_axes([0.05, 0.21, 0.40, 0.28])
style(ax3)
ax3.set_title('Daily Fill Count', fontsize=11, pad=8)
cfills = [BLU if v < 500 else GOLD for v in fill_counts]
b3 = ax3.bar(date_labels, fill_counts, color=cfills, alpha=0.85)
for bar in b3:
    h = bar.get_height()
    ax3.text(bar.get_x()+bar.get_width()/2, h+5, '%d'%int(h),
             ha='center', va='bottom', color=TXT, fontsize=9)
ax3.set_ylabel('Fills', color=TXT)

# ── Winners / losers waterfall ───────────────────────────────────
ax4 = fig.add_axes([0.52, 0.21, 0.45, 0.28])
style(ax4)
ax4.set_title('Top 8 Winners & Worst 8 Losers (Resolved Markets)', fontsize=11, pad=8)
sorted_res = sorted(resolved, key=lambda r: r['pnl'])
n = 8
items  = sorted_res[:n] + sorted_res[-n:]
qlbls  = [(q['question'][:38]+'..') if len(q['question'])>38 else q['question'] for q in items]
pvals  = [r['pnl'] for r in items]
pcols  = [RED if v < 0 else GRN for v in pvals]
yp     = np.arange(len(items))
ax4.barh(yp, pvals, color=pcols, alpha=0.85, height=0.7)
ax4.set_yticks(yp)
ax4.set_yticklabels(qlbls, fontsize=7, color=TXT)
ax4.axvline(0, color='white', lw=0.8, alpha=0.5)
ax4.axhline(n-0.5, color='#444', lw=1, ls='--')
ax4.set_xlabel('Resolution P&L (USDC)', color=TXT)
xmin = ax4.get_xlim()[0]
ax4.text(xmin+0.5, n-0.35, 'WINNERS v', color=GRN, fontsize=7, alpha=0.8)
ax4.text(xmin+0.5, n-0.75, 'LOSERS ^',  color=RED, fontsize=7, alpha=0.8)
for v, y in zip(pvals, yp):
    ha = 'left' if v>=0 else 'right'
    xo = 0.3 if v>=0 else -0.3
    ax4.text(v+xo, y, '%+.2f'%v, va='center', ha=ha, fontsize=7, color=TXT)

# ── Markout panel ───────────────────────────────────────────────
ax5 = fig.add_axes([0.05, 0.02, 0.90, 0.14])
style(ax5)
ax5.set_title('Markout Quality - Adverse Selection Monitor', fontsize=11, pad=6)
intervals  = [5, 30, 60]
avg_m      = [-0.00088, -0.00144, -0.00273]
adv_rate   = [0.1982,   0.2584,   0.3022]
n_obs      = [2871,     2864,     2859]
x5 = np.arange(len(intervals))

ax5b = ax5.twinx()
ax5b.set_facecolor(BG)
ax5b.tick_params(colors=ORG, labelsize=9)
for sp in ['top']:
    ax5b.spines[sp].set_visible(False)
ax5b.spines['right'].set_color(GRID)

ax5.bar(x5, avg_m, 0.35, color=RED, alpha=0.8, label='Avg Markout')
ax5b.plot(x5, adv_rate, 'D-', color=ORG, lw=2, ms=8, label='Adverse Rate')
ax5.set_xticks(x5)
ax5.set_xticklabels(['T+%ds  (n=%s)'%(i,'{:,}'.format(n)) for i,n in zip(intervals,n_obs)],
                    color=TXT, fontsize=10)
ax5.set_ylabel('Avg Markout', color=TXT)
ax5b.set_ylabel('Adverse Rate', color=ORG)
ax5.axhline(0, color='white', lw=1, ls='--', alpha=0.5)
ax5.set_xlim(-0.5, 2.5)
for xi5, v in zip(x5, avg_m):
    ax5.text(xi5, v-0.00008, '%.5f'%v, ha='center', va='top', color=TXT, fontsize=9)
for xi5, v in zip(x5, adv_rate):
    ax5b.text(xi5+0.2, v+0.004, '%.1f%%'%(v*100), color=ORG, fontsize=9, fontweight='bold')

l1,lb1 = ax5.get_legend_handles_labels()
l2,lb2 = ax5b.get_legend_handles_labels()
ax5.legend(l1+l2, lb1+lb2, loc='lower right', fontsize=8,
           facecolor=PANEL, labelcolor=TXT, framealpha=0.8)

fig.text(0.5, 0.001,
         'Pre-live gate: avg_markout_30s >= 0  [FAIL — expected in paper mode]  |  adverse_rate_30s  [MONITOR: 25.8% at 30s]',
         ha='center', fontsize=9, color=ORG,
         bbox=dict(boxstyle='round', facecolor=PANEL, alpha=0.8, edgecolor='#444'))

plt.savefig('maker_pnl_chart.png', dpi=150, bbox_inches='tight', facecolor=BG)
print('Saved maker_pnl_chart.png')
