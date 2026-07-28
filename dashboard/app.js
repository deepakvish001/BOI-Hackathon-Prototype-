/* BODHI MULE HUNTER AI - investigator console.
 *
 * Deliberately dependency-free: no CDN, no bundler, no framework. A bank
 * deploys this inside a network that cannot reach unpkg, and a hackathon judge
 * should be able to open it with the machine offline. The force-directed graph
 * is ~60 lines of Verlet integration rather than a 250 KB library.
 */

'use strict';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const fmtINR = (n) =>
  n == null ? '-' : '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
const fmtNum = (n) => (n == null ? '-' : Number(n).toLocaleString('en-IN'));
const esc = (s) =>
  String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* non-JSON body */ }
    throw new Error(detail);
  }
  return res.json();
}

const state = { alerts: [], selected: null, detail: null, liveLog: [] };

/* ------------------------------------------------------------------ tabs */

$$('nav.tabs button').forEach((btn) => {
  btn.addEventListener('click', () => {
    $$('nav.tabs button').forEach((b) => b.classList.remove('active'));
    $$('.view').forEach((v) => v.classList.remove('active'));
    btn.classList.add('active');
    $('#view-' + btn.dataset.view).classList.add('active');
    const loader = VIEW_LOADERS[btn.dataset.view];
    if (loader) loader();
  });
});

/* --------------------------------------------------------------- overview */

async function loadOverview() {
  const o = await api('/api/overview');
  const bands = o.bands || {};
  const kpis = [
    ['Accounts', fmtNum(o.accounts)],
    ['Transactions', fmtNum(o.transactions)],
    ['Open alerts', fmtNum(o.alerts_open)],
    ['Critical', fmtNum(bands.CRITICAL || 0)],
    ['Rings', fmtNum(o.rings)],
    ['At risk', fmtINR(o.amount_at_risk)],
    ['Restrictions', fmtNum(o.active_restrictions)],
    ['NCRP tickets', fmtNum(o.ncrp_tickets)],
  ];
  $('#kpis').innerHTML = kpis
    .map(([l, v]) => `<div class="kpi"><div class="label">${l}</div><div class="value">${v}</div></div>`)
    .join('');
}

/* ------------------------------------------------------------ alert queue */

async function loadQueue() {
  const data = await api('/api/alerts?limit=120');
  state.alerts = data.alerts;
  $('#queue-count').textContent = data.count;
  if (!data.alerts.length) {
    $('#queue').innerHTML = '<div class="loading">No alerts above threshold.</div>';
    return;
  }
  $('#queue').innerHTML = data.alerts
    .map(
      (a) => `
    <div class="alert-row" data-id="${esc(a.alert_id)}">
      <div class="score-chip band-${esc(a.band)}">${Math.round(a.risk_score)}</div>
      <div>
        <div class="alert-title">${esc(a.title)}</div>
        <div class="alert-meta">${esc(a.account_id)} &middot; ${fmtINR(a.amount_at_risk)} at risk</div>
        <div class="alert-tags">
          ${a.evidence.slice(0, 3).map((e) => `<span class="tag">${esc(e.code)}</span>`).join('')}
          ${a.linked_tickets.length ? `<span class="tag">${a.linked_tickets.length} NCRP</span>` : ''}
        </div>
      </div>
    </div>`
    )
    .join('');

  $$('#queue .alert-row').forEach((row) =>
    row.addEventListener('click', () => selectAlert(row.dataset.id))
  );
  if (!state.selected) selectAlert(data.alerts[0].alert_id);
}

async function selectAlert(alertId) {
  state.selected = alertId;
  $$('#queue .alert-row').forEach((r) =>
    r.classList.toggle('selected', r.dataset.id === alertId)
  );
  $('#detail').innerHTML = '<div class="loading"><div class="spin"></div>Assembling case…</div>';
  try {
    const data = await api('/api/alerts/' + encodeURIComponent(alertId));
    state.detail = data;
    renderDetail(data);
  } catch (err) {
    $('#detail').innerHTML = `<div class="loading">Could not load case: ${esc(err.message)}</div>`;
  }
}

/* ---------------------------------------------------------- case detail */

function renderDetail(data) {
  const a = data.alert;
  const d = data.detail;
  const ls = d.layer_scores;

  const layer = (name, key, note) => `
    <div class="layer">
      <div class="lname">${name}</div>
      <div class="lval">${(ls[key] * 100).toFixed(0)}</div>
      <div class="bar"><i style="width:${Math.min(100, ls[key] * 100).toFixed(0)}%"></i></div>
      <div class="faint" style="font-size:10px;margin-top:4px">${note}</div>
    </div>`;

  $('#detail').innerHTML = `
    <div class="detail-head">
      <div class="detail-score band-${esc(a.band)}">
        <div class="n">${Math.round(a.risk_score)}</div>
        <div class="b">${esc(a.band)}</div>
      </div>
      <div style="flex:1">
        <h3>${esc(a.account_id)}</h3>
        <div class="muted" style="font-size:12px">${esc(a.title)}</div>
        <p class="narrative">${esc(a.narrative)}</p>
      </div>
    </div>

    <div class="layer-bars">
      ${layer('Layer 4 · XGBoost', 'xgb', 'tabular behaviour')}
      ${layer('Layer 5 · GraphSAGE', 'graph', 'network structure')}
      ${layer('Layer 6 · TGN', 'temporal', 'event ordering')}
      ${layer('Intelligence', 'intel', 'NCRP · APK · RBI')}
    </div>

    <div class="subtabs">
      <button data-panel="evidence" class="active">Evidence</button>
      <button data-panel="shap">Attribution</button>
      <button data-panel="graph">Network</button>
      <button data-panel="flows">Money trail</button>
      <button data-panel="actions">Actions</button>
    </div>

    <div class="panel active" id="panel-evidence">${renderEvidence(a, d, data)}</div>
    <div class="panel" id="panel-shap">${renderShap(d)}</div>
    <div class="panel" id="panel-graph">
      <svg id="graph-svg"></svg>
      <div class="legend">
        <span><i style="background:#2ecc9b"></i>Safe</span>
        <span><i style="background:#f2c14e"></i>Medium</span>
        <span><i style="background:#ff8c42"></i>High</span>
        <span><i style="background:#ff4d5e"></i>Critical</span>
        <span><i style="background:#35507d"></i>Funds transfer</span>
        <span><i style="background:#b45cff"></i>Shared device</span>
        <span class="faint">Drag a node to pin it.</span>
      </div>
      <div id="gnn-explain" class="spacer"></div>
    </div>
    <div class="panel" id="panel-flows"><div class="loading">Loading money trail…</div></div>
    <div class="panel" id="panel-actions">${renderActions(a, data)}</div>
  `;

  $$('#detail .subtabs button').forEach((btn) =>
    btn.addEventListener('click', () => {
      $$('#detail .subtabs button').forEach((b) => b.classList.remove('active'));
      $$('#detail .panel').forEach((p) => p.classList.remove('active'));
      btn.classList.add('active');
      $('#panel-' + btn.dataset.panel).classList.add('active');
      if (btn.dataset.panel === 'graph') loadGraph(a.account_id);
      if (btn.dataset.panel === 'flows') loadFlows(a.account_id);
    })
  );

  wireActions(a);
}

function renderEvidence(a, d, data) {
  const ev = a.evidence.length ? a.evidence : d.evidence;
  const tickets = data.detail.account_id && (data.notes || []);
  let html = ev
    .map(
      (e) => `
    <div class="evidence sev-${esc(e.severity)}">
      <div class="ecode">${esc(e.code)}</div>
      <div class="elabel">${esc(e.label)}</div>
      <div class="edetail">${esc(e.detail)}</div>
    </div>`
    )
    .join('');
  if (d.ring) {
    html += `<div class="spacer"></div>
      <h3 style="font-size:13px;margin:0 0 8px">Cluster ${esc(d.ring.ring_id)}</h3>
      <table>
        <tr><th>Flagged members</th><td>${d.ring.size}</td></tr>
        <tr><th>Associated accounts</th><td>${(d.ring.associated || []).length} <span class="faint">(linked by shared hardware; not themselves flagged)</span></td></tr>
        <tr><th>Typology</th><td>${esc(d.ring.typology_hint)}</td></tr>
        <tr><th>Density</th><td>${d.ring.density}</td></tr>
        <tr><th>Internal turnover</th><td>${fmtINR(d.ring.total_amount)}</td></tr>
        <tr><th>Shared devices</th><td class="mono">${esc((d.ring.shared_devices || []).join(', ') || '-')}</td></tr>
        <tr><th>Collectors</th><td class="mono">${esc((d.ring.collector_accounts || []).join(', '))}</td></tr>
        <tr><th>Cash-out</th><td class="mono">${esc((d.ring.cashout_accounts || []).join(', '))}</td></tr>
      </table>`;
  }
  if (a.linked_tickets && a.linked_tickets.length) {
    html += `<div class="spacer"></div>
      <h3 style="font-size:13px;margin:0 0 6px">Linked government tickets</h3>
      <div class="mono muted" style="font-size:11.5px">${esc(a.linked_tickets.join(', '))}</div>`;
  }
  return html || '<div class="muted">No evidence recorded.</div>';
}

function renderShap(d) {
  if (!d.shap || !d.shap.length) return '<div class="muted">No attributions available.</div>';
  const max = Math.max(...d.shap.map((s) => Math.abs(s.contribution)), 1e-6);
  return `
    <p class="muted" style="margin-top:0;font-size:12.5px">
      Exact TreeSHAP contributions in log-odds. They sum, with the model's base value,
      to the raw margin — these are not sampled approximations.
    </p>
    ${d.shap
      .map(
        (s) => `
      <div class="shap-row">
        <div class="shap-name" title="${esc(s.feature)}">${esc(s.feature)}</div>
        <div class="shap-track">
          <div class="shap-fill ${s.contribution < 0 ? 'neg' : ''}"
               style="width:${((Math.abs(s.contribution) / max) * 100).toFixed(1)}%"></div>
        </div>
        <div class="shap-val">${s.contribution > 0 ? '+' : ''}${s.contribution.toFixed(3)}</div>
      </div>`
      )
      .join('')}`;
}

function renderActions(a, data) {
  const r = data.restriction;
  return `
    <div class="actions-row">
      <button class="act" data-act="STEP_UP_AUTH">Step-up auth</button>
      <button class="act" data-act="HOLD_CREDIT">Hold credit</button>
      <button class="act danger" data-act="FREEZE_DEBIT">Freeze debit</button>
      <button class="act danger" data-act="FULL_FREEZE">Full freeze</button>
      <button class="act" id="btn-revert">Revert</button>
    </div>
    <div class="actions-row">
      <button class="act primary" id="btn-str">Draft STR</button>
      <button class="act" data-resolve="CONFIRMED_FRAUD">Confirm fraud</button>
      <button class="act" data-resolve="FALSE_POSITIVE">Mark false positive</button>
    </div>
    <p class="muted" style="font-size:12.5px">
      Recommended by the engine:
      <b>${esc((a.suggested_actions || []).join(', ') || 'MONITOR')}</b>.
      The kill-switch will refuse or downgrade any action that lacks corroboration
      from at least two independent layers, and every refusal is audited.
    </p>
    <div id="action-out">${
      r
        ? `<pre class="out">Active restriction: ${esc(r.action)} since ${esc(r.taken_at)}\nReason: ${esc(r.reason)}</pre>`
        : '<span class="muted">No active restriction on this account.</span>'
    }</div>`;
}

function wireActions(a) {
  $$('#panel-actions [data-act]').forEach((btn) =>
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        const res = await api(
          `/api/actions/killswitch?account_id=${encodeURIComponent(a.account_id)}&action=${btn.dataset.act}`,
          { method: 'POST' }
        );
        const dec = res.decision;
        $('#action-out').innerHTML = `<pre class="out">${esc(JSON.stringify(dec, null, 2))}</pre>`;
        loadOverview();
      } catch (err) {
        $('#action-out').innerHTML = `<pre class="out">Error: ${esc(err.message)}</pre>`;
      } finally {
        btn.disabled = false;
      }
    })
  );

  const revert = $('#btn-revert');
  if (revert)
    revert.addEventListener('click', async () => {
      try {
        await api(`/api/actions/revert?account_id=${encodeURIComponent(a.account_id)}`, { method: 'POST' });
        $('#action-out').innerHTML = '<span class="muted">Restriction reverted and audited.</span>';
        loadOverview();
      } catch (err) {
        $('#action-out').innerHTML = `<pre class="out">${esc(err.message)}</pre>`;
      }
    });

  const strBtn = $('#btn-str');
  if (strBtn)
    strBtn.addEventListener('click', async () => {
      const res = await fetch(`/api/reports/str/${encodeURIComponent(a.alert_id)}?as_text=true`);
      $('#action-out').innerHTML = `<pre class="out">${esc(await res.text())}</pre>`;
    });

  $$('#panel-actions [data-resolve]').forEach((btn) =>
    btn.addEventListener('click', async () => {
      await api(
        `/api/alerts/${encodeURIComponent(a.alert_id)}/resolve?status=${btn.dataset.resolve}&investigator=analyst@bank`,
        { method: 'POST' }
      );
      await loadQueue();
      loadOverview();
    })
  );
}

/* ------------------------------------------------------------ money trail */

async function loadFlows(accountId) {
  const el = $('#panel-flows');
  try {
    const data = await api(`/api/accounts/${encodeURIComponent(accountId)}/flows`);
    if (!data.paths.length) {
      el.innerHTML = '<div class="muted">No outbound time-respecting path found from this account.</div>';
      return;
    }
    el.innerHTML = `
      <p class="muted" style="margin-top:0;font-size:12.5px">
        Each hop must occur <em>after</em> the previous one and within 48 hours — money
        cannot be forwarded before it arrives. Value retention below 1.0 is the commission
        each mule takes.
      </p>
      ${data.paths
        .slice(0, 8)
        .map(
          (p) => `
        <div style="border:1px solid var(--border);border-radius:9px;padding:11px;margin-bottom:10px">
          <div class="mono" style="font-size:11.5px;margin-bottom:6px">
            ${p.path.map((n, i) => `<span class="pill band-${bandOf(p.risk_along_path[i])}">${esc(n)}</span>`).join(' <span class="faint">→</span> ')}
          </div>
          <div class="muted" style="font-size:12px">
            ${p.n_hops} hop(s) &middot; ${fmtINR(p.total_amount)} in, ${fmtINR(p.terminal_amount)} out
            &middot; ${p.elapsed_minutes} min elapsed
            &middot; median gap ${p.median_hop_minutes} min
            &middot; value retention <b>${(p.value_retention * 100).toFixed(1)}%</b>
          </div>
        </div>`
        )
        .join('')}`;
  } catch (err) {
    el.innerHTML = `<div class="muted">${esc(err.message)}</div>`;
  }
}

const bandOf = (s) => (s >= 85 ? 'CRITICAL' : s >= 65 ? 'HIGH' : s >= 40 ? 'MEDIUM' : 'SAFE');

/* ------------------------------------------------- force-directed network */

async function loadGraph(accountId) {
  const svg = $('#graph-svg');
  svg.innerHTML = '<text x="20" y="30" fill="#8fa3c0" font-size="12">Loading network…</text>';
  let data;
  try {
    data = await api(`/api/accounts/${encodeURIComponent(accountId)}/graph?hops=1&max_nodes=55`);
  } catch (err) {
    svg.innerHTML = `<text x="20" y="30" fill="#8fa3c0" font-size="12">${esc(err.message)}</text>`;
    return;
  }
  drawForceGraph(svg, data);
  loadGnnExplain(accountId);
}

function drawForceGraph(svg, data) {
  const W = svg.clientWidth || 800;
  const H = 480;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

  const bandColour = { SAFE: '#2ecc9b', MEDIUM: '#f2c14e', HIGH: '#ff8c42', CRITICAL: '#ff4d5e' };
  const nodes = data.nodes.map((n, i) => ({
    ...n,
    x: W / 2 + Math.cos((i / data.nodes.length) * 2 * Math.PI) * (n.is_centre ? 0 : 150) + (Math.random() - 0.5) * 40,
    y: H / 2 + Math.sin((i / data.nodes.length) * 2 * Math.PI) * (n.is_centre ? 0 : 130) + (Math.random() - 0.5) * 40,
    vx: 0, vy: 0, pinned: n.is_centre,
  }));
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const links = data.edges.filter((e) => byId[e.source] && byId[e.target]);

  // Verlet-style relaxation: repulsion between all pairs, springs along edges,
  // weak gravity to the centre. Enough for 60 nodes, no dependency required.
  for (let step = 0; step < 320; step++) {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let d2 = dx * dx + dy * dy || 0.01;
        const f = 2600 / d2;
        const d = Math.sqrt(d2);
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy;
      }
    }
    for (const l of links) {
      const a = byId[l.source], b = byId[l.target];
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - 95) * 0.012;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
    }
    for (const n of nodes) {
      n.vx += (W / 2 - n.x) * 0.0016;
      n.vy += (H / 2 - n.y) * 0.0016;
      if (!n.pinned) { n.x += n.vx * 0.55; n.y += n.vy * 0.55; }
      n.vx *= 0.72; n.vy *= 0.72;
      n.x = Math.max(28, Math.min(W - 28, n.x));
      n.y = Math.max(24, Math.min(H - 24, n.y));
    }
  }

  const maxAmt = Math.max(...links.map((l) => l.amount), 1);
  const parts = [];
  for (const l of links) {
    const a = byId[l.source], b = byId[l.target];
    const w = l.type === 'DEVICE' ? 1.6 : 0.8 + (l.amount / maxAmt) * 3.4;
    parts.push(
      `<line class="link-${l.type}" x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}" stroke-width="${w.toFixed(2)}" opacity="0.75"><title>${esc(l.type)} ${l.amount ? fmtINR(l.amount) : ''} (${l.count})</title></line>`
    );
  }
  for (const n of nodes) {
    const r = n.is_centre ? 13 : 6 + Math.min(6, n.risk / 18);
    parts.push(
      `<circle cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="${r.toFixed(1)}" fill="${bandColour[n.band]}" stroke="${n.is_centre ? '#fff' : '#0b0f17'}" stroke-width="${n.is_centre ? 2.5 : 1.5}" data-id="${esc(n.id)}" style="cursor:pointer"><title>${esc(n.id)} · risk ${n.risk} · ${esc(n.band)} · KYC ${esc(n.kyc)} · ${n.vintage_days}d old</title></circle>`
    );
    if (n.is_centre || n.risk >= 55) {
      parts.push(
        `<text class="node-label" x="${n.x.toFixed(1)}" y="${(n.y - r - 5).toFixed(1)}" text-anchor="middle">${esc(n.id)}</text>`
      );
    }
  }
  svg.innerHTML = parts.join('');

  svg.querySelectorAll('circle').forEach((c) =>
    c.addEventListener('click', () => {
      const id = c.dataset.id;
      const alert = state.alerts.find((a) => a.account_id === id);
      if (alert) selectAlert(alert.alert_id);
    })
  );
}

async function loadGnnExplain(accountId) {
  const el = $('#gnn-explain');
  el.innerHTML = '<span class="muted">Computing GNNExplainer edge mask…</span>';
  try {
    const g = await api(`/api/accounts/${encodeURIComponent(accountId)}/gnn-explain`);
    if (!g.edges || !g.edges.length) {
      el.innerHTML = '<span class="muted">No edges available to explain.</span>';
      return;
    }
    el.innerHTML = `
      <h3 style="font-size:13px;margin:14px 0 6px">Which relationships drove the graph score</h3>
      <p class="muted" style="font-size:12px;margin:0 0 8px">
        A learned mask over the ${g.n_subgraph_edges} edges of this account's two-hop
        neighbourhood, keeping ${(g.edges_retained * 100).toFixed(1)}% of them.
        Deleting the selected edges moves the score by
        ${(g.necessity * 100).toFixed(0)}% (necessity).
        <b>${(g.self_feature_share * 100).toFixed(0)}%</b> of this account's graph score
        comes from its own feature vector rather than from message passing — when that
        share is high, the neighbourhood is corroborating evidence rather than the
        cause, and the edge mask can only explain the remainder.
      </p>
      <table>
        <thead><tr><th class="mono">From</th><th class="mono">To</th><th>Type</th><th class="right">Mask</th></tr></thead>
        <tbody>${g.edges
          .slice(0, 8)
          .map(
            (e) =>
              `<tr><td class="mono">${esc(e.source)}</td><td class="mono">${esc(e.target)}</td><td>${esc(e.edge_type)}</td><td class="right">${e.importance.toFixed(3)}</td></tr>`
          )
          .join('')}</tbody>
      </table>`;
  } catch (err) {
    el.innerHTML = `<span class="muted">${esc(err.message)}</span>`;
  }
}

/* ---------------------------------------------------------------- rings */

async function loadRings() {
  const data = await api('/api/rings?limit=40');
  $('#rings-count').textContent = data.count;
  if (!data.rings.length) {
    $('#rings-table').innerHTML = '<div class="loading">No rings detected at the current threshold.</div>';
    return;
  }
  $('#rings-table').innerHTML = `
    <table>
      <thead><tr>
        <th>Ring</th><th>Typology</th><th class="right">Flagged</th><th class="right">Assoc.</th><th class="right">Density</th>
        <th class="right">Turnover</th><th class="right">Mean risk</th><th class="mono">Collectors</th><th class="mono">Cash-out</th>
      </tr></thead>
      <tbody>${data.rings
        .map(
          (r) => `<tr>
          <td class="mono">${esc(r.ring_id)}</td>
          <td><span class="tag">${esc(r.typology_hint)}</span></td>
          <td class="right">${r.size}</td>
          <td class="right">${(r.associated || []).length}</td>
          <td class="right">${r.density.toFixed(3)}</td>
          <td class="right">${fmtINR(r.total_amount)}</td>
          <td class="right"><span class="pill band-${bandOf(r.mean_risk)}">${r.mean_risk.toFixed(0)}</span></td>
          <td class="mono">${esc((r.collector_accounts || []).slice(0, 2).join(', '))}</td>
          <td class="mono">${esc((r.cashout_accounts || []).slice(0, 2).join(', '))}</td>
        </tr>`
        )
        .join('')}</tbody>
    </table>`;
}

/* ----------------------------------------------------------- live scoring */

async function scoreLive(kind) {
  const out = $('#live-out');
  out.textContent = 'Scoring…';
  const alerts = state.alerts;
  let src = 'ACC0000001', dst = 'ACC0000002', amount = 4500, ben = 720;

  if (kind === 'mule' && alerts.length) {
    dst = alerts[Math.floor(Math.random() * Math.min(5, alerts.length))].account_id;
    amount = 95000;
    ben = 0.2;
  } else {
    const n = 1 + Math.floor(Math.random() * 3000);
    src = 'ACC' + String(n).padStart(7, '0');
    dst = 'ACC' + String(n + 7).padStart(7, '0');
    amount = Math.round(200 + Math.random() * 20000);
  }

  const body = {
    transaction: {
      txn_id: 'LIVE-' + Date.now(),
      timestamp: new Date().toISOString(),
      src_account: src,
      dst_account: dst,
      amount,
      channel: amount > 100000 ? 'IMPS' : 'UPI',
      txn_type: 'P2P',
      beneficiary_age_hours: ben,
    },
  };
  try {
    const res = await api('/api/score/transaction', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    out.textContent = JSON.stringify(res, null, 2);
    state.liveLog.unshift(res);
    renderLiveLog();
  } catch (err) {
    out.textContent = 'Error: ' + err.message;
  }
}

function renderLiveLog() {
  const rows = state.liveLog.slice(0, 30);
  $('#live-log').innerHTML = rows.length
    ? `<table>
        <thead><tr><th class="mono">Txn</th><th>Decision</th><th class="right">Risk</th><th class="right">Latency</th></tr></thead>
        <tbody>${rows
          .map(
            (r) => `<tr>
            <td class="mono">${esc(r.txn_id)}</td>
            <td><span class="pill band-${esc(r.band)}">${esc(r.decision)}</span></td>
            <td class="right">${r.risk_score.toFixed(1)}</td>
            <td class="right">${r.latency_ms.toFixed(3)} ms</td>
          </tr>`
          )
          .join('')}</tbody>
      </table>`
    : '<div class="loading">No decisions yet.</div>';
}

/* ------------------------------------------------------------ shield tab */

async function analyseAPK(file) {
  const out = $('#shield-out');
  out.innerHTML = '<div class="loading"><div class="spin"></div>Triaging package…</div>';
  const fd = new FormData();
  fd.append('file', file);
  try {
    const res = await api('/api/shield/analyze?inject=true', { method: 'POST', body: fd });
    renderShield(res);
    loadOverview();
  } catch (err) {
    out.innerHTML = `<div class="muted">${esc(err.message)}</div>`;
  }
}

function renderShield(res) {
  const r = res.report;
  $('#shield-out').innerHTML = `
    <div style="display:flex;align-items:center;gap:14px;margin-bottom:14px">
      <div class="detail-score band-${esc(r.risk_band)}">
        <div class="n">${Math.round(r.risk_score)}</div><div class="b">${esc(r.risk_band)}</div>
      </div>
      <div>
        <div class="mono" style="font-size:13px">${esc(r.package_name || r.file_name)}</div>
        <div class="faint mono" style="font-size:10.5px;word-break:break-all">${esc(r.sha256)}</div>
        <div class="muted" style="font-size:12px">
          ${(r.size_bytes / 1024).toFixed(0)} KB &middot; ${r.dex_count} DEX &middot;
          ${r.signed ? 'signed' : '<b>unsigned</b>'} &middot;
          obfuscation ${(r.obfuscation_ratio * 100).toFixed(0)}%
        </div>
      </div>
    </div>

    ${r.toxic_combinations.length
      ? `<h3 style="font-size:13px;margin:0 0 6px">Toxic permission combinations</h3>
         ${r.toxic_combinations.map((t) => `<div class="evidence sev-HIGH"><div class="edetail">${esc(t)}</div></div>`).join('')}`
      : ''}

    ${r.findings.length
      ? `<h3 style="font-size:13px;margin:14px 0 6px">Findings</h3>
         <table><tbody>${r.findings
           .map((f) => `<tr><td class="mono" style="width:190px">${esc(f.code)}</td><td>${esc(f.detail)}</td></tr>`)
           .join('')}</tbody></table>`
      : ''}

    <h3 style="font-size:13px;margin:14px 0 6px">Financial indicators handed to Mule Hunter</h3>
    <table><tbody>
      <tr><th style="width:150px">UPI handles</th><td class="mono">${esc(r.upi_vpas.join(', ') || '-')}</td></tr>
      <tr><th>Bank accounts</th><td class="mono">${esc(r.accounts.join(', ') || '-')}</td></tr>
      <tr><th>IFSC codes</th><td class="mono">${esc(r.ifsc_codes.join(', ') || '-')}</td></tr>
      <tr><th>C2 addresses</th><td class="mono">${esc(r.ip_addresses.join(', ') || '-')}</td></tr>
      <tr><th>Packers</th><td>${esc(r.packers.join(', ') || 'none detected')}</td></tr>
    </tbody></table>

    <h3 style="font-size:13px;margin:14px 0 6px">Hand-off</h3>
    <p class="muted" style="font-size:12.5px;margin:0">
      ${res.iocs.length} indicator(s) injected into the fraud graph;
      <b>${res.matched_accounts.length}</b> already correspond to accounts in this bank.
    </p>
    ${res.matched_accounts.length
      ? `<table style="margin-top:8px"><thead><tr><th class="mono">Account</th><th class="mono">VPA</th><th class="right">Current risk</th></tr></thead>
         <tbody>${res.matched_accounts
           .map(
             (m) =>
               `<tr><td class="mono">${esc(m.account_id)}</td><td class="mono">${esc(m.vpa)}</td><td class="right"><span class="pill band-${bandOf(m.current_risk || 0)}">${(m.current_risk || 0).toFixed(0)}</span></td></tr>`
           )
           .join('')}</tbody></table>`
      : ''}`;
}

/* -------------------------------------------------------------- pipeline */

async function loadPipeline() {
  const [a, m] = await Promise.all([api('/api/agents'), api('/api/metrics')]);
  $('#agents').innerHTML = a.agents
    .map(
      (g) => `
    <div class="agent-step">
      <div class="agent-n">${g.layer}</div>
      <div>
        <div class="agent-name">${esc(g.name)}</div>
        <div class="agent-role">${esc(g.role)}</div>
        <div class="agent-io">in: ${esc(g.consumes.join(', '))} &nbsp;→&nbsp; out: ${esc(g.produces.join(', '))}</div>
      </div>
    </div>`
    )
    .join('');

  if (!m || m.detail) {
    $('#metrics').innerHTML = `<div class="muted">${esc((m && m.detail) || 'No metrics recorded.')}</div>`;
    return;
  }
  const layers = m.layers || {};
  $('#metrics').innerHTML = `
    <table>
      <thead><tr><th>Layer</th><th class="right">ROC-AUC</th><th class="right">PR-AUC</th><th class="right">Recall@1%</th></tr></thead>
      <tbody>${Object.entries(layers)
        .map(
          ([k, v]) =>
            `<tr><td>${esc(k)}</td><td class="right">${(v.roc_auc || 0).toFixed(4)}</td><td class="right">${(v.pr_auc || 0).toFixed(4)}</td><td class="right">${((v.recall_at_1pct || 0) * 100).toFixed(1)}%</td></tr>`
        )
        .join('')}</tbody>
    </table>
    ${m.baseline
      ? `<h3 style="font-size:13px;margin:16px 0 6px">Versus the rule engine it replaces</h3>
         <table><tbody>
           <tr><th>Rule-engine alerts</th><td class="right">${fmtNum(m.baseline.alerts)}</td></tr>
           <tr><th>Rule-engine precision</th><td class="right">${(m.baseline.precision * 100).toFixed(1)}%</td></tr>
           <tr><th>BODHI precision at equal recall</th><td class="right">${(m.baseline.bodhi_precision_at_equal_recall * 100).toFixed(1)}%</td></tr>
           <tr><th>False positives removed</th><td class="right">${(m.baseline.false_positive_reduction * 100).toFixed(1)}%</td></tr>
         </tbody></table>`
      : ''}`;
}

/* ------------------------------------------------------------ compliance */

async function loadCompliance() {
  const [audit, ctr] = await Promise.all([api('/api/audit?limit=40'), api('/api/reports/ctr')]);
  $('#audit').innerHTML = `
    <table><tbody>
      <tr><th style="width:150px">Entries</th><td>${fmtNum(audit.entries)}</td></tr>
      <tr><th>Chain valid</th><td>${audit.chain_valid ? '<span class="pill band-SAFE">VERIFIED</span>' : '<span class="pill band-CRITICAL">BROKEN at ' + audit.first_invalid_seq + '</span>'}</td></tr>
      <tr><th>Head hash</th><td class="mono" style="word-break:break-all;font-size:10.5px">${esc(audit.head)}</td></tr>
    </tbody></table>
    <div class="spacer"></div>
    <table>
      <thead><tr><th>Seq</th><th>Actor</th><th>Event</th></tr></thead>
      <tbody>${audit.tail
        .slice()
        .reverse()
        .map((e) => `<tr><td>${e.seq}</td><td class="mono">${esc(e.actor)}</td><td>${esc(e.event)}</td></tr>`)
        .join('')}</tbody>
    </table>`;

  $('#ctr').innerHTML = ctr.count
    ? `<table>
        <thead><tr><th class="mono">Account</th><th>Date</th><th class="right">Cash total</th><th class="right">Txns</th></tr></thead>
        <tbody>${ctr.candidates
          .slice(0, 30)
          .map(
            (c) =>
              `<tr><td class="mono">${esc(c.account_id)}</td><td>${esc(c.date)}</td><td class="right">${fmtINR(c.cash_total)}</td><td class="right">${c.n_transactions}</td></tr>`
          )
          .join('')}</tbody>
      </table>`
    : '<div class="loading">No aggregate cash activity above the threshold.</div>';
}

/* ------------------------------------------------------------------ init */

const VIEW_LOADERS = {
  investigate: () => {},
  rings: loadRings,
  live: renderLiveLog,
  shield: () => {},
  pipeline: loadPipeline,
  compliance: loadCompliance,
};

$('#btn-live-random').addEventListener('click', () => scoreLive('random'));
$('#btn-live-mule').addEventListener('click', () => scoreLive('mule'));
$('#btn-live-burst').addEventListener('click', async () => {
  for (let i = 0; i < 25; i++) await scoreLive(i % 4 === 0 ? 'mule' : 'random');
});

$('#apk-file').addEventListener('change', (e) => {
  if (e.target.files[0]) analyseAPK(e.target.files[0]);
});
const drop = $('#drop');
['dragenter', 'dragover'].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('hover'); })
);
['dragleave', 'drop'].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('hover'); })
);
drop.addEventListener('drop', (e) => {
  if (e.dataTransfer.files[0]) analyseAPK(e.dataTransfer.files[0]);
});

$('#btn-shield-sample').addEventListener('click', async () => {
  const out = $('#shield-out');
  out.innerHTML = '<div class="loading"><div class="spin"></div>Fetching bundled sample…</div>';
  try {
    const res = await fetch('/static/samples/bodhi_demo_trojan.apk');
    if (!res.ok) throw new Error('Sample not built. Run `make sample-apk`.');
    const blob = await res.blob();
    analyseAPK(new File([blob], 'bodhi_demo_trojan.apk'));
  } catch (err) {
    out.innerHTML = `<div class="muted">${esc(err.message)}</div>`;
  }
});

(async function init() {
  try {
    await loadOverview();
    await loadQueue();
  } catch (err) {
    $('#queue').innerHTML = `<div class="loading">Backend not ready: ${esc(err.message)}</div>`;
  }
})();
