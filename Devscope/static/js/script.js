let currentSessionId = null;
let reportData = null;
let currentReportId = null;
let featureAnalysisInterval = null;
let notificationInterval = null;
let currentMode = 'founder';
let featureReportData = null;
let currentFeatureReportId = null;
// ── INIT ──────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  loadSessions();
  setupTextarea();
  const theme = localStorage.getItem('theme') || 'dark';
  document.documentElement.setAttribute('data-theme', theme);
  updateThemeBtn(theme);
  startNotificationPolling();
});

// ── THEME ─────────────────────────────────────────────────────────────────

document.getElementById('themeToggle').addEventListener('click', () => {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
  updateThemeBtn(next);
});

function updateThemeBtn(theme) {
  document.getElementById('themeToggle').textContent = theme === 'dark' ? '☀️' : '🌙';
}

document.getElementById('menuBtn').addEventListener('click', () => {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  overlay.classList.toggle('visible', sidebar.classList.toggle('open'));
});

function setupTextarea() {
  const ta = document.getElementById('userInput');
  ta.addEventListener('input', () => {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 140) + 'px';
  });
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

async function setMode(mode) {
  if (mode === currentMode) return;

  try {
    const res = await fetch('/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode })
    });
    const data = await res.json();
    if (data.error) {
      showToast('Could not switch mode.');
      return;
    }

    currentMode = data.mode;
    currentSessionId = data.session_id;
    updateModeUI(currentMode);

    document.getElementById('messages').innerHTML = '';
    document.getElementById('reportBar').style.display = 'none';
    hideWelcome();

    if (data.resumed) {
      const res2 = await fetch(`/sessions/${currentSessionId}`);
      const sessionData = await res2.json();
      sessionData.messages.forEach(m => appendMessage(m.role, m.content));
      if (sessionData.messages.length > 0) showReportButton();
    } else {
      appendMessage('assistant', data.greeting);
    }

    loadSessions();

function updateModeUI(mode) {
  document.querySelectorAll('.mode-toggle-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === mode);
  });
  document.getElementById('founderReportBtn').style.display = mode === 'founder' ? 'block' : 'none';
  document.getElementById('researcherReportBtn').style.display = mode === 'researcher' ? 'block' : 'none';
}

function fillInput(text) {
  const input = document.getElementById('userInput');
  input.value = text;
  input.focus();
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 140) + 'px';
}

// ── SEND MESSAGE ──────────────────────────────────────────────────────────

async function sendMessage() {
  const input = document.getElementById('userInput');
  const text = input.value.trim();
  if (!text) return;

  hideWelcome();
  appendMessage('user', text);
  input.value = '';
  input.style.height = 'auto';

  const typing = showTyping();

  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: currentSessionId })
    });
    const data = await res.json();
    removeTyping(typing);
    appendMessage('assistant', data.reply);
    if (data.session_id) currentSessionId = data.session_id;
    if (data.mode && data.mode !== currentMode) {
      currentMode = data.mode;
      updateModeUI(currentMode);
    }
    if (data.show_report) showReportButton();
    loadSessions();
    scrollToBottom();
  } catch (err) {
    removeTyping(typing);
    appendMessage('assistant', 'Something went wrong. Please try again.');
  }
}

// ── VALIDATE IDEA ─────────────────────────────────────────────────────────

async function validateIdea() {
  const input = document.getElementById('ideaInput');
  const idea = input.value.trim();
  if (!idea) return;

  hideWelcome();
  appendMessage('user', `Quick check: ${idea}`);
  input.value = '';

  const typing = showTyping();

  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: `Brutally honest quick take — 3 sentences max. Viable, too broad, or already exists? "${idea}"`,
        session_id: currentSessionId
      })
    });
    const data = await res.json();
    removeTyping(typing);
    appendMessage('assistant', data.reply);
    if (data.session_id) currentSessionId = data.session_id;
    loadSessions();
    scrollToBottom();
  } catch (err) {
    removeTyping(typing);
    appendMessage('assistant', 'Something went wrong. Try again.');
  }
}

// ── GENERATE REPORT ───────────────────────────────────────────────────────

async function generateReport() {
  const btn = document.querySelector('.generate-report-btn');
  btn.textContent = '⏳ Analyzing your product...';
  btn.disabled = true;

  try {
    const res = await fetch('/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: currentSessionId })
    });
    const data = await res.json();

    if (data.error) {
      showToast('Failed to generate report. Try again.');
      btn.textContent = '📊 Generate My Feature Report';
      btn.disabled = false;
      return;
    }

    reportData = data.report;
    currentReportId = data.report_id;
    renderReport(data.report);
    updateStatsPanel(data.report);
    document.getElementById('reportModal').style.display = 'flex';

// ── NEW: trigger deep dive prompt after a delay ──
if (data.deep_dive_ready) {
  setTimeout(() => {
    triggerDeepDivePrompt(data.report_id, data.stack_known, data.stack);
  }, 8000); // 8s — gives them time to glance at the report first
}

    // Start polling for async feature analyses
    startFeatureAnalysisPolling(data.report_id);

  } catch (err) {
    showToast('Something went wrong.');
  }

  btn.textContent = '📊 Generate My Feature Report';
  btn.disabled = false;
}

async function generateFeatureReport() {
  const btn = document.getElementById('researcherReportBtn');
  btn.textContent = '⏳ Researching feature...';
  btn.disabled = true;

  try {
    const res = await fetch('/feature-report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: currentSessionId })
    });
    const data = await res.json();

    if (data.error) {
      showToast('Failed to generate feature report. Try again.');
      btn.textContent = '🔬 Generate Feature Research Report';
      btn.disabled = false;
      return;
    }

    featureReportData = data.report;
    currentFeatureReportId = data.report_id;
    renderFeatureReport(data.report);
    document.getElementById('featureReportModal').style.display = 'flex';

  } catch (err) {
    showToast('Something went wrong.');
  }

  btn.textContent = '🔬 Generate Feature Research Report';
  btn.disabled = false;
}

// ── DEEP DIVE ─────────────────────────────────────────────────────────────

async function triggerDeepDivePrompt(reportId, stackKnown, stack) {
  // Close the report modal first so the chat feels natural
  closeReport();

  // Wait a beat, then drop the question into chat
  await new Promise(r => setTimeout(r, 1200));

  if (stackKnown) {
    // Stack is known — ask if they want to dive in
    appendDeepDiveInvite(reportId, stack);
  } else {
    // Stack unknown — ask for it before we can proceed
    appendStackAskMessage(reportId);
  }
}

function appendDeepDiveInvite(reportId, stack) {
  const messages = document.getElementById('messages');
  const div = document.createElement('div');
  div.className = 'message assistant';
  div.innerHTML = `
    <div class="msg-avatar">DS</div>
    <div class="msg-content">
      Report locked in. Want a deep dive into your core features — I'll break down 
      exactly how to build each one for your <strong>${stack}</strong> stack, 
      with the best libraries, gotchas, and a paste-ready Claude prompt per feature?
      <div class="quick-reply-row inline" style="margin-top:10px">
        <button class="quick-reply-chip" onclick="startDeepDive('${reportId}', '${stack}')">
          🔬 Yes, deep dive
        </button>
        <button class="quick-reply-chip" onclick="this.closest('.message').remove()">
          Skip for now
        </button>
      </div>
    </div>
  `;
  messages.appendChild(div);
  scrollToBottom();
}

function appendStackAskMessage(reportId) {
  const messages = document.getElementById('messages');
  const div = document.createElement('div');
  div.className = 'message assistant';
  div.id = 'stack-ask-msg';
  div.innerHTML = `
    <div class="msg-avatar">DS</div>
    <div class="msg-content">
      Report's ready. Before I deep dive into your features, what's your stack? 
      I need this to give you the right libraries and implementation steps — not generic ones.
      <div class="quick-reply-row inline" style="margin-top:10px" id="stack-options-row">
        <button class="quick-reply-chip" onclick="confirmStack('${reportId}', 'React + Node.js')">React + Node.js</button>
        <button class="quick-reply-chip" onclick="confirmStack('${reportId}', 'Flutter')">Flutter</button>
        <button class="quick-reply-chip" onclick="confirmStack('${reportId}', 'Next.js + Supabase')">Next.js + Supabase</button>
        <button class="quick-reply-chip" onclick="confirmStack('${reportId}', 'React Native')">React Native</button>
      </div>
      <div style="margin-top:8px;display:flex;gap:6px">
        <input id="customStackInput" class="stack-custom-input" 
          placeholder="or type your stack..." 
          style="flex:1;padding:7px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg3);color:var(--text1);font-size:13px"
        />
        <button class="quick-reply-chip" onclick="confirmStack('${reportId}', document.getElementById('customStackInput').value)">→</button>
      </div>
    </div>
  `;
  messages.appendChild(div);
  scrollToBottom();
}

async function confirmStack(reportId, stack) {
  if (!stack || !stack.trim()) return;
  stack = stack.trim();

  // Remove the ask message
  const askMsg = document.getElementById('stack-ask-msg');
  if (askMsg) askMsg.remove();

  appendMessage('user', `My stack: ${stack}`);
  await startDeepDive(reportId, stack);
}

async function startDeepDive(reportId, stack) {
  const typing = showTyping();

  try {
    const endpoint = stack
      ? '/deep-dive/start'
      : '/deep-dive/start';

    const res = await fetch('/deep-dive/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ report_id: reportId, stack: stack || '' })
    });
    const data = await res.json();
    removeTyping(typing);

    if (data.error) {
      appendMessage('assistant', 'Something went wrong starting the deep dive. Try again.');
      return;
    }

    appendMessage('assistant', data.reply);
    scrollToBottom();

  } catch (err) {
    removeTyping(typing);
    appendMessage('assistant', 'Deep dive failed to start. Please try again.');
  }
}

// ── ASYNC FEATURE ANALYSIS POLLING ────────────────────────────────────────

function startFeatureAnalysisPolling(reportId) {
  if (featureAnalysisInterval) clearInterval(featureAnalysisInterval);

  showAnalysisStatus('🔍 Analyzing features in background...');

  featureAnalysisInterval = setInterval(async () => {
    try {
      const res = await fetch(`/report/${reportId}/feature-analysis`);
      const data = await res.json();

      if (data.analyses && Object.keys(data.analyses).length > 0) {
        // Update report data with new analyses
        if (reportData) {
          reportData.feature_analyses = data.analyses;
          updateFeatureAnalysisCards(data.analyses);
        }

        updateAnalysisStatus(
          `🔍 Analyzing features... ${data.completed}/${data.total} done`
        );
      }

      if (data.done) {
        clearInterval(featureAnalysisInterval);
        featureAnalysisInterval = null;
        updateAnalysisStatus('✅ Deep feature analysis complete!');
        setTimeout(hideAnalysisStatus, 3000);

        if (reportData) {
          renderReport(reportData);
          updateStatsPanel(reportData);
        }
      }
    } catch (err) {
      // Silent fail — analysis is background
    }
  }, 3000);
}

function showAnalysisStatus(msg) {
  let bar = document.getElementById('analysisStatusBar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'analysisStatusBar';
    bar.className = 'analysis-status-bar';
    const modalBody = document.getElementById('reportContent');
    if (modalBody) modalBody.parentElement.insertBefore(bar, modalBody);
  }
  bar.textContent = msg;
  bar.style.display = 'block';
}

function updateAnalysisStatus(msg) {
  const bar = document.getElementById('analysisStatusBar');
  if (bar) bar.textContent = msg;
}

function hideAnalysisStatus() {
  const bar = document.getElementById('analysisStatusBar');
  if (bar) bar.style.display = 'none';
}

function updateFeatureAnalysisCards(analyses) {
  Object.entries(analyses).forEach(([featureName, analysis]) => {
    const cards = document.querySelectorAll('.feature-card');
    cards.forEach(card => {
      const nameEl = card.querySelector('.feature-name');
      if (nameEl && nameEl.textContent.trim() === featureName) {
        // Add or update the deep analysis section
        let deepDiv = card.querySelector('.feature-deep-analysis');
        if (!deepDiv) {
          deepDiv = document.createElement('div');
          deepDiv.className = 'feature-deep-analysis';
          card.appendChild(deepDiv);
        }
        deepDiv.innerHTML = renderDeepAnalysis(analysis);
      }
    });
  });
}

function renderDeepAnalysis(analysis) {
  if (!analysis) return '';
  return `
    <div class="deep-analysis-section">
      <div class="deep-analysis-title">🔬 Deep Analysis</div>
      <div class="deep-meta">
        <span class="deep-time">⏱ ${analysis.time_estimate || '—'}</span>
      </div>
      ${analysis.biggest_mistake ? `
        <div class="deep-mistake">
          ⚠️ Common mistake: ${analysis.biggest_mistake}
        </div>
      ` : ''}
      ${analysis.existing_solutions && analysis.existing_solutions.length ? `
        <div class="deep-solutions">
          <div class="deep-solutions-label">🆓 Free tools that help:</div>
          ${analysis.existing_solutions.map(s => `
            <div class="deep-solution-item">
              <strong>${s.name}</strong> — ${s.what_it_gives_you_free}
            </div>
          `).join('')}
        </div>
      ` : ''}
      ${analysis.build_steps && analysis.build_steps.length ? `
        <div class="deep-steps">
          <div class="deep-steps-label">📋 Build steps:</div>
          ${analysis.build_steps.map((s, i) => `
            <div class="deep-step">${i + 1}. ${s}</div>
          `).join('')}
        </div>
      ` : ''}
      ${analysis.prompt_for_claude ? `
        <button class="feature-btn btn-copy-prompt"
          onclick="copyFeaturePrompt(this, \`${analysis.prompt_for_claude.replace(/`/g, '\\`')}\`)">
          📋 Copy Claude Prompt for This Feature
        </button>
      ` : ''}
    </div>
  `;
}

function copyFeaturePrompt(btn, prompt) {
  navigator.clipboard.writeText(prompt);
  const original = btn.textContent;
  btn.textContent = '✅ Copied!';
  setTimeout(() => { btn.textContent = original; }, 2000);
}

// ── PROMPT GENERATOR ──────────────────────────────────────────────────────

async function generatePrompts() {
  if (!currentReportId) {
    showToast('Generate a report first.');
    return;
  }

  const btn = document.getElementById('generatePromptsBtn');
  if (btn) {
    btn.textContent = '⏳ Generating prompts...';
    btn.disabled = true;
  }

  try {
    const res = await fetch(`/report/${currentReportId}/generate-prompts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await res.json();

    if (data.error) {
      showToast('Failed to generate prompts.');
      return;
    }

    renderPromptSection(data.prompts);
    document.getElementById('promptModal').style.display = 'flex';

  } catch (err) {
    showToast('Something went wrong.');
  }

  if (btn) {
    btn.textContent = '🤖 Generate Claude Prompts';
    btn.disabled = false;
  }
}

function renderPromptSection(prompts) {
  const el = document.getElementById('promptContent');
  if (!el || !prompts) return;

  el.innerHTML = `
    <div class="prompt-section">
      <div class="prompt-section-title">⚡ Quick Prompt</div>
      <p class="prompt-desc">For simple, focused tasks</p>
      <div class="prompt-box">${prompts.quick_prompt || '—'}</div>
      <button class="copy-prompt-btn" onclick="copyText(\`${escapeBackticks(prompts.quick_prompt)}\`, this)">
        📋 Copy Quick Prompt
      </button>
    </div>

    <div class="prompt-section">
      <div class="prompt-section-title">🚀 Full Build Prompt</div>
      <p class="prompt-desc">Complete prompt to start the entire build — paste into Claude or Cursor</p>
      <div class="prompt-box">${prompts.full_prompt || '—'}</div>
      <button class="copy-prompt-btn" onclick="copyText(\`${escapeBackticks(prompts.full_prompt)}\`, this)">
        📋 Copy Full Prompt
      </button>
    </div>

    <div class="prompt-section">
      <div class="prompt-section-title">🖱 Cursor-Optimized Prompt</div>
      <p class="prompt-desc">Tuned for Cursor AI with file structure hints</p>
      <div class="prompt-box">${prompts.cursor_prompt || '—'}</div>
      <button class="copy-prompt-btn" onclick="copyText(\`${escapeBackticks(prompts.cursor_prompt)}\`, this)">
        📋 Copy Cursor Prompt
      </button>
    </div>

    ${prompts.token_estimate ? `
      <div class="prompt-meta">
        📊 Estimated tokens: <strong>${prompts.token_estimate}</strong>
      </div>
    ` : ''}

    ${prompts.what_this_skips && prompts.what_this_skips.length ? `
      <div class="prompt-skips">
        <div class="prompt-skips-title">✅ What Claude won't need to ask about:</div>
        ${prompts.what_this_skips.map(s => `<div class="prompt-skip-item">• ${s}</div>`).join('')}
      </div>
    ` : ''}
  `;
}

function escapeBackticks(str) {
  return (str || '').replace(/`/g, '\\`').replace(/\$/g, '\\$');
}

function copyText(text, btn) {
  navigator.clipboard.writeText(text);
  const orig = btn.textContent;
  btn.textContent = '✅ Copied!';
  setTimeout(() => { btn.textContent = orig; }, 2000);
}

// ── RENDER REPORT ─────────────────────────────────────────────────────────

function renderReport(r) {
  const el = document.getElementById('reportContent');

  const diffBadge = (d) => {
    const cls = d === 'Easy' ? 'easy' : d === 'Medium' ? 'medium' : 'hard';
    return `<span class="badge badge-${cls}">${d}</span>`;
  };

  const readinessColor = r.readiness_score >= 70 ? 'var(--success)' :
                         r.readiness_score >= 40 ? 'var(--warning)' : 'var(--danger)';

  const features = (r.features || []).map((f, i) => {
    const isCut = f.cut_or_keep === 'CUT';
    const existingAnalysis = r.feature_analyses && r.feature_analyses[f.name];

    return `
    <div class="feature-card ${f.shipped ? 'feature-shipped' : ''} ${isCut ? 'feature-cut' : ''}"
         id="fc-${i}">
      <div class="feature-card-top">
        <label class="feature-checkbox-label">
          <input type="checkbox" class="feature-checkbox" ${f.shipped ? 'checked' : ''}
            onchange="toggleFeature('${f.name.replace(/'/g, "\\'")}', this)" />
          <span class="feature-name ${f.shipped ? 'shipped' : ''} ${isCut ? 'cut' : ''}">
            ${isCut ? '❌ ' : ''}${f.name}
          </span>
        </label>
        <div class="feature-badges">
          ${diffBadge(f.difficulty)}
          <span class="badge" style="background:rgba(79,158,255,0.1);color:var(--accent);border:1px solid rgba(79,158,255,0.2)">
            ${f.efficiency}%
          </span>
        </div>
      </div>

      <div class="efficiency-bar">
        <div class="efficiency-fill" style="width:${f.efficiency}%"></div>
      </div>
      <div class="efficiency-label">Efficiency score</div>

      <p class="feature-why">${f.why}</p>
      <div class="feature-gap">🎯 Competitor gap: ${f.competitor_gap}</div>

      ${f.risk ? `<div class="feature-risk">⚠️ Risk: ${f.risk}</div>` : ''}

      ${isCut ? `
        <div class="feature-cut-reason">
          ✂️ Cut from v1: ${f.cut_reason || 'Save for v2'}
        </div>
      ` : ''}

      ${f.suggested_additions && f.suggested_additions.length ? `
        <div class="feature-suggestions">
          <div class="feature-suggestions-label">💡 Also consider:</div>
          ${f.suggested_additions.map(s =>
            `<span class="suggestion-chip">${s}</span>`
          ).join('')}
        </div>
      ` : ''}

      <div class="feature-actions">
        <button class="feature-btn btn-deadline"
          onclick="openDeadlineModal('${f.name.replace(/'/g, "\\'")}')">
          🗓 ${f.deadline ? 'Due: ' + formatDate(f.deadline) : 'Set Deadline'}
        </button>
      </div>

      <!-- Deep analysis section — filled by polling -->
      <div class="feature-deep-analysis" id="analysis-${i}">
        ${existingAnalysis ? renderDeepAnalysis(existingAnalysis) : `
          <div class="deep-analysis-loading">
            <span class="deep-loading-dot"></span>
            <span class="deep-loading-dot"></span>
            <span class="deep-loading-dot"></span>
            <span style="font-size:11px;color:var(--text3);margin-left:6px">
              Analyzing feature...
            </span>
          </div>
        `}
      </div>
    </div>
  `;
  }).join('');

  const missingFeatures = r.missing_features && r.missing_features.length ? `
    <div class="report-section">
      <div class="report-section-title">🚨 Features you missed but should build</div>
      ${r.missing_features.map(f => `
        <div class="missing-feature-card">
          <div class="missing-feature-top">
            <div class="missing-feature-name">+ ${f.name}</div>
            <span class="badge badge-${f.priority === 'High' ? 'hard' : 'medium'}">
              ${f.priority || 'Medium'}
            </span>
          </div>
          <div class="missing-feature-why">${f.why}</div>
        </div>
      `).join('')}
    </div>
  ` : '';

  const roadmap = r.roadmap ? `
    <div class="report-section">
      <div class="report-section-title">4-week roadmap</div>
      <div class="roadmap-grid">
        ${Object.entries(r.roadmap).map(([week, task]) => `
          <div class="roadmap-item">
            <div class="roadmap-week">${week.toUpperCase()}</div>
            <div class="roadmap-task">${task}</div>
          </div>
        `).join('')}
      </div>
    </div>
  ` : '';

  el.innerHTML = `
    <!-- READINESS SCORE -->
    ${r.readiness_score !== undefined ? `
    <div class="report-section">
      <div class="report-section-title">🎯 Build Readiness Score</div>
      <div class="readiness-card">
        <div class="readiness-score" style="color:${readinessColor}">
          ${r.readiness_score}%
        </div>
        <div class="readiness-bar-track">
          <div class="readiness-bar-fill"
               style="width:${r.readiness_score}%;background:${readinessColor}">
          </div>
        </div>
        ${r.readiness_gaps && r.readiness_gaps.length ? `
          <div class="readiness-gaps">
            <div class="readiness-gaps-title">Close these gaps before starting:</div>
            ${r.readiness_gaps.map(g => `
              <div class="readiness-gap-item">⚠ ${g}</div>
            `).join('')}
          </div>
        ` : ''}
      </div>
    </div>` : ''}

    <!-- CORE INFO -->
    ${r.target_user ? `
    <div class="report-section">
      <div class="report-section-title">👤 Target User</div>
      <div class="disclaimer">${r.target_user}</div>
    </div>` : ''}

    ${r.persona ? `
    <div class="report-section">
      <div class="report-section-title">Dev Persona</div>
      <div class="disclaimer">${r.persona}</div>
    </div>` : ''}

    ${r.what_to_build_first ? `
    <div class="report-section">
      <div class="report-section-title">⚡ Build This First</div>
      <div class="disclaimer" style="color:var(--accent);border-color:var(--accent-border)">
        ${r.what_to_build_first}
      </div>
    </div>` : ''}

    <!-- FEATURES -->
    <div class="report-section">
      <div class="report-section-title">Feature Recommendations</div>
      <div class="features-progress-bar">
        <div class="features-progress-label">
          <span>Shipped</span>
          <span id="shipped-count">0 / ${(r.features||[]).length}</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill" id="progress-fill" style="width:0%"></div>
        </div>
      </div>
      ${features}
    </div>

    <!-- MISSING FEATURES -->
    ${missingFeatures}

    <!-- FEATURES TO CUT -->
    ${r.features_to_cut && r.features_to_cut.length ? `
    <div class="report-section">
      <div class="report-section-title">✂️ Cut from v1</div>
      ${r.features_to_cut.map(item => `
        <div class="cut-feature-item">• ${item}</div>
      `).join('')}
    </div>` : ''}

    <!-- COMPETITOR RADAR -->
    ${r.competitor_radar ? `
    <div class="report-section">
      <div class="report-section-title">🔍 Competitor Radar</div>
      <div class="disclaimer">${r.competitor_radar}</div>
    </div>` : ''}

    ${roadmap}

    <!-- BUILD PROMPT -->
    ${r.build_prompt ? `
    <div class="report-section">
      <div class="report-section-title">🤖 Claude Build Prompt</div>
      <p style="font-size:12px;color:var(--text2);margin-bottom:8px">
        Paste this into Claude or Cursor to start building immediately:
      </p>
      <div class="build-prompt-box">${r.build_prompt}</div>
      <div style="display:flex;gap:8px;margin-top:10px">
        <button class="feature-btn btn-ship" style="flex:1" onclick="copyBuildPrompt()">
          📋 Copy Prompt
        </button>
        <button class="feature-btn btn-deadline" style="flex:1"
          id="generatePromptsBtn" onclick="generatePrompts()">
          🤖 Generate All Prompts
        </button>
      </div>
    </div>` : ''}

    <div class="disclaimer">
      ${r.disclaimer || 'AI-generated advice. Validate with real users.'}
    </div>
  `;

  updateShippedProgress(r);
}

// DEVELOPER'S REPORT GENERATION
function renderFeatureReport(r) {
  const el = document.getElementById('featureReportContent');

  const verdictClass = r.verdict === 'BUILD IT' ? 'verdict-build'
    : r.verdict === 'SKIP IT' ? 'verdict-skip' : 'verdict-later';

  const shouldBuild = r.should_build || {};
  const shouldNot = r.should_not_build || {};
  const complexity = r.complexity || {};

  el.innerHTML = `
    <div class="report-section">
      <div class="report-section-title">Feature Under Analysis</div>
      <div class="disclaimer" style="font-family:var(--font);font-size:14px;font-weight:600;color:var(--text)">
        ${r.feature_name || '—'}
      </div>
      ${r.app_type ? `<div class="feature-gap" style="margin-top:8px">App type: ${r.app_type}</div>` : ''}
      ${r.target_users ? `<div class="feature-gap">Target users: ${r.target_users}</div>` : ''}
      ${r.stack && r.stack !== 'Not specified' ? `<div class="feature-gap">Stack: ${r.stack}</div>` : ''}
    </div>

    <div class="report-section" style="text-align:center">
      <div class="verdict-badge ${verdictClass}">${r.verdict || '—'}</div>
      <div class="feature-gap" style="margin-top:8px">Confidence: ${r.confidence_score ?? '—'}%</div>
      ${r.verdict_reason ? `<p class="feature-why" style="margin-top:10px">${r.verdict_reason}</p>` : ''}
    </div>

    ${shouldBuild.reasons && shouldBuild.reasons.length ? `
    <div class="report-section">
      <div class="report-section-title">✅ Reasons to Build It</div>
      ${shouldBuild.reasons.map(reason => `<div class="cut-feature-item">• ${reason}</div>`).join('')}
      ${shouldBuild.apps_that_did_it_right && shouldBuild.apps_that_did_it_right.length ? `
        <div style="margin-top:10px">
          ${shouldBuild.apps_that_did_it_right.map(a => `
            <div class="feature-card" style="margin-bottom:8px">
              <div class="feature-name">${a.app}</div>
              <p class="feature-why">${a.how_they_did_it}</p>
              <div class="feature-gap">Result: ${a.result}</div>
            </div>
          `).join('')}
        </div>
      ` : ''}
    </div>` : ''}

    ${shouldNot.reasons && shouldNot.reasons.length ? `
    <div class="report-section">
      <div class="report-section-title">❌ Reasons to Skip It</div>
      ${shouldNot.reasons.map(reason => `<div class="cut-feature-item">• ${reason}</div>`).join('')}
      ${shouldNot.apps_that_got_burned && shouldNot.apps_that_got_burned.length ? `
        <div style="margin-top:10px">
          ${shouldNot.apps_that_got_burned.map(a => `
            <div class="feature-card" style="margin-bottom:8px">
              <div class="feature-name">${a.app}</div>
              <p class="feature-why">${a.what_went_wrong}</p>
              <div class="feature-gap">Lesson: ${a.lesson}</div>
            </div>
          `).join('')}
        </div>
      ` : ''}
    </div>` : ''}

    ${r.build_conditions ? `
    <div class="report-section">
      <div class="report-section-title">Build If</div>
      <div class="disclaimer">${r.build_conditions}</div>
    </div>` : ''}

    ${r.skip_conditions ? `
    <div class="report-section">
      <div class="report-section-title">Skip If</div>
      <div class="disclaimer">${r.skip_conditions}</div>
    </div>` : ''}

    ${Object.keys(complexity).length ? `
    <div class="report-section">
      <div class="report-section-title">Implementation Complexity</div>
      <div class="feature-gap">Effort: ${complexity.effort || '—'} · Time: ${complexity.time_estimate || '—'}</div>
      ${complexity.frontend ? `<div class="feature-gap">Frontend: ${complexity.frontend}</div>` : ''}
      ${complexity.backend ? `<div class="feature-gap">Backend: ${complexity.backend}</div>` : ''}
      ${complexity.database ? `<div class="feature-gap">Database: ${complexity.database}</div>` : ''}
      ${complexity.third_party ? `<div class="feature-gap">Third-party: ${complexity.third_party}</div>` : ''}
    </div>` : ''}

    ${r.alternatives && r.alternatives.length ? `
    <div class="report-section">
      <div class="report-section-title">Alternatives to Consider</div>
      ${r.alternatives.map(alt => `
        <div class="feature-card" style="margin-bottom:8px">
          <div class="feature-name">${alt.name}</div>
          <p class="feature-why">Why better: ${alt.why_better}</p>
          <div class="feature-gap">Tradeoff: ${alt.tradeoff}</div>
        </div>
      `).join('')}
    </div>` : ''}

    ${r.best_libraries && r.best_libraries.length ? `
    <div class="report-section">
      <div class="report-section-title">Best Libraries & Tools</div>
      ${r.best_libraries.map(lib => `<div class="cut-feature-item">• ${lib.name} — ${lib.why} (${lib.stack_fit})</div>`).join('')}
    </div>` : ''}

    ${r.week1_implementation ? `
    <div class="report-section">
      <div class="report-section-title">Week 1 Implementation</div>
      <div class="disclaimer">${r.week1_implementation}</div>
    </div>` : ''}

    ${r.starter_prompt ? `
    <div class="report-section">
      <div class="report-section-title">🤖 Starter Prompt</div>
      <div class="build-prompt-box">${r.starter_prompt}</div>
      <button class="feature-btn btn-ship" style="margin-top:10px" onclick="copyFeatureStarterPrompt()">
        📋 Copy Prompt
      </button>
    </div>` : ''}

    <div class="disclaimer">
      ${r.disclaimer || 'AI-generated analysis. Validate before committing.'}
    </div>
  `;
}
// DEVELOPER'S REPORT GENERATION
function closeFeatureReport() {
  document.getElementById('featureReportModal').style.display = 'none';
}

function downloadFeatureReport() {
  if (!currentFeatureReportId) return;
  window.open(`/report/${currentFeatureReportId}/download`, '_blank');
}

async function copyFeatureReport() {
  if (!featureReportData) return;
  await navigator.clipboard.writeText(JSON.stringify(featureReportData, null, 2));
  showToast('Report copied');
}

function copyFeatureStarterPrompt() {
  if (!featureReportData?.starter_prompt) return;
  navigator.clipboard.writeText(featureReportData.starter_prompt);
  showToast('✅ Starter prompt copied!');
}

// ── STATS PANEL ───────────────────────────────────────────────────────────

function updateStatsPanel(r) {
  const panel = document.getElementById('statsPanel');
  if (!panel) return;

  const features = r.features || [];
  const total = features.length;
  const shipped = features.filter(f => f.shipped).length;
  const easy = features.filter(f => f.difficulty === 'Easy').length;
  const medium = features.filter(f => f.difficulty === 'Medium').length;
  const hard = features.filter(f => f.difficulty === 'Hard').length;
  const tocut = features.filter(f => f.cut_or_keep === 'CUT').length;
  const avgEfficiency = total
    ? Math.round(features.reduce((a, f) => a + f.efficiency, 0) / total)
    : 0;
  const pct = total ? Math.round((shipped / total) * 100) : 0;
  const readiness = r.readiness_score || 0;
  const readinessColor = readiness >= 70 ? 'var(--success)' :
                         readiness >= 40 ? 'var(--warning)' : 'var(--danger)';

  panel.innerHTML = `
    <div class="stats-title">📊 Project Stats</div>

    <!-- READINESS -->
    <div class="stat-card" style="flex-direction:column;align-items:flex-start;gap:8px">
      <div class="stat-label">Build Readiness</div>
      <div class="stat-value" style="color:${readinessColor};font-size:22px;font-weight:700">
        ${readiness}%
      </div>
      <div class="mini-progress">
        <div class="mini-progress-fill" style="width:${readiness}%;background:${readinessColor}"></div>
      </div>
    </div>

    <!-- PERSONA -->
    <div class="stat-card persona-card">
      <div class="stat-card-icon">👤</div>
      <div>
        <div class="stat-label">Persona</div>
        <div class="stat-value accent">${r.persona || '—'}</div>
      </div>
    </div>

    ${r.stack ? `
    <div class="stat-card">
      <div class="stat-card-icon">🛠</div>
      <div>
        <div class="stat-label">Stack</div>
        <div class="stat-value small">${r.stack}</div>
      </div>
    </div>` : ''}

    ${r.claude_usage ? `
    <div class="stat-card">
      <div class="stat-card-icon">🤖</div>
      <div>
        <div class="stat-label">AI Plan</div>
        <div class="stat-value small">${r.claude_usage}</div>
      </div>
    </div>` : ''}

    <!-- PROGRESS RING -->
    <div class="stat-card progress-card">
      <div class="ring-wrap">
        <svg viewBox="0 0 56 56" class="ring-svg">
          <circle cx="28" cy="28" r="24" fill="none" stroke="var(--bg4)" stroke-width="5"/>
          <circle cx="28" cy="28" r="24" fill="none"
            stroke="url(#ringGrad)" stroke-width="5"
            stroke-dasharray="${2 * Math.PI * 24}"
            stroke-dashoffset="${2 * Math.PI * 24 * (1 - pct / 100)}"
            stroke-linecap="round"
            transform="rotate(-90 28 28)"/>
          <defs>
            <linearGradient id="ringGrad" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stop-color="var(--accent)"/>
              <stop offset="100%" stop-color="var(--accent2)"/>
            </linearGradient>
          </defs>
        </svg>
        <div class="ring-label">${pct}%</div>
      </div>
      <div>
        <div class="stat-label">Shipped</div>
        <div class="stat-value">${shipped} / ${total}</div>
        ${tocut > 0 ? `<div class="stat-label" style="margin-top:4px">${tocut} to cut</div>` : ''}
      </div>
    </div>

    <!-- EFFICIENCY -->
    <div class="stat-item">
      <div class="stat-label">Avg Efficiency</div>
      <div class="efficiency-meter">
        <div class="efficiency-meter-bar" style="width:${avgEfficiency}%"></div>
      </div>
      <div class="stat-value accent" style="margin-top:4px">${avgEfficiency}%</div>
    </div>

    <!-- DIFFICULTY BARS -->
    <div class="stat-item">
      <div class="stat-label">Difficulty</div>
      <div class="diff-bars">
        <div class="diff-bar-item">
          <div class="diff-bar-label">Easy</div>
          <div class="diff-bar-track">
            <div class="diff-bar-fill easy"
                 style="width:${total ? (easy/total*100) : 0}%"></div>
          </div>
          <div class="diff-bar-count">${easy}</div>
        </div>
        <div class="diff-bar-item">
          <div class="diff-bar-label">Med</div>
          <div class="diff-bar-track">
            <div class="diff-bar-fill medium"
                 style="width:${total ? (medium/total*100) : 0}%"></div>
          </div>
          <div class="diff-bar-count">${medium}</div>
        </div>
        <div class="diff-bar-item">
          <div class="diff-bar-label">Hard</div>
          <div class="diff-bar-track">
            <div class="diff-bar-fill hard"
                 style="width:${total ? (hard/total*100) : 0}%"></div>
          </div>
          <div class="diff-bar-count">${hard}</div>
        </div>
      </div>
    </div>

    <!-- FEATURE LIST -->
    <div class="stat-item">
      <div class="stat-label">Features</div>
      <div class="stat-feature-list">
        ${features.map(f => `
          <div class="stat-feature-item ${f.shipped ? 'done' : ''} ${f.cut_or_keep === 'CUT' ? 'cut' : ''}">
            <span class="stat-feature-dot ${f.shipped ? 'done' : ''}"></span>
            <span class="stat-feature-name">${f.name}</span>
            ${f.cut_or_keep === 'CUT' ? '<span class="stat-feature-cut">cut</span>' : ''}
          </div>
        `).join('')}
      </div>
    </div>

    ${r.what_to_build_first ? `
    <div class="stat-card build-first-card">
      <div class="stat-label">⚡ Build first</div>
      <div class="stat-value small" style="margin-top:4px">
        ${r.what_to_build_first.split(' ').slice(0, 10).join(' ')}...
      </div>
    </div>` : ''}

    <button class="stats-report-btn"
      onclick="document.getElementById('reportModal').style.display='flex'">
      View Full Report
    </button>
    <button class="stats-report-btn secondary" onclick="downloadReport()">
      ⬇ Download PDF
    </button>
    <button class="stats-report-btn secondary" onclick="generatePrompts()" style="margin-top:4px">
      🤖 Generate Prompts
    </button>
  `;
 
const mobilePanel = document.getElementById('mobileStatsPanel');
if (mobilePanel && mobilePanel.classList.contains('open')) {
  mobilePanel.innerHTML = panel.innerHTML;
}
}

function updateShippedProgress(r) {
  const features = r.features || [];
  const shipped = features.filter(f => f.shipped).length;
  const total = features.length;
  const pct = total ? Math.round((shipped / total) * 100) : 0;
  const countEl = document.getElementById('shipped-count');
  const fillEl = document.getElementById('progress-fill');
  if (countEl) countEl.textContent = `${shipped} / ${total}`;
  if (fillEl) fillEl.style.width = `${pct}%`;
}

// ── TOGGLE FEATURE ────────────────────────────────────────────────────────

async function toggleFeature(featureName, checkbox) {
  if (!currentReportId) return;
  try {
    const res = await fetch('/feature/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ report_id: currentReportId, feature_name: featureName })
    });
    const data = await res.json();
    if (data.report) {
      reportData = data.report;
      renderReport(data.report);
      updateStatsPanel(data.report);
      showToast(checkbox.checked ? '✅ Shipped!' : 'Marked unshipped');
    }
  } catch (err) {
    checkbox.checked = !checkbox.checked;
    showToast('Failed to update.');
  }
}

// ── DEADLINE MODAL ────────────────────────────────────────────────────────

let activeDeadlineFeature = null;

function openDeadlineModal(featureName) {
  activeDeadlineFeature = featureName;
  document.getElementById('deadlineFeatureName').textContent = featureName;
  const today = new Date().toISOString().slice(0, 16);
  document.getElementById('deadlineInput').min = today;
  document.getElementById('deadlineInput').value = '';
  document.getElementById('deadlineModal').style.display = 'flex';
}

function closeDeadlineModal() {
  document.getElementById('deadlineModal').style.display = 'none';
  activeDeadlineFeature = null;
}

async function saveDeadline() {
  const deadline = document.getElementById('deadlineInput').value;
  if (!deadline || !activeDeadlineFeature || !currentReportId) return;

  try {
    const res = await fetch('/feature/deadline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        report_id: currentReportId,
        feature_name: activeDeadlineFeature,
        deadline: deadline
      })
    });
    const data = await res.json();
    if (data.success) {
      if (reportData?.features) {
        const f = reportData.features.find(x => x.name === activeDeadlineFeature);
        if (f) f.deadline = deadline;
        renderReport(reportData);
        updateStatsPanel(reportData);
      }
      showToast(`⏰ Deadline set — we'll remind you!`);
      closeDeadlineModal();
    }
  } catch (err) {
    showToast('Failed to save deadline.');
  }
}

// ── SERVER-SIDE NOTIFICATION POLLING ─────────────────────────────────────

function startNotificationPolling() {
  // Check every 5 minutes for overdue deadlines
  notificationInterval = setInterval(checkServerNotifications, 5 * 60 * 1000);
  // Also check immediately on load
  checkServerNotifications();
}

async function checkServerNotifications() {
  try {
    const res = await fetch('/notifications');
    const data = await res.json();

    if (data.notifications && data.notifications.length > 0) {
      data.notifications.forEach(n => {
        // Only show if not already dismissed (track in localStorage)
        const key = `notif_${n.report_id}_${n.feature}_dismissed`;
        if (!localStorage.getItem(key)) {
          showServerNotification(n);
        }
      });
    }
  } catch (err) {
    // Silent fail
  }
}

function showServerNotification(n) {
  const notif = document.createElement('div');
  notif.className = 'reminder-notif';
  notif.innerHTML = `
    <div class="reminder-notif-inner">
      <span>⏰</span>
      <div>
        <strong>Deadline passed ${n.hours_overdue}h ago!</strong>
        <p>"${n.feature}" — have you shipped it?</p>
      </div>
      <button onclick="dismissNotification('${n.report_id}', '${n.feature}', this)">✕</button>
    </div>
  `;
  document.body.appendChild(notif);
  setTimeout(() => { if (notif.parentElement) notif.remove(); }, 12000);
}

function dismissNotification(reportId, feature, btn) {
  const key = `notif_${reportId}_${feature}_dismissed`;
  localStorage.setItem(key, '1');
  btn.closest('.reminder-notif').remove();
}

function toggleMobileStats() {
  const btn = document.getElementById('mobileStatsToggle');
  const panel = document.getElementById('mobileStatsPanel');
  const isOpen = panel.classList.contains('open');

  if (isOpen) {
    panel.classList.remove('open');
    btn.classList.remove('open');
  } else {
    // Copy content from desktop stats panel
    panel.innerHTML = document.getElementById('statsPanel').innerHTML;
    panel.classList.add('open');
    btn.classList.add('open');
  }
}

function handleLogout() {
  Swal.fire({
    title: 'Logging out?',
    text: 'Your sessions are saved and will be here when you return.',
    icon: 'question',
    background: '#0d1425',
    color: '#f0f4ff',
    iconColor: '#4f9eff',
    showCancelButton: true,
    confirmButtonText: 'Yes, log out',
    cancelButtonText: 'Stay',
    confirmButtonColor: '#ef4444',
    cancelButtonColor: '#4f9eff',
    reverseButtons: true,
    borderRadius: '14px',
  }).then((result) => {
    if (result.isConfirmed) {
      pendo.clearSession();
      fetch('/logout', { method: 'POST' })
        .then(() => window.location.href = '/');
    }
  });
}
// ── SESSIONS ──────────────────────────────────────────────────────────────

async function loadSessions() {
  try {
    const res = await fetch('/sessions');
    const sessions = await res.json();
    const list = document.getElementById('sessionList');
    if (!sessions.length) {
      list.innerHTML = `<div style="padding:16px 8px;color:var(--text3);font-size:12px;font-family:var(--mono)">No past chats yet</div>`;
      return;
    }
    list.innerHTML = `<div class="session-group-label">Recent</div>` +
      sessions.map(s => {
        const mode = s.mode === 'researcher' ? 'researcher' : 'founder';
        const tag = mode === 'researcher' ? 'R' : 'F';
        const tagLabel = mode === 'researcher' ? 'Researcher mode' : 'Founder mode';
        return `
        <div class="session-item ${s.id === currentSessionId ? 'active' : ''}"
             onclick="loadSession('${s.id}')">
          <span class="session-mode-tag tag-${mode}" title="${tagLabel}">${tag}</span>
          <span class="session-title">${s.title || 'New Chat'}</span>
          <button class="session-delete-btn"
            onclick="event.stopPropagation(); deleteSession('${s.id}')"
            title="Delete">✕</button>
        </div>
      `;
      }).join('');
    
  } catch (err) {}
}

async function loadSession(id) {
  currentSessionId = id;
  try {
    const res = await fetch(`/sessions/${id}`);
    const data = await res.json();
    hideWelcome();
    document.getElementById('messages').innerHTML = '';
    data.messages.forEach(m => appendMessage(m.role, m.content));
    loadSessions();
    scrollToBottom();

    // Close sidebar + overlay on mobile
    document.getElementById('sidebar').classList.remove('open');
    document.getElementById('sidebarOverlay').classList.remove('visible');
  } catch (err) {}
}
async function deleteSession(id) {
  if (!confirm('Delete this chat? Cannot be undone.')) return;
  try {
    const res = await fetch(`/sessions/${id}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      if (currentSessionId === id) {
        currentSessionId = null;
        reportData = null;
        currentReportId = null;
        featureReportData = null;
        currentFeatureReportId = null;
        document.getElementById('messages').innerHTML = '';
        document.getElementById('welcome').style.display = 'flex';
        document.getElementById('reportBar').style.display = 'none';
        const panel = document.getElementById('statsPanel');
        panel.innerHTML = `
          <div class="stats-empty">
            <p>📊</p>
            <p>Stats appear after<br/>you generate a report</p>
          </div>`;
      }
      loadSessions();
      showToast('Chat deleted');
    }
  } catch (err) {
    showToast('Failed to delete.');
  }
}

document.getElementById('newChatBtn').addEventListener('click', async () => {
  try {
    const res = await fetch('/new_chat', { method: 'POST' });
    const data = await res.json();
    currentSessionId = data.session_id;
    reportData = null;
    currentReportId = null;
    featureReportData = null;
    currentFeatureReportId = null;
    currentMode = 'founder';
    updateModeUI('founder');
    if (featureAnalysisInterval) {
      clearInterval(featureAnalysisInterval);
      featureAnalysisInterval = null;
    }
    document.getElementById('messages').innerHTML = '';
    document.getElementById('welcome').style.display = 'flex';
    document.getElementById('reportBar').style.display = 'none';
    const panel = document.getElementById('statsPanel');
    panel.innerHTML = `
      <div class="stats-empty">
        <p>📊</p>
        <p>Stats appear after<br/>you generate a report</p>
      </div>`;
    loadSessions();
    document.getElementById('sidebar').classList.remove('open');
    document.getElementById('sidebarOverlay').classList.remove('visible');
  } catch (err) {
    showToast('Failed to create new chat.');
  }
});

// ── UI HELPERS ────────────────────────────────────────────────────────────

function appendMessage(role, content) {
  content = content
    .replace(/SHOW_REPORT_BUTTON/g, '')
    .replace(/GENERATE_REPORT_NOW/g, '')
    .replace(/PERSONA:\s*[\w\s]*/g, '')
    .trim();

  if (!content) return;

  const messages = document.getElementById('messages');
  const div = document.createElement('div');
  div.className = `message ${role}`;
  div.innerHTML = `
    <div class="msg-avatar">${role === 'user' ? 'you' : 'DS'}</div>
    <div class="msg-content">${formatMessage(content)}</div>
  `;
  messages.appendChild(div);
  scrollToBottom();
}

function formatMessage(content) {
  // Detect [OPTIONS: ...] and convert to tappable chips
  content = content.replace(/\[OPTIONS:\s*([^\]]+)\]/g, (match, opts) => {
    const chips = opts.split('|').map(o => o.trim());
    return `<div class="quick-reply-row inline">
      ${chips.map(c =>
        `<button class="quick-reply-chip" onclick="sendQuickReply('${c.replace(/'/g, "\\'")}')">${c}</button>`
      ).join('')}
    </div>`;
  });

  return content
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');
}

function sendQuickReply(text) {
  // Remove all existing chip rows
  document.querySelectorAll('.quick-reply-row').forEach(el => el.remove());
  // Put the text in input and send
  document.getElementById('userInput').value = text;
  sendMessage();
}

function showTyping() {
  const messages = document.getElementById('messages');
  const div = document.createElement('div');
  div.className = 'message assistant typing-wrapper';
  div.innerHTML = `
    <div class="msg-avatar">DS</div>
    <div class="msg-content typing">
      <span></span><span></span><span></span>
    </div>
  `;
  messages.appendChild(div);
  scrollToBottom();
  return div;
}

function removeTyping(el) { if (el) el.remove(); }

function hideWelcome() {
  const w = document.getElementById('welcome');
  if (w) w.style.display = 'none';
}

function showReportButton() {
  document.getElementById('reportBar').style.display = 'flex';
}

function scrollToBottom() {
  const area = document.getElementById('chatArea');
  area.scrollTop = area.scrollHeight;
}

function closeReport() {
  document.getElementById('reportModal').style.display = 'none';
}

function closePromptModal() {
  document.getElementById('promptModal').style.display = 'none';
}

function showUpgrade() {
  document.getElementById('upgradeModal').style.display = 'flex';
}

function closeUpgrade() {
  document.getElementById('upgradeModal').style.display = 'none';
}

function showToast(msg) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

async function copyReport() {
  if (!reportData) return;
  await navigator.clipboard.writeText(JSON.stringify(reportData, null, 2));
  showToast('Report copied');
}

async function shareReport() {
  if (!currentReportId) return;
  await navigator.clipboard.writeText(
    `${window.location.origin}/share/${currentReportId}`
  );
  showToast('Share link copied');
}

function downloadReport() {
  if (!currentReportId) return;
  window.open(`/report/${currentReportId}/download`, '_blank');
}

function copyBuildPrompt() {
  if (!reportData?.build_prompt) return;
  navigator.clipboard.writeText(reportData.build_prompt);
  showToast('✅ Build prompt copied!');
}

function slugify(str) {
  return str.toLowerCase().replace(/\s+/g, '-').replace(/[^\w-]/g, '');
}

function formatDate(d) {
  return new Date(d).toLocaleDateString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric'
  });
}

// ── MODAL CLOSE ON OVERLAY ────────────────────────────────────────────────
document.getElementById('featureReportModal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeFeatureReport();
});
document.getElementById('reportModal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeReport();
});
document.getElementById('upgradeModal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeUpgrade();
});
document.getElementById('deadlineModal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeDeadlineModal();
});
document.getElementById('promptModal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closePromptModal();
});