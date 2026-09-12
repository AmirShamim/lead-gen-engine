/**
 * Lead Generation Portal & Mobile Queue — Client Engine
 * Handles real-time telemetry, stage filtering, lead details modal,
 * pipeline controller execution, and mobile 1-tap dispatches.
 */

let currentTab = 'dashboard';
let currentStatusFilter = 'ALL';
let currentTierFilter = 'ALL';
let searchQuery = '';
let currentPage = 0;
const PAGE_SIZE = 25;
let activeJobId = null;
let pollTimer = null;

// ============================================================
// Initialization & Tab Navigation
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initBatchPills();
  initSearch();
  loadStats();
  loadLeads();
  loadQueue();
  loadAccepted();
});

function initTabs() {
  const tabs = document.querySelectorAll('.nav-tab');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const target = tab.dataset.tab;
      switchTab(target);
    });
  });
}

function switchTab(tabName) {
  currentTab = tabName;
  document.querySelectorAll('.nav-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tabName);
  });
  document.querySelectorAll('.tab-view').forEach(v => {
    v.style.display = v.id === `view-${tabName}` ? 'block' : 'none';
  });

  if (tabName === 'dashboard') loadStats();
  if (tabName === 'explorer') loadLeads();
  if (tabName === 'queue') loadQueue();
  if (tabName === 'accepted') loadAccepted();
}

function showToast(message) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3200);
}

// ============================================================
// Stats & Telemetry
// ============================================================

async function loadStats() {
  try {
    const res = await fetch('/api/stats');
    if (!res.ok) return;
    const data = await res.json();

    // KPIs
    updateElement('kpi-total-leads', data.total_leads.toLocaleString());
    updateElement('kpi-queued-leads', (data.status_counts['QUEUED'] || 0).toLocaleString());
    updateElement('kpi-sent-leads', (data.status_counts['SENT'] || 0).toLocaleString());
    updateElement('kpi-accepted-leads', (data.status_counts['ACCEPTED'] || 0).toLocaleString());
    updateElement('kpi-spend-total', `$${data.spend.total.toFixed(2)}`);
    updateElement('kpi-sweet-spot', (data.follower_tiers['SWEET_SPOT_500_5K'] || 0).toLocaleString());

    // Nav badges
    updateElement('nav-queued-badge', data.status_counts['QUEUED'] || 0);

    // Funnel counts on pills
    document.querySelectorAll('.funnel-pill').forEach(pill => {
      const st = pill.dataset.status;
      if (st === 'ALL') {
        pill.querySelector('.count').textContent = data.total_leads;
      } else {
        pill.querySelector('.count').textContent = data.status_counts[st] || 0;
      }
    });

    // Tier counts
    updateElement('tier-count-sweet-spot', data.follower_tiers['SWEET_SPOT_500_5K'] || 0);
    updateElement('tier-count-over-5k', data.follower_tiers['OVER_5K'] || 0);
  } catch (err) {
    console.error('Error loading stats:', err);
  }
}

function updateElement(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

// ============================================================
// Pipeline Controller (Run Stages from UI)
// ============================================================

function initBatchPills() {
  const pills = document.querySelectorAll('.batch-pill');
  pills.forEach(pill => {
    pill.addEventListener('click', () => {
      pills.forEach(p => p.classList.remove('selected'));
      pill.classList.add('selected');
    });
  });
}

function getSelectedBatchSize() {
  const sel = document.querySelector('.batch-pill.selected');
  return sel ? parseInt(sel.dataset.size, 10) : 15;
}

async function triggerPipelineRun() {
  const stageSelect = document.getElementById('run-stage-select');
  const stage = stageSelect ? stageSelect.value : 'stage4';
  const batchSize = getSelectedBatchSize();
  const generateAiToggle = document.getElementById('toggle-generate-ai');
  const generateAi = generateAiToggle ? generateAiToggle.checked : false;
  const followerFilterSelect = document.getElementById('run-follower-filter');
  const followerFilter = followerFilterSelect ? followerFilterSelect.value : 'ALL';

  const runBtn = document.getElementById('btn-run-stage');
  if (runBtn) {
    runBtn.disabled = true;
    runBtn.innerHTML = '⏳ Executing Batch...';
  }

  const logBox = document.getElementById('log-console');
  if (logBox) logBox.innerHTML = '<div class="log-line">🚀 Dispatching pipeline task to server...</div>';

  try {
    const res = await fetch('/api/pipeline/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        stage_id: stage,
        batch_size: batchSize,
        generate_ai_note: generateAi,
        follower_filter: followerFilter,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      alert('Error launching job: ' + (err.detail || 'Unknown'));
      if (runBtn) {
        runBtn.disabled = false;
        runBtn.innerHTML = '▶ Run Selected Batch';
      }
      return;
    }

    const data = await res.json();
    activeJobId = data.job_id;
    pollJobStatus();
  } catch (err) {
    alert('Failed to trigger pipeline run: ' + err.message);
    if (runBtn) {
      runBtn.disabled = false;
      runBtn.innerHTML = '▶ Run Selected Batch';
    }
  }
}

function pollJobStatus() {
  if (!activeJobId) return;
  if (pollTimer) clearInterval(pollTimer);

  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/pipeline/status/${activeJobId}`);
      if (!res.ok) return;
      const job = await res.json();

      const logBox = document.getElementById('log-console');
      if (logBox && job.logs) {
        logBox.innerHTML = job.logs.map(l => `<div class="log-line">${escapeHtml(l)}</div>`).join('');
        logBox.scrollTop = logBox.scrollHeight;
      }

      if (job.status === 'COMPLETED' || job.status === 'FAILED') {
        clearInterval(pollTimer);
        const runBtn = document.getElementById('btn-run-stage');
        if (runBtn) {
          runBtn.disabled = false;
          runBtn.innerHTML = '▶ Run Selected Batch';
        }
        showToast(job.status === 'COMPLETED' ? '✓ Batch completed successfully!' : '❌ Batch failed');
        loadStats();
        loadLeads();
        loadQueue();
      }
    } catch (e) {
      console.error(e);
    }
  }, 1000);
}

// ============================================================
// Stages Explorer & Leads Table
// ============================================================

function initSearch() {
  const searchInput = document.getElementById('table-search-input');
  if (searchInput) {
    let timeout;
    searchInput.addEventListener('input', () => {
      clearTimeout(timeout);
      timeout = setTimeout(() => {
        searchQuery = searchInput.value;
        currentPage = 0;
        loadLeads();
      }, 300);
    });
  }

  // Funnel pills
  document.querySelectorAll('.funnel-pill').forEach(pill => {
    pill.addEventListener('click', () => {
      document.querySelectorAll('.funnel-pill').forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      currentStatusFilter = pill.dataset.status;
      currentPage = 0;
      loadLeads();
    });
  });

  // Follower tier pills
  document.querySelectorAll('.tier-filter-pill').forEach(pill => {
    pill.addEventListener('click', () => {
      document.querySelectorAll('.tier-filter-pill').forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      currentTierFilter = pill.dataset.tier;
      currentPage = 0;
      loadLeads();
    });
  });
}

async function loadLeads() {
  const tbody = document.getElementById('leads-tbody');
  if (!tbody) return;

  tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:30px;color:#94a3b8;">Loading leads...</td></tr>';

  try {
    const url = new URL('/api/leads', window.location.origin);
    url.searchParams.set('status', currentStatusFilter);
    url.searchParams.set('follower_tier', currentTierFilter);
    if (searchQuery) url.searchParams.set('search', searchQuery);
    url.searchParams.set('limit', PAGE_SIZE);
    url.searchParams.set('offset', currentPage * PAGE_SIZE);

    const res = await fetch(url);
    if (!res.ok) throw new Error('Failed to fetch');
    const data = await res.json();

    if (!data.leads || data.leads.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:40px;color:#64748b;">No matching leads found.</td></tr>';
      updateElement('pagination-info', '0 / 0');
      return;
    }

    const total = data.total;
    const startIdx = currentPage * PAGE_SIZE + 1;
    const endIdx = Math.min(startIdx + data.leads.length - 1, total);
    updateElement('pagination-info', `${startIdx}–${endIdx} of ${total.toLocaleString()}`);

    tbody.innerHTML = data.leads.map(lead => {
      const tierBadge = renderTierBadge(lead.follower_tier, lead.follower_count, lead.linkedin_connection_count);
      const statusBadge = `<span class="badge badge-${lead.status.toLowerCase()}">${lead.status}</span>`;
      const founderText = lead.founder_name ? `<b>${escapeHtml(lead.founder_name)}</b><br><small style="color:#94a3b8;">${escapeHtml(lead.founder_title || 'Founder')}</small>` : '<span style="color:#64748b;">—</span>';
      const companyText = `<b>${escapeHtml(lead.business_name)}</b><br><small style="color:#94a3b8;">${escapeHtml(lead.city || '')}</small>`;
      const linkedInLink = lead.linkedin_url ? `<a href="${lead.linkedin_url}" target="_blank" onclick="event.stopPropagation();" style="color:#0077b5;font-weight:600;">Profile ↗</a>` : '<span style="color:#64748b;">—</span>';

      return `
        <tr onclick="openLeadModal('${lead.id}')" style="cursor:pointer;">
          <td>${statusBadge}</td>
          <td>${companyText}</td>
          <td>${founderText}</td>
          <td>${tierBadge}</td>
          <td>${linkedInLink}</td>
          <td style="color:#94a3b8;font-size:12px;">${lead.personalized_note ? '✓ Note Ready' : (lead.ab_variant === 'variant_b' ? 'Blank Connect' : '—')}</td>
          <td><button class="nav-tab" style="padding:4px 8px;font-size:11px;">Inspect</button></td>
        </tr>
      `;
    }).join('');

  } catch (err) {
    console.error(err);
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:30px;color:#ef4444;">Error loading leads.</td></tr>';
  }
}

function prevPage() {
  if (currentPage > 0) {
    currentPage--;
    loadLeads();
  }
}

function nextPage() {
  currentPage++;
  loadLeads();
}

function renderTierBadge(tier, followers, connections) {
  if (tier === 'SWEET_SPOT_500_5K') {
    const txt = followers ? `${(followers/1000).toFixed(1)}k Followers` : `${connections || 500}+ Conn`;
    return `<span class="badge badge-sweet-spot">🎯 Sweet Spot (${txt})</span>`;
  } else if (tier === 'OVER_5K') {
    const txt = followers ? `${(followers/1000).toFixed(1)}k` : `${connections || 5000}+`;
    return `<span class="badge badge-over-5k">⚠️ Macro (${txt})</span>`;
  } else if (tier === 'UNDER_500') {
    return `<span class="badge badge-under-500">🌱 &lt;500 Conn</span>`;
  }
  return `<span style="color:#64748b;font-size:12px;">Unknown</span>`;
}

// ============================================================
// Lead Details Modal / Drawer
// ============================================================

async function openLeadModal(leadId) {
  const modal = document.getElementById('lead-modal');
  const body = document.getElementById('lead-modal-body');
  if (!modal || !body) return;

  body.innerHTML = '<div style="text-align:center;padding:40px;color:#94a3b8;">Loading lead details...</div>';
  modal.classList.add('open');

  try {
    const res = await fetch(`/api/leads/${leadId}`);
    if (!res.ok) throw new Error('Not found');
    const lead = await res.json();

    const transitionsHtml = (lead.transitions || []).map(t => `
      <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-size:12px;">
        <div><b>${t.from_status}</b> → <b style="color:#60a5fa;">${t.to_status}</b> <span style="color:#94a3b8;">(${t.triggered_by_stage})</span></div>
        <div style="color:#64748b;">${t.created_at || ''}</div>
      </div>
    `).join('') || '<div style="color:#64748b;font-size:12px;">No recorded transitions.</div>';

    body.innerHTML = `
      <div style="margin-bottom:16px;">
        <span class="badge badge-${lead.status.toLowerCase()}" style="margin-bottom:8px;">${lead.status}</span>
        <h2 style="margin:0 0 4px 0;color:#fff;">${escapeHtml(lead.founder_name || 'Founder Unresolved')}</h2>
        <p style="margin:0;color:#94a3b8;font-size:14px;">${escapeHtml(lead.founder_title || 'Founder')} at <b>${escapeHtml(lead.business_name)}</b> (${escapeHtml(lead.city || '')})</p>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;background:rgba(15,23,42,0.6);padding:12px;border-radius:8px;">
        <div>
          <div style="font-size:11px;color:#64748b;text-transform:uppercase;">LinkedIn Profile</div>
          <div>${lead.linkedin_url ? `<a href="${lead.linkedin_url}" target="_blank" style="font-weight:bold;">${escapeHtml(lead.linkedin_headline || 'View Profile ↗')}</a>` : 'Not Resolved'}</div>
        </div>
        <div>
          <div style="font-size:11px;color:#64748b;text-transform:uppercase;">Audience / Follower Tier</div>
          <div>${renderTierBadge(lead.follower_tier, lead.follower_count, lead.linkedin_connection_count)}</div>
        </div>
        <div>
          <div style="font-size:11px;color:#64748b;text-transform:uppercase;">Website & Domain</div>
          <div>${lead.domain ? `<a href="${lead.website_uri || '#'}" target="_blank">${lead.domain}</a> (${lead.website_status || 'OK'})` : '—'}</div>
        </div>
        <div>
          <div style="font-size:11px;color:#64748b;text-transform:uppercase;">Copyright / Activity</div>
          <div>Year: ${lead.website_copyright_year || 'Unknown'} | Status: ${lead.activity_check_status || '—'}</div>
        </div>
      </div>

      ${lead.business_summary ? `
        <div style="margin-bottom:16px;">
          <div style="font-size:11px;color:#64748b;text-transform:uppercase;margin-bottom:4px;">Business Focus & Tagline</div>
          <div style="background:rgba(255,255,255,0.03);padding:10px;border-radius:6px;font-size:13px;color:#cbd5e1;">${escapeHtml(lead.business_summary)}</div>
        </div>
      ` : ''}

      ${lead.personalized_note ? `
        <div style="margin-bottom:16px;">
          <div style="font-size:11px;color:#60a5fa;text-transform:uppercase;margin-bottom:4px;font-weight:bold;">AI Personalized Connection Note (${lead.note_char_count} chars)</div>
          <div style="background:rgba(59,130,246,0.08);border:1px solid rgba(59,130,246,0.2);padding:12px;border-radius:8px;font-size:13px;color:#f1f5f9;line-height:1.4;">${escapeHtml(lead.personalized_note)}</div>
        </div>
      ` : ''}

      <div>
        <div style="font-size:11px;color:#64748b;text-transform:uppercase;margin-bottom:8px;font-weight:bold;">State Transition Audit Trail</div>
        <div style="background:rgba(0,0,0,0.25);border-radius:6px;padding:10px;">${transitionsHtml}</div>
      </div>
    `;
  } catch (err) {
    body.innerHTML = '<div style="color:#ef4444;text-align:center;padding:30px;">Error loading lead details.</div>';
  }
}

function closeLeadModal() {
  const modal = document.getElementById('lead-modal');
  if (modal) modal.classList.remove('open');
}

// ============================================================
// Mobile Action Queue (/queue)
// ============================================================

async function loadQueue() {
  const container = document.getElementById('queue-cards-container');
  if (!container) return;

  const filterPill = document.querySelector('.queue-tier-pill.active');
  const tier = filterPill ? filterPill.dataset.tier : 'SWEET_SPOT_500_5K';

  container.innerHTML = '<div style="text-align:center;padding:40px;color:#94a3b8;">Loading queued leads...</div>';

  try {
    const url = new URL('/api/queue', window.location.origin);
    url.searchParams.set('limit', 15);
    if (tier && tier !== 'ALL') url.searchParams.set('follower_tier', tier);

    const res = await fetch(url);
    if (!res.ok) throw new Error('Failed to fetch queue');
    const leads = await res.json();

    if (!leads || leads.length === 0) {
      container.innerHTML = `
        <div style="text-align:center;padding:60px 20px;color:#94a3b8;">
          <div style="font-size:48px;margin-bottom:12px;">🎉</div>
          <h3 style="color:#fff;margin-bottom:8px;">Queue is Clear!</h3>
          <p style="font-size:13px;color:#64748b;">No leads currently queued matching your filter. Use the Controller above to queue a new batch.</p>
        </div>
      `;
      return;
    }

    container.innerHTML = leads.map(lead => renderMobileCard(lead)).join('');
  } catch (err) {
    container.innerHTML = '<div style="color:#ef4444;text-align:center;padding:30px;">Error loading queue.</div>';
  }
}

function renderMobileCard(lead) {
  const tierBadge = renderTierBadge(lead.follower_tier, lead.follower_count, lead.linkedin_connection_count);
  const note = lead.personalized_note || '';

  return `
    <div class="mobile-card" id="card-${lead.id}">
      <div class="founder-title-row">
        <div>
          <div class="founder-name">${escapeHtml(lead.founder_name || 'Founder')}</div>
          <div class="founder-biz">${escapeHtml(lead.founder_title || 'Founder')} at <b>${escapeHtml(lead.business_name)}</b></div>
          <div style="font-size:12px;color:#64748b;margin-top:2px;">📍 ${escapeHtml(lead.city || '')}</div>
        </div>
        <div>${tierBadge}</div>
      </div>

      <!-- Glance & Type Section (Readable context for effortless manual typing) -->
      <div class="glance-box">
        <div class="glance-hook-title">⚡ Conversation Angle & Focus</div>
        <div class="glance-points">
          ${lead.business_summary ? `• <b>Focus:</b> ${escapeHtml(lead.business_summary)}<br>` : ''}
          • <b>Angle:</b> Peer connection with fellow founder in ${escapeHtml(lead.city || 'agency space')}.
        </div>
        ${note ? `
          <div class="glance-note">
            <b style="color:#60a5fa;">Suggested Hook:</b> "${escapeHtml(note)}"
          </div>
        ` : ''}
      </div>

      <!-- LinkedIn Deep Link -->
      <a href="${lead.linkedin_url || '#'}" target="_blank" class="mobile-btn-linkedin">
        Open LinkedIn Profile ↗
      </a>

      <!-- Action Row: Blank Connect (Recommended) vs Custom Note -->
      <div class="action-row">
        <button class="btn-blank-connect" onclick="markSent('${lead.id}', 'blank')">
          ✓ Sent Blank (Rec.)
        </button>
        <button class="btn-note-sent" onclick="markSent('${lead.id}', 'custom_note')">
          ✓ Sent Note
        </button>
      </div>
    </div>
  `;
}

async function markSent(leadId, outreachType) {
  const card = document.getElementById(`card-${leadId}`);
  if (card) {
    card.classList.add('slide-out');
  }

  try {
    const res = await fetch(`/api/leads/${leadId}/mark-sent?outreach_type=${outreachType}`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error('Failed to update lead');

    showToast(outreachType === 'blank' ? '✓ Blank connect sent! Zero bot footprint.' : '✓ Note sent recorded.');
    setTimeout(() => {
      if (card) card.remove();
      loadStats();
    }, 260);
  } catch (err) {
    alert('Error recording sent: ' + err.message);
    if (card) card.classList.remove('slide-out');
  }
}

// ============================================================
// Post-Connection Accepted Leads
// ============================================================

async function loadAccepted() {
  const container = document.getElementById('accepted-leads-container');
  if (!container) return;

  container.innerHTML = '<div style="text-align:center;padding:40px;color:#94a3b8;">Loading accepted connections...</div>';

  try {
    const res = await fetch('/api/accepted');
    if (!res.ok) throw new Error('Failed to fetch accepted');
    const leads = await res.json();

    if (!leads || leads.length === 0) {
      container.innerHTML = `
        <div style="text-align:center;padding:50px 20px;color:#94a3b8;">
          <div style="font-size:48px;margin-bottom:12px;">🏆</div>
          <h3 style="color:#fff;margin-bottom:8px;">No Accepted Leads Yet</h3>
          <p style="font-size:13px;color:#64748b;">When connections accept your invite, tap "Mark Accepted" to manage follow-up conversations here.</p>
        </div>
      `;
      return;
    }

    container.innerHTML = leads.map(lead => `
      <div class="kpi-card" style="margin-bottom:14px;">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
          <div>
            <h3 style="margin:0 0 2px 0;color:#fff;">${escapeHtml(lead.founder_name)}</h3>
            <p style="margin:0;color:#94a3b8;font-size:13px;">${escapeHtml(lead.founder_title || 'Founder')} at <b>${escapeHtml(lead.business_name)}</b> (${escapeHtml(lead.city || '')})</p>
          </div>
          <span class="badge badge-accepted">🏆 Accepted</span>
        </div>

        <div style="background:rgba(15,23,42,0.6);padding:10px;border-radius:6px;font-size:13px;color:#e2e8f0;margin-bottom:10px;">
          <b>Follow-up Talking Point:</b> ${escapeHtml(lead.personalized_note || lead.business_summary || 'Discuss synergy and services.')}
        </div>

        <a href="${lead.linkedin_url}" target="_blank" class="nav-tab" style="display:inline-flex;padding:8px 14px;background:#0077b5;color:#fff;border-radius:6px;font-weight:600;">
          Message on LinkedIn ↗
        </a>
      </div>
    `).join('');
  } catch (err) {
    container.innerHTML = '<div style="color:#ef4444;text-align:center;padding:30px;">Error loading accepted leads.</div>';
  }
}

// ============================================================
// Utilities
// ============================================================

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
