'use strict';

// ─────────────────────────────────────────────────────────────
// Formatters
// ─────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

function fmtUptime(s) {
  s = Math.floor(s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}
function fmtPnl(v) {
  if (v == null || isNaN(v)) return '—';
  const sign = v >= 0 ? '+' : '−';
  const abs = Math.abs(v);
  if (abs >= 1000) return `${sign}$${abs.toLocaleString(undefined, {maximumFractionDigits: 0})}`;
  if (abs >= 1)    return `${sign}$${abs.toFixed(2)}`;
  return `${sign}$${abs.toFixed(4)}`;
}
function pnlClass(v) { if (v > 0) return 'pos'; if (v < 0) return 'neg'; return 'muted'; }

function setPnl(id, v) {
  const el = $(id); if (!el) return;
  el.textContent = fmtPnl(v ?? 0);
  el.classList.remove('pos','neg','muted');
  el.classList.add(pnlClass(v ?? 0));
}

function fmtVol(v) {
  if (v == null) return '—';
  if (v >= 1e6) return '$' + (v/1e6).toFixed(1) + 'M';
  if (v >= 1e3) return '$' + (v/1e3).toFixed(1) + 'k';
  return '$' + v.toFixed(0);
}
function fmtDays(d) {
  if (d == null) return '—';
  if (Math.abs(d) < 1/24) return '<span class="urgency-soon">'+Math.max(1, Math.round(d*24*60))+'m</span>';
  if (Math.abs(d) < 1) {
    const h = Math.round(d * 24);
    return `<span class="${h<6?'urgency-soon':'urgency-near'}">${h}h</span>`;
  }
  return d.toFixed(1) + 'd';
}
function fmtTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  if (isNaN(d)) return '—';
  return d.toTimeString().slice(0, 8);
}
function fmtResolves(iso) {
  if (!iso) return '<span class="dim">—</span>';
  const d = new Date(iso);
  if (isNaN(d)) return '<span class="dim">—</span>';
  const diffH = (d - new Date()) / 3600000;
  if (diffH < 0) return '<span class="dim">resolved</span>';
  if (diffH < 1) return `<span class="urgency-soon">${Math.round(diffH*60)}m</span>`;
  if (diffH < 6) return `<span class="urgency-soon">${Math.round(diffH)}h</span>`;
  if (diffH < 48) return `<span class="urgency-near">${Math.round(diffH)}h</span>`;
  const days = Math.round(diffH/24);
  if (days < 30) return `${days}d`;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
function parseAgeSeconds(str) {
  if (!str || str === '?') return Infinity;
  let total = 0;
  const m = str.match(/(\d+)m/); const s = str.match(/(\d+)s/);
  if (m) total += parseInt(m[1]) * 60;
  if (s) total += parseInt(s[1]);
  return total;
}
function feedClass(ageStr) {
  if (!ageStr || ageStr === '?') return 'feed-unknown';
  const s = parseAgeSeconds(ageStr);
  if (s < 10) return 'feed-fresh';
  if (s < 30) return 'feed-warning';
  return 'feed-stale';
}
function esc(str) {
  return String(str ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function shortAddr(a) {
  if (!a) return '—';
  if (a.length <= 12) return a;
  return a.slice(0, 6) + '…' + a.slice(-4);
}
function truncate(s, n) {
  s = s || '';
  return s.length <= n ? s : s.slice(0, n-1) + '…';
}

// ─────────────────────────────────────────────────────────────
// Empty states
// ─────────────────────────────────────────────────────────────
function emptyRow(cols, title, hint) {
  return `<tr><td colspan="${cols}"><div class="empty">
      <div class="empty-icon">∅</div>
      <div class="empty-title">${esc(title)}</div>
      ${hint ? `<div class="empty-hint">${esc(hint)}</div>` : ''}
    </div></td></tr>`;
}

// ─────────────────────────────────────────────────────────────
// Render: KPIs + header
// ─────────────────────────────────────────────────────────────
function renderHeader(d) {
  $('uptime').textContent = fmtUptime(d.uptime_seconds || 0);

  setPnl('sess-mtm',       d.session_mtm_pnl || 0);
  setPnl('sess-realized',  d.session_realized_pnl || 0);
  $('sess-fills').textContent = d.session_fills || 0;

  setPnl('today-cash',     d.today_cash_pnl || 0);
  setPnl('today-realized', d.today_realized_pnl || 0);
  $('today-fills').textContent = d.today_fills || 0;

  setPnl('alltime-cash',     d.alltime_cash_pnl || 0);
  setPnl('alltime-realized', d.alltime_realized_pnl || 0);
  $('alltime-fills').textContent = d.alltime_fills || 0;

  $('total-inv').textContent = (d.total_abs_inventory || 0).toFixed(2);
  $('current-investment').textContent = '$' + (d.current_investment || 0).toFixed(2);
  $('n-markets').textContent = d.n_active_markets || 0;
  $('cb-fires').textContent = d.cb_fires || 0;

  const badge = $('mode-badge');
  if (d.paper) { badge.textContent = 'Paper'; badge.className = 'mode-badge mode-paper'; }
  else         { badge.textContent = 'Live';  badge.className = 'mode-badge mode-live';  }
}

// ─────────────────────────────────────────────────────────────
// Quote row renderer (shared between Overview + Quotes tab)
// ─────────────────────────────────────────────────────────────
function statusPill(m) {
  if (m.in_cooldown) return `<span class="pill pill-cooldown pill-dot">Cooldown</span>`;
  return `<span class="pill pill-quoting pill-dot">Quoting</span>`;
}

function inventoryBar(m) {
  const pct = Math.max(-1, Math.min(1, m.inventory_pct || 0));
  const width = Math.abs(pct) * 50; // half-bar
  const isPos = pct >= 0;
  const shares = (m.inventory || m.inventory_shares || 0);
  const style = isPos
    ? `left:50%; width:${width}%; background:var(--pos);`
    : `right:50%; width:${width}%; background:var(--neg);`;
  return `<div class="inv-cell">
    <div class="inv-track"><div class="inv-fill" style="${style}"></div></div>
    <span class="inv-val">${(shares>=0?'+':'')}${Number(shares).toFixed(1)}</span>
  </div>`;
}

function bookCell(m) {
  const bb = (m.book_bid ?? 0).toFixed(3);
  const ba = (m.book_ask ?? 0).toFixed(3);
  return `<span class="book"><span class="bid">${bb}</span><span class="slash">/</span><span class="ask">${ba}</span></span>`;
}
function quoteCell(m) {
  const b = (m.bid ?? 0).toFixed(3);
  const a = (m.ask ?? 0).toFixed(3);
  return `<span class="book"><span class="bid">${b}</span><span class="slash">/</span><span class="ask">${a}</span></span>`;
}

// ─────────────────────────────────────────────────────────────
// Main render
// ─────────────────────────────────────────────────────────────
function render(d) {
  renderHeader(d);

  const markets = d.active_markets || [];
  const fills   = (d.fills || []).slice(0, 20);
  const perf    = d.by_market || [];
  const selected = d.selected_markets || [];

  // Tab badges
  $('tab-quotes-badge').textContent  = markets.length;
  $('tab-fills-badge').textContent   = fills.length;
  $('tab-markets-badge').textContent = selected.length;
  $('tab-arb-badge').textContent     = (d.arb_current || []).length || (d.arb_total_events || 0);

  // ── Active Quotes (full tab) ─────────────────────────────
  $('quotes-count').textContent = markets.length;
  const qBody = $('quotes-body');
  if (!markets.length) {
    qBody.innerHTML = emptyRow(9, 'No active quotes', 'The bot is not currently posting quotes.');
  } else {
    qBody.innerHTML = markets.map(m => {
      const spread = ((m.ask||0) - (m.bid||0)).toFixed(3);
      const depth  = ((m.bid_depth||0) + (m.ask_depth||0)).toFixed(0);
      const cat    = m.category ? `<div class="qsub">${esc(m.category)}</div>` : '';
      return `<tr>
        <td><div class="qtext" title="${esc(m.question||'')}">${esc(truncate(m.question||'—', 60))}</div>${cat}</td>
        <td>${fmtDays(m.days_left)}</td>
        <td class="num right">${fmtVol(m.volume_24h)}</td>
        <td>${bookCell(m)}</td>
        <td class="num right">$${depth}</td>
        <td>${quoteCell(m)}</td>
        <td class="right"><span class="spread">${spread}</span></td>
        <td>${inventoryBar(m)}</td>
        <td>${statusPill(m)}</td>
      </tr>`;
    }).join('');
  }

  // Overview: compact active quotes (4 cols)
  $('ov-quotes-count').textContent = markets.length;
  $('ov-quotes-meta').textContent = markets.length ? `${markets.length} markets · ${(d.cb_fires||0)} CB fires` : 'idle';
  const ovQ = $('ov-quotes-body');
  if (!markets.length) {
    ovQ.innerHTML = emptyRow(4, 'No active quotes', 'Waiting for the bot to begin quoting.');
  } else {
    ovQ.innerHTML = markets.slice(0,8).map(m => `<tr>
      <td><div class="qtext" title="${esc(m.question||'')}">${esc(truncate(m.question||'—', 50))}</div></td>
      <td>${quoteCell(m)}</td>
      <td>${inventoryBar(m)}</td>
      <td>${statusPill(m)}</td>
    </tr>`).join('');
  }

  // ── Fills ────────────────────────────────────────────────
  $('fills-count').textContent = fills.length;
  const fBody = $('fills-body');
  if (!fills.length) {
    fBody.innerHTML = emptyRow(7, 'No fills yet', 'Fills will appear as the bot trades.');
  } else {
    fBody.innerHTML = fills.map(f => {
      const side = (f.side || '').toUpperCase();
      const sideTag = side === 'BUY'
        ? '<span class="pill pill-buy">Buy</span>'
        : '<span class="pill pill-sell">Sell</span>';
      const notional = ((f.price||0) * (f.size||0));
      return `<tr>
        <td class="num">${fmtTime(f.filled_at || f.timestamp || f.ts)}</td>
        <td><div class="qtext" title="${esc(f.question||'')}">${esc(truncate(f.question || f.token_id || '—', 48))}</div></td>
        <td>${fmtResolves(f.end_date_iso)}</td>
        <td>${sideTag}</td>
        <td class="num right">${(f.price||0).toFixed(4)}</td>
        <td class="num right">${(f.size||0).toFixed(2)}</td>
        <td class="num right">$${notional.toFixed(2)}</td>
      </tr>`;
    }).join('');
  }
  // Overview fills
  $('ov-fills-count').textContent = fills.length;
  const ovF = $('ov-fills-body');
  if (!fills.length) {
    ovF.innerHTML = emptyRow(4, 'No fills yet');
  } else {
    ovF.innerHTML = fills.slice(0,10).map(f => {
      const side = (f.side || '').toUpperCase();
      const sideTag = side === 'BUY' ? '<span class="pill pill-buy">Buy</span>' : '<span class="pill pill-sell">Sell</span>';
      return `<tr>
        <td class="num">${fmtTime(f.filled_at || f.timestamp || f.ts)}</td>
        <td>${sideTag}</td>
        <td class="num right">${(f.price||0).toFixed(4)}</td>
        <td class="num right">${(f.size||0).toFixed(2)}</td>
      </tr>`;
    }).join('');
  }

  // ── Per-market perf (shared body) ────────────────────────
  const renderPerf = (body, countEl) => {
    countEl.textContent = perf.length;
    if (!perf.length) {
      body.innerHTML = emptyRow(4, 'No performance data yet');
      return;
    }
    body.innerHTML = perf.map(m => {
      const cash = m.alltime_cash_pnl || 0;
      const realized = m.alltime_realized_pnl || 0;
      const q = m.question ? truncate(m.question, 60) : shortAddr(m.token_id);
      return `<tr>
        <td><div class="qtext" title="${esc(m.question || m.token_id || '')}">${esc(q)}</div></td>
        <td class="num right">${m.alltime_fills || 0}</td>
        <td class="num right ${pnlClass(cash)}">${fmtPnl(cash)}</td>
        <td class="num right ${pnlClass(realized)}">${fmtPnl(realized)}</td>
      </tr>`;
    }).join('');
  };
  renderPerf($('market-perf-body'), $('market-perf-count'));
  renderPerf($('ov-perf-body'), $('ov-perf-count'));

  // ── Selected Markets ─────────────────────────────────────
  $('sel-count').textContent = selected.length;
  const selBody = $('sel-body');
  if (!selected.length) {
    selBody.innerHTML = emptyRow(7, 'No markets selected', 'Your watchlist is empty.');
  } else {
    selBody.innerHTML = selected.map(m => {
      const pill = m.is_quoting
        ? '<span class="pill pill-quoting pill-dot">Quoting</span>'
        : '<span class="pill pill-watching pill-dot">Watching</span>';
      return `<tr>
        <td>${pill}</td>
        <td><div class="qtext" title="${esc(m.question||'')}">${esc(truncate(m.question||'—', 70))}</div></td>
        <td>${m.category ? `<span class="pill pill-cat">${esc(m.category)}</span>` : '<span class="dim">—</span>'}</td>
        <td>${bookCell(m)}</td>
        <td class="num right">${(m.spread||0).toFixed(3)}</td>
        <td class="num right">${fmtVol(m.volume_24h)}</td>
        <td>${fmtDays(m.days_left)}</td>
      </tr>`;
    }).join('');
  }

  // ── Arb Scanner ──────────────────────────────────────────
  const arbCurrent = d.arb_current || [];
  const arbEvents = d.arb_total_events || 0;
  $('arb-total').textContent = arbEvents + ' events';
  $('arb-live-note').textContent = arbCurrent.length
      ? `${arbCurrent.length} live opportunity${arbCurrent.length>1?'ies':''}`
      : (arbEvents > 0 ? `${arbEvents} seen this session · no live` : 'requires YES + NO tokens per market');
  const arbBody = $('arb-body');
  if (!arbCurrent.length) {
    arbBody.innerHTML = emptyRow(5, 'No live opportunities',
      arbEvents > 0 ? `${arbEvents} opportunities fired this session` : 'Scanner requires YES + NO tokens tracked.');
  } else {
    arbBody.innerHTML = arbCurrent.map(o => `<tr class="arb-row">
      <td><div class="qtext" title="${esc(o.question||'')}">${esc(truncate(o.question||'—', 60))}</div></td>
      <td class="num right">${(o.ask_a||0).toFixed(4)}</td>
      <td class="num right">${(o.ask_b||0).toFixed(4)}</td>
      <td class="num right warn">${(o.ask_sum||0).toFixed(4)}</td>
      <td class="num right"><span class="gap-pos">+${((o.gap||0)*100).toFixed(2)}%</span></td>
    </tr>`).join('');
  }

  // ── Active Quotes P&L (currently being quoted) ──────────
  const positions = d.live_positions || [];
  $('tab-positions-badge').textContent = positions.length;
  $('pos-count').textContent = positions.length;
  const posBody = $('positions-body');
  if (!positions.length) {
    posBody.innerHTML = emptyRow(10, 'No markets being quoted', 'Markets with active quotes will appear here.');
  } else {
    posBody.innerHTML = positions.map(p => {
      const inv = p.inventory || 0;
      const invSign = inv >= 0 ? '+' : '';
      const invCls = inv >= 0 ? 'pos' : 'neg';
      const bid = (p.bid || 0).toFixed(3);
      const ask = (p.ask || 0).toFixed(3);
      return `<tr>
        <td><div class="qtext" title="${esc(p.question)}">${esc(truncate(p.question, 60))}</div></td>
        <td>${p.category ? `<span class="pill pill-cat">${esc(p.category)}</span>` : '<span class="dim">—</span>'}</td>
        <td>${fmtResolves(p.end_date_iso)}</td>
        <td><span class="book"><span class="bid">${bid}</span><span class="slash">/</span><span class="ask">${ask}</span></span></td>
        <td class="num right ${invCls}">${invSign}${inv.toFixed(2)}</td>
        <td class="num right">${(p.current_mid || 0).toFixed(4)}</td>
        <td class="num right ${pnlClass(p.position_value)}">${fmtPnl(p.position_value)}</td>
        <td class="num right ${pnlClass(p.cash_pnl)}">${fmtPnl(p.cash_pnl)}</td>
        <td class="num right ${pnlClass(p.mtm_pnl)}">${fmtPnl(p.mtm_pnl)}</td>
        <td class="num right ${pnlClass(p.realized_pnl)}">${fmtPnl(p.realized_pnl)}</td>
      </tr>`;
    }).join('');
  }

  // ── Session P&L by Market ────────────────────────────────
  const sessionPnl = d.session_pnl || [];
  $('tab-session-pnl-badge').textContent = sessionPnl.length;
  $('session-pnl-count').textContent = sessionPnl.length;
  const sessionPnlBody = $('session-pnl-body');
  if (!sessionPnl.length) {
    sessionPnlBody.innerHTML = emptyRow(9, 'No fills this session yet', 'Markets with fills this session will appear here.');
  } else {
    sessionPnlBody.innerHTML = sessionPnl.map(p => {
      const inv = p.inventory || 0;
      const invSign = inv >= 0 ? '+' : '';
      const invCls = inv >= 0 ? 'pos' : 'neg';
      const quotingDot = p.is_quoting ? '<span style="color:#4fc3f7" title="Currently quoting">●</span> ' : '';
      return `<tr>
        <td><div class="qtext" title="${esc(p.question)}">${quotingDot}${esc(truncate(p.question, 60))}</div></td>
        <td>${p.category ? `<span class="pill pill-cat">${esc(p.category)}</span>` : '<span class="dim">—</span>'}</td>
        <td>${fmtResolves(p.end_date_iso)}</td>
        <td class="num right ${invCls}">${invSign}${inv.toFixed(2)}</td>
        <td class="num right">${(p.current_mid || 0).toFixed(4)}</td>
        <td class="num right ${pnlClass(p.position_value)}">${fmtPnl(p.position_value)}</td>
        <td class="num right ${pnlClass(p.cash_pnl)}">${fmtPnl(p.cash_pnl)}</td>
        <td class="num right ${pnlClass(p.mtm_pnl)}">${fmtPnl(p.mtm_pnl)}</td>
        <td class="num right ${pnlClass(p.realized_pnl)}">${fmtPnl(p.realized_pnl)}</td>
      </tr>`;
    }).join('');
  }

  // ── Resolved Markets ─────────────────────────────────────
  const resolved = d.resolved_markets || [];
  $('tab-resolved-badge').textContent = resolved.length;
  $('resolved-count').textContent = resolved.length;
  const resolvedBody = $('resolved-body');
  if (!resolved.length) {
    resolvedBody.innerHTML = emptyRow(6, 'No resolved markets yet', 'Markets will appear here after they resolve.');
  } else {
    resolvedBody.innerHTML = resolved.map(r => {
      const dl = r.days_left;
      const resolvedTag = dl == null
        ? '<span class="dim">—</span>'
        : `<span class="dim">${Math.abs(dl) < 1 ? Math.round(Math.abs(dl)*24)+'h ago' : Math.round(Math.abs(dl))+'d ago'}</span>`;
      return `<tr>
        <td><div class="qtext" title="${esc(r.question)}">${esc(truncate(r.question, 65))}</div></td>
        <td>${r.category ? `<span class="pill pill-cat">${esc(r.category)}</span>` : '<span class="dim">—</span>'}</td>
        <td>${resolvedTag}</td>
        <td class="num right">${r.fills || 0}</td>
        <td class="num right ${pnlClass(r.cash_pnl)}">${fmtPnl(r.cash_pnl)}</td>
        <td class="num right ${pnlClass(r.realized_pnl)}">${fmtPnl(r.realized_pnl)}</td>
      </tr>`;
    }).join('');
  }

  // ── Falcon ───────────────────────────────────────────────
  renderFalcon(d.falcon_data || {});

  // ── Feeds ────────────────────────────────────────────────
  const clobAge = d.clob_age || '?';
  const microAge = d.micro_age || '?';
  const clobEl = $('clob-age');  clobEl.textContent = clobAge;  clobEl.className = 'feed-age ' + feedClass(clobAge);
  const microEl = $('micro-age'); microEl.textContent = microAge; microEl.className = 'feed-age ' + feedClass(microAge);
}

function renderFalcon(f) {
  const multiplier = f.spread_multiplier || 1.0;
  const badge = $('falcon-spread-badge');
  if (multiplier > 1.01) {
    badge.textContent = `${multiplier.toFixed(2)}× spread`;
    badge.className = 'spread-mult widened';
    badge.title = `Whale activity — spread widened ${((multiplier-1)*100).toFixed(0)}%`;
  } else {
    badge.textContent = '1.00× spread';
    badge.className = 'spread-mult normal';
    badge.title = 'Standard spread';
  }

  const wallets = f.wallets || [];
  $('falcon-whale-count').textContent = wallets.length;
  $('falcon-whale-age').textContent = wallets.length ? `updated ${f.wallet_feed_age || '?'} ago` : '';
  const whaleBody = $('falcon-whale-body');
  if (!wallets.length) {
    whaleBody.innerHTML = emptyRow(6, 'Leaderboard not loaded', 'Awaiting FALCON_API_KEY.');
  } else {
    whaleBody.innerHTML = wallets.map(w => {
      const trustColor = w.elite ? 'pos' : (w.trust_score >= 1.0 ? '' : 'muted');
      const trendCls = w.trend === 'improving' ? 'trend-improving'
                     : w.trend === 'declining' ? 'trend-declining' : 'muted';
      const rankCls = w.elite ? 'rank-chip elite' : 'rank-chip';
      const rank = w.rank ? `#${w.rank}` : '—';
      const staleDot = `<span class="stale-dot ${w.fresh ? 'fresh' : 'stale'}"></span>`;
      const pnl = Number(w.pnl||0);
      const pnlStr = (pnl >= 0 ? '+' : '−') + '$' + Math.abs(pnl).toLocaleString();
      return `<tr>
        <td>${staleDot}<span class="${rankCls}">${rank}</span><span class="wallet-addr" title="${esc(w.address)}">${shortAddr(w.address)}</span></td>
        <td class="num right ${pnl>=0?'pos':'neg'}">${pnlStr}</td>
        <td class="num right">${(w.win_rate||0).toFixed(1)}%</td>
        <td class="num right">${(w.h_score||0).toFixed(1)}</td>
        <td class="num right ${trustColor}">${(w.trust_score||0).toFixed(3)}${w.elite ? ' ▲' : ''}${w.sybil ? ' ⚠' : ''}</td>
        <td class="${trendCls}">${w.trend || '—'}</td>
      </tr>`;
    }).join('');
  }

  const markets = f.markets || [];
  const nWhale = f.n_whale_markets || 0;
  const nSpiking = f.n_spiking || 0;
  $('falcon-markets-count').textContent = markets.length;
  const badgesEl = $('falcon-markets-badges');
  let badgesHtml = '';
  if (nWhale > 0)   badgesHtml += `<span class="spread-mult widened" title="Whale-controlled markets">${nWhale} whale</span>`;
  if (nSpiking > 0) badgesHtml += `<span class="spread-mult" style="background:rgba(61,214,140,0.1);color:var(--pos);border:1px solid rgba(61,214,140,0.3)" title="Volume spiking">${nSpiking} spiking</span>`;
  badgesEl.innerHTML = badgesHtml;
  $('falcon-insights-age').textContent = markets.length ? `updated ${f.insights_feed_age || '?'} ago` : '';

  const mBody = $('falcon-markets-body');
  if (!markets.length) {
    mBody.innerHTML = emptyRow(6, 'No market insights', 'Awaiting FALCON_API_KEY.');
  } else {
    mBody.innerHTML = markets.map(m => {
      const trendCls = m.volume_trend === 'Spiking' ? 'trend-spiking'
                     : m.volume_trend === 'Dying Interest' ? 'trend-dying'
                     : m.volume_trend === 'Declining' ? 'warn' : 'muted';
      const spreadCls = (m.spread_mult||1) > 1.01 ? 'widened' : 'normal';
      const quotingTag = m.in_active ? ' <span class="pill pill-quoting" style="font-size:9.5px;padding:1px 5px">Quoting</span>' : '';
      const whaleMark = m.whale_flag ? ' <span title="whale-controlled">🐋</span>' : '';
      return `<tr>
        <td><div class="qtext" title="${esc(m.question||'')}">${esc(truncate(m.question||m.condition_id||'—', 45))}${whaleMark}${quotingTag}</div></td>
        <td class="num right">$${Number(m.volume_24h||0).toLocaleString()}</td>
        <td class="${trendCls}">${m.volume_trend || '—'}</td>
        <td class="num right">${(m.top1_pct||0).toFixed(1)}%</td>
        <td class="num right">${m.unique_traders || 0}</td>
        <td><span class="spread-mult ${spreadCls}">${(m.spread_mult||1).toFixed(2)}×</span></td>
      </tr>`;
    }).join('');
  }
}

// ─────────────────────────────────────────────────────────────
// Tabs
// ─────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const key = btn.dataset.tab;
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.id === `panel-${key}`));
    try { localStorage.setItem('maker-tab', key); } catch(_) {}
  });
});
try {
  const saved = localStorage.getItem('maker-tab');
  if (saved) {
    const btn = document.querySelector(`.tab[data-tab="${saved}"]`);
    if (btn) btn.click();
  }
} catch(_) {}

// ─────────────────────────────────────────────────────────────
// Connection status
// ─────────────────────────────────────────────────────────────
function setConnStatus(state) {
  const dot = $('conn-dot');
  const label = $('conn-label');
  dot.className = 'conn-dot ' + state;
  const labels = { connected: 'Live', connecting: 'Connecting…', disconnected: 'Reconnecting…', demo: 'Demo mode' };
  label.textContent = labels[state] || state;
}

// ─────────────────────────────────────────────────────────────
// SSE w/ demo fallback
// ─────────────────────────────────────────────────────────────
let es = null;
let retryTimer = null;
let demoTimer = null;

function clearDemo() {
  if (demoTimer) { clearInterval(demoTimer); demoTimer = null; }
  $('demo-ribbon').style.display = 'none';
}
function startDemo() {
  $('demo-ribbon').style.display = '';
  setConnStatus('demo');
  let t = 0;
  const tick = () => {
    t++;
    render(demoData(t));
  };
  tick();
  demoTimer = setInterval(tick, 1500);
}

function connect() {
  if (es) { es.close(); es = null; }
  setConnStatus('connecting');

  // If EventSource isn't available or endpoint clearly missing (file:// previews), go demo.
  if (typeof EventSource === 'undefined' || location.protocol === 'file:') {
    startDemo();
    return;
  }

  try {
    es = new EventSource('/maker-stream');
  } catch (e) {
    startDemo(); return;
  }

  let gotMessage = false;

  es.onopen = () => { /* still wait for first message */ };
  es.onmessage = (ev) => {
    gotMessage = true;
    clearDemo();
    setConnStatus('connected');
    try {
      const data = JSON.parse(ev.data);
      render(data);
    } catch (e) { console.warn('parse error', e); }
  };
  es.onerror = () => {
    if (es) { es.close(); es = null; }
    if (!gotMessage) {
      // backend not available — show demo data so the dashboard is useful to look at
      startDemo();
    } else {
      setConnStatus('disconnected');
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = setTimeout(connect, 3000);
    }
  };

  // If no message arrives within 3s, assume no backend and show demo
  setTimeout(() => {
    if (!gotMessage) startDemo();
  }, 3000);
}

// ─────────────────────────────────────────────────────────────
// Demo data generator (illustrative only; shape matches real feed)
// ─────────────────────────────────────────────────────────────
const DEMO_MARKETS = [
  { q: 'Will BTC close above $95,000 on April 25?', cat: 'Crypto', token: '0x7a2f...c011' },
  { q: 'Will the Fed cut rates at the May FOMC meeting?', cat: 'Macro', token: '0x3b81...99af' },
  { q: 'Will Lakers win NBA Finals 2026?', cat: 'Sports', token: '0x1e4d...4c20' },
  { q: 'Will ETH hit $5,000 before June 1?', cat: 'Crypto', token: '0x8c00...0d7e' },
  { q: 'Will Nvidia stock close above $1,200 this week?', cat: 'Equities', token: '0x44af...fe1b' },
  { q: 'Will SpaceX attempt Starship IFT-12 in April?', cat: 'Science', token: '0x9912...aa07' },
  { q: 'Will US Jobs Report beat 200k consensus?', cat: 'Macro', token: '0x2233...8c1d' },
];
const DEMO_WALLETS = [
  { rank: 1, address: '0x8c34d1f290f11a8fecad19b6ab0b0a12', pnl: 1840250, win_rate: 68.4, h_score: 92.1, trust_score: 2.84, elite: true, fresh: true, trend: 'improving' },
  { rank: 2, address: '0x3f12ab99c0fe12340dd8a19b0a12abcd', pnl: 922140, win_rate: 61.2, h_score: 84.6, trust_score: 1.92, elite: true, fresh: true, trend: 'stable' },
  { rank: 3, address: '0x192abcde2377ff2112ef8812aa992144', pnl: 481230, win_rate: 57.8, h_score: 71.2, trust_score: 1.44, fresh: true, trend: 'improving' },
  { rank: 4, address: '0x7fcafe120980ccff1023ab2200aabb11', pnl: 312411, win_rate: 54.1, h_score: 62.4, trust_score: 1.08, fresh: false, trend: 'declining' },
  { rank: 5, address: '0xdeadbeef12340987aabbcc11223344ff', pnl: -84120, win_rate: 46.2, h_score: 41.3, trust_score: 0.72, fresh: true, trend: 'declining', sybil: true },
];

function demoData(t) {
  const makeMarket = (m, i) => {
    const bb = 0.42 + Math.sin((t + i) * 0.3) * 0.06 + i * 0.02;
    const ba = bb + 0.018 + Math.random() * 0.004;
    const our_b = bb - 0.008;
    const our_a = ba + 0.008;
    const inv = Math.sin((t + i*2)*0.15) * (30 + i * 10);
    return {
      question: m.q, category: m.cat, token_id: m.token,
      days_left: [0.08, 0.6, 2.3, 8.4, 1.2, 0.04, 3.1][i] || 1,
      volume_24h: 50000 + (i+1)*34000 + Math.random()*9000,
      book_bid: bb, book_ask: ba, bid: our_b, ask: our_a,
      bid_depth: 120 + i*50, ask_depth: 90 + i*40,
      inventory: inv, inventory_pct: inv / 100,
      in_cooldown: i === 2,
    };
  };
  const active = DEMO_MARKETS.slice(0, 5).map(makeMarket);
  const fills = [];
  for (let i = 0; i < 14; i++) {
    const m = DEMO_MARKETS[(i + t) % DEMO_MARKETS.length];
    fills.push({
      filled_at: Math.floor(Date.now()/1000) - i * (60 + Math.random()*120),
      question: m.q, token_id: m.token,
      side: i % 2 === 0 ? 'buy' : 'sell',
      price: 0.4 + Math.random() * 0.2,
      size: 20 + Math.random() * 80,
      end_date_iso: new Date(Date.now() + [3,26,50,200,80,2,500][(i)%7] * 3600000).toISOString(),
    });
  }
  const perf = DEMO_MARKETS.map((m, i) => ({
    question: m.q, token_id: m.token,
    alltime_fills: 12 + i*7,
    alltime_cash_pnl: (i === 4 ? -12.34 : 4 + i * 3.2 + Math.sin(t*0.1+i)),
    alltime_realized_pnl: (i === 4 ? -8.12 : 2.1 + i * 1.8),
  }));
  const selected = DEMO_MARKETS.map((m, i) => ({
    question: m.q, category: m.cat,
    book_bid: 0.42 + i*0.03, book_ask: 0.44 + i*0.03,
    spread: 0.022 + i*0.001, volume_24h: 34000 * (i+1),
    days_left: [0.08, 0.6, 2.3, 8.4, 1.2, 0.04, 3.1][i],
    is_quoting: i < 5,
  }));
  const arb_current = (t % 20 < 3) ? [{
    question: DEMO_MARKETS[1].q,
    ask_a: 0.512, ask_b: 0.478, ask_sum: 0.990, gap: 0.010,
  }] : [];

  const live_positions = DEMO_MARKETS.slice(0, 5).map((m, i) => {
    const inv = Math.sin((t + i*2)*0.15) * (30 + i * 10);
    const mid = 0.42 + Math.sin((t + i) * 0.3) * 0.06 + i * 0.02;
    const cash = (i === 3 ? -12.34 : 3.2 + i * 2.1 + Math.sin(t*0.1+i));
    const posVal = inv * mid;
    return {
      question: m.q, category: m.cat, token_id: m.token,
      end_date_iso: new Date(Date.now() + [3,26,50,200,80][i] * 3600000).toISOString(),
      days_left: [0.08, 0.6, 2.3, 8.4, 1.2][i],
      inventory: parseFloat(inv.toFixed(2)),
      current_mid: parseFloat(mid.toFixed(4)),
      position_value: parseFloat(posVal.toFixed(4)),
      cash_pnl: parseFloat(cash.toFixed(4)),
      mtm_pnl: parseFloat((cash + posVal).toFixed(4)),
      realized_pnl: parseFloat((i === 3 ? -4.12 : 1.5 + i * 0.9).toFixed(4)),
    };
  });

  return {
    paper: true,
    uptime_seconds: 3600 * 2 + 840 + t,
    session_mtm_pnl: 8.42 + Math.sin(t*0.05)*2.3,
    session_realized_pnl: 4.12,
    session_fills: 18,
    today_cash_pnl: 21.84,
    today_realized_pnl: 14.32,
    today_fills: 47,
    alltime_cash_pnl: 1284.21,
    alltime_realized_pnl: 912.08,
    alltime_fills: 2841,
    total_abs_inventory: 182.44,
    current_investment: 47.23,
    n_active_markets: active.length,
    cb_fires: 3,
    active_markets: active,
    fills,
    by_market: perf,
    selected_markets: selected,
    arb_current,
    arb_total_events: 12,
    live_positions,
    session_pnl: DEMO_MARKETS.slice(0, 6).map((m, i) => {
      const inv = Math.sin((t + i*2)*0.15) * (20 + i * 8);
      const mid = 0.42 + Math.sin((t + i) * 0.3) * 0.06 + i * 0.02;
      const cash = (Math.random() - 0.5) * 4;
      const realized = (Math.random() - 0.4) * 2;
      return {
        token_id: m.token, question: m.q, category: m.cat,
        end_date_iso: new Date(Date.now() + [1,3,5,2,7,4][i] * 86400000).toISOString(),
        days_left: [1,3,5,2,7,4][i],
        inventory: parseFloat(inv.toFixed(2)),
        current_mid: parseFloat(mid.toFixed(4)),
        position_value: parseFloat((inv * mid).toFixed(4)),
        cash_pnl: parseFloat(cash.toFixed(4)),
        realized_pnl: parseFloat(realized.toFixed(4)),
        mtm_pnl: parseFloat((cash + inv * mid).toFixed(4)),
        is_quoting: i < 4,
      };
    }),
    resolved_markets: DEMO_MARKETS.slice(0, 4).map((m, i) => ({
      question: m.q, category: m.cat, token_id: m.token,
      end_date_iso: new Date(Date.now() - [3,8,26,50][i] * 3600000).toISOString(),
      days_left: -[0.1, 0.3, 1.1, 2.1][i],
      fills: [18, 31, 12, 44][i],
      cash_pnl: [4.21, -2.14, 8.77, 1.33][i],
      realized_pnl: [2.10, -1.05, 4.32, 0.88][i],
    })),
    clob_age: t % 30 < 20 ? `${(t % 8) + 1}s` : '42s',
    micro_age: t % 40 < 30 ? `${(t % 5) + 1}s` : '1m12s',
    falcon_data: {
      spread_multiplier: (t % 25 < 10) ? 1.5 : 1.0,
      wallets: DEMO_WALLETS,
      wallet_feed_age: '3s',
      insights_feed_age: '8s',
      n_whale_markets: 2,
      n_spiking: 3,
      markets: DEMO_MARKETS.slice(0, 6).map((m, i) => ({
        question: m.q, condition_id: m.token,
        volume_24h: 48000 + i * 21000,
        volume_trend: ['Spiking','Stable','Declining','Stable','Spiking','Dying Interest'][i],
        top1_pct: 18 + i*8,
        unique_traders: 240 + i*120,
        spread_mult: i === 0 ? 1.5 : (i === 4 ? 1.25 : 1.0),
        whale_flag: i === 0 || i === 4,
        in_active: i < 5,
      })),
    },
  };
}

connect();
