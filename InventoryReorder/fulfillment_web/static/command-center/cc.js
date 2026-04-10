/* Command Center — cc.js */

// ── SSE Real-Time Updates ────────────────────────────────────────────
let _ccSSE = null;
function ccConnectSSE() {
    if (_ccSSE) return;
    try {
        _ccSSE = new EventSource('/api/cc/events');
        _ccSSE.addEventListener('task_created', () => { ccFetchToday(); ccUpdateMascot(); });
        _ccSSE.addEventListener('task_completed', () => { ccFetchToday(); ccFetchActivity(); ccUpdateMascot(); });
        _ccSSE.addEventListener('task_updated', () => ccFetchToday());
        _ccSSE.addEventListener('blocker_resolved', () => ccFetchToday());
        _ccSSE.addEventListener('decision_answered', () => ccFetchDecisions());
        _ccSSE.onerror = () => {
            _ccSSE.close(); _ccSSE = null;
            setTimeout(ccConnectSSE, 5000); // Reconnect after 5s
        };
    } catch (e) { /* SSE unavailable — polling fallback */ }
}

let ccData = null;
let ccEnergyLevel = 'medium';
let ccActiveTaskId = null;
let ccTimerInterval = null;
let ccTimerSeconds = 0;
let ccBriefDismissed = false;
let ccSelectedTaskId = null;
let ccLightenedDay = false;
let ccOverwhelmDismissed = false;
let _ccLoaded = false;
const CC_WIP_LIMIT = 3;  // Max active tasks at once

/* ── Load ── */

function ccLoad() {
    if (_ccLoaded) return;
    _ccLoaded = true;

    ccAutoEnergy();
    ccSpawnRecurring();

    // Auto-build brief from live data, then fetch everything
    fetch('/api/cc/build-brief', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' })
        .then(() => ccFetchBrief())
        .catch(() => ccFetchBrief());

    ccFetchToday();
    ccFetchStreaks();
    ccFetchActivity();
    ccFetchRecurringGrid();
    ccCheckHealth();
    ccInjectSearchBar();
    ccConnectSSE();
    ccUpdateGreeting();

    // Auto-carry forward yesterday's incomplete tasks
    ccCarryForward();

    // Guided morning ritual (before 11am, first load of day)
    const hour = new Date().getHours();
    const ritualKey = 'cc_ritual_' + new Date().toISOString().slice(0, 10);
    if (hour < 11 && !sessionStorage.getItem(ritualKey)) {
        sessionStorage.setItem(ritualKey, '1');
        setTimeout(ccShowMorningRitual, 1500);
    }

    // Hide empty sidebar sections
    const deadlines = document.getElementById('cc-deadlines');
    if (deadlines && !deadlines.querySelector('.cc-deadline-item')) deadlines.style.display = 'none';

    // Auto-refresh every 5 minutes (re-check energy, re-fetch tasks)
    setInterval(() => {
        if (typeof currentView !== 'undefined' && currentView === 'commandcenter') {
            ccAutoEnergy();
            ccFetchToday();
        }
    }, 5 * 60 * 1000);
}

function ccAutoEnergy() {
    // After 3pm, auto-degrade energy if user hasn't manually set it today
    const hour = new Date().getHours();
    if (hour >= 15 && ccEnergyLevel === 'high') {
        ccEnergyLevel = 'medium';
        ccUpdateEnergyButtons();
    } else if (hour >= 17 && ccEnergyLevel === 'medium') {
        ccEnergyLevel = 'low';
        ccUpdateEnergyButtons();
    }
}

function ccUpdateEnergyButtons() {
    document.querySelectorAll('.cc-energy-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.level === ccEnergyLevel);
    });
}

async function ccFetchBrief() {
    try {
        const resp = await fetch('/api/cc/brief');
        const data = await resp.json();
        if (data.status === 'no brief today') return;
        ccRenderBrief(data);
    } catch (e) { /* silent */ }
}

function ccRenderBrief(data) {
    if (ccBriefDismissed) return;
    const brief = document.getElementById('cc-brief');
    const body = document.getElementById('cc-brief-body');
    if (!brief || !body) return;

    const items = [];
    if (data.orders_unfulfilled != null) items.push(`&#128230; ${data.orders_unfulfilled} unfulfilled orders`);
    if (data.gorgias_open != null) {
        let g = `&#127915; ${data.gorgias_open} open tickets`;
        if (data.gorgias_food_safety > 0) g += ` (${data.gorgias_food_safety} food safety)`;
        items.push(g);
    }
    if (data.inventory_alerts?.length > 0) {
        const alerts = data.inventory_alerts.map(a => `${a.sku}: ${a.runway_weeks}wk`).join(', ');
        items.push(`&#128200; Inventory alerts: ${alerts}`);
    }
    if (data.slack_unreads > 0) items.push(`&#128172; ${data.slack_unreads} Slack unreads`);
    if (data.gmail_unreads > 0) items.push(`&#128231; ${data.gmail_unreads} unread emails`);
    if (data.slack_trawl_created > 0) items.push(`&#9888; ${data.slack_trawl_created} new tasks from Slack promises`);

    if (items.length === 0) {
        items.push('Business looks stable. Nothing urgent.');
    }

    body.innerHTML = items.map(i => `<div class="cc-brief-item">${i}</div>`).join('');
    brief.style.display = '';
}

async function ccFetchStreaks() {
    try {
        const resp = await fetch('/api/cc/streaks');
        const streaks = await resp.json();
        ccRenderStreaks(streaks);
    } catch (e) { /* silent */ }
}

function ccRenderStreaks(streaks) {
    const el = document.getElementById('cc-streaks');
    if (!el) return;

    if (!streaks || streaks.length === 0) {
        el.style.display = 'none';
        return;
    }
    el.style.display = '';

    const top = streaks.slice(0, 5);
    el.innerHTML = `
        <div class="cc-sidebar-label">STREAKS</div>
        ${top.map(s => `
            <div style="display:flex;justify-content:space-between;align-items:center;padding:3px 0;font-family:'DM Sans',sans-serif;font-size:13px;color:#c0c8d4">
                <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px">${ccEsc(s.title)}</span>
                <span style="font-family:'Rajdhani',sans-serif;color:#f5a623;font-size:14px;white-space:nowrap">&#128293; ${s.weeks}w</span>
            </div>
        `).join('')}
    `;
}

function ccUpdateGreeting() {
    const el = document.getElementById('cc-greeting');
    if (!el) return;
    const hour = new Date().getHours();
    const dayNames = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const now = new Date();
    const greeting = hour < 6 ? 'Early bird.' : hour < 12 ? 'Good morning.' : hour < 17 ? 'Afternoon mode.' : hour < 21 ? 'Evening wind-down.' : 'Night owl mode.';
    const energy = ccEnergyLevel === 'high' ? ' You got this.' : ccEnergyLevel === 'low' ? ' Take it easy.' : '';
    el.textContent = `${greeting} ${dayNames[now.getDay()]}, ${months[now.getMonth()]} ${now.getDate()}${energy}`;
}

async function ccSpawnRecurring() {
    try {
        await fetch('/api/cc/recurring/spawn', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({energy_level: ccEnergyLevel})
        });
    } catch (e) { /* silent */ }
}

async function ccFetchToday() {
    try {
        const resp = await fetch(`/api/cc/today?energy=${ccEnergyLevel}`);
        ccData = await resp.json();
        ccRender();
    } catch (e) {
        console.error('CC fetch failed:', e);
    }
}

/* ── Render ── */

function ccRender() {
    if (!ccData) return;

    // Count total active work tasks
    const totalWork = (ccData.quick_wins?.length || 0) + (ccData.today?.length || 0) + (ccData.frog ? 1 : 0);
    const totalAll = totalWork + (ccData.personal?.length || 0) + (ccData.blocked?.length || 0) + (ccData.completed_today?.length || 0);

    // Empty state — no tasks at all
    if (totalAll === 0) {
        ccShowEmptyState();
        return;
    }
    // Hide empty state if it was showing
    const emptyEl = document.getElementById('cc-empty-state');
    if (emptyEl) emptyEl.style.display = 'none';

    // Overwhelm detection (12+ tasks) — show once per session
    if (totalWork >= 12 && !ccOverwhelmDismissed && !ccLightenedDay) {
        ccShowOverwhelmBanner(totalWork);
    }

    // Lightened day — if active, only show top 2 tasks + frog
    if (ccLightenedDay) {
        if (ccData.today) ccData.today = ccData.today.slice(0, 2);
        ccData.quick_wins = [];
    }

    // Brief
    if (!ccBriefDismissed) {
        const brief = document.getElementById('cc-brief');
        const briefBody = document.getElementById('cc-brief-body');
        if (brief && briefBody) {
            const blocked = ccData.blocked?.length || 0;
            const personal = ccData.personal?.length || 0;
            briefBody.innerHTML = `
                <div class="cc-brief-item">${totalWork} work tasks today${blocked > 0 ? ` &middot; ${blocked} waiting` : ''}${ccLightenedDay ? ' &middot; Lightened day active' : ''}</div>
                ${personal > 0 ? `<div class="cc-brief-item">${personal} personal items</div>` : ''}
                <div class="cc-brief-item">Energy: ${ccEnergyLevel}</div>
            `;
            brief.style.display = '';
        }
    }

    // Decisions queue
    ccFetchDecisions();

    // Quick wins
    ccRenderList('cc-quickwins-list', ccData.quick_wins || []);
    ccToggleSectionVisibility('cc-quickwins', ccData.quick_wins?.length);

    // Frog
    const frogList = document.getElementById('cc-frog-list');
    if (frogList) {
        frogList.innerHTML = ccData.frog ? ccRenderCard(ccData.frog, true) : '';
    }
    ccToggleSectionVisibility('cc-frog', ccData.frog);

    // Today
    ccRenderList('cc-today-list', ccData.today || []);
    ccToggleSectionVisibility('cc-today', ccData.today?.length);

    // Personal
    ccRenderList('cc-personal-list', ccData.personal || [], true);
    ccToggleSectionVisibility('cc-personal', ccData.personal?.length);

    // Blocked
    ccRenderList('cc-blocked-list', ccData.blocked || []);
    const blockedCount = document.getElementById('cc-blocked-count');
    if (blockedCount) blockedCount.textContent = ccData.blocked?.length || 0;

    // Completed
    ccRenderList('cc-completed-list', ccData.completed_today || []);
    const completedCount = document.getElementById('cc-completed-count');
    if (completedCount) completedCount.textContent = ccData.completed_today?.length || 0;

    // Progress + Mascot
    ccUpdateProgress();
    ccUpdateMascot();
}

function ccRenderList(containerId, tasks, isPersonal) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = tasks.map(t => ccRenderCard(t, false, isPersonal)).join('');
}

function _urgencyTier(score) {
    if (score >= 0.85) return 'critical';
    if (score >= 0.65) return 'high';
    if (score >= 0.45) return 'medium';
    return 'low';
}

function ccRenderCard(task, isFrog, isPersonal) {
    const classes = ['cc-task-card'];
    if (isFrog) classes.push('cc-task-frog');
    if (isPersonal) classes.push('cc-task-personal');
    if (task.status === 'blocked') classes.push('cc-task-blocked');
    if (task.status === 'done') classes.push('cc-task-done');
    if (ccSelectedTaskId === task.id) classes.push('cc-task-focused');
    if (task.urgency_score != null) classes.push('cc-urgency-' + _urgencyTier(task.urgency_score));

    const expanded = ccSelectedTaskId === task.id;
    const checklist = task.checklist || [];
    const doneCount = checklist.filter(c => c.done).length;
    const checklistPct = checklist.length > 0 ? Math.round((doneCount / checklist.length) * 100) : 0;

    const estStr = task.estimated_minutes ? `${task.estimated_minutes}min` : '';
    const sourceStr = task.source && task.source !== 'manual' ? task.source : '';

    let checklistHtml = '';
    if (expanded && checklist.length > 0) {
        checklistHtml = `
            <div class="cc-checklist">
                ${checklist.map(c => `
                    <div class="cc-checklist-item ${c.done ? 'done' : ''}" onclick="event.stopPropagation(); ccToggleCheck('${c.id}')">
                        <div class="cc-checklist-check">${c.done ? '&#10003;' : ''}</div>
                        <span>${ccEsc(c.title)}</span>
                    </div>
                `).join('')}
                <div class="cc-checklist-progress">
                    <div class="cc-checklist-progress-fill" style="width:${checklistPct}%"></div>
                </div>
            </div>
        `;
    }

    const checklistPreview = !expanded && checklist.length > 0
        ? `<span>${doneCount}/${checklist.length}</span>` : '';

    return `
        <div class="${classes.join(' ')}" role="article" aria-label="${ccEsc(task.title)}" onclick="ccSelectTask('${task.id}')">
            <div class="cc-task-title">${ccEsc(task.title)}</div>
            <div class="cc-task-meta">
                <span class="cc-task-priority ${task.priority || 'medium'}">${(task.priority || 'med').toUpperCase()}</span>
                ${estStr ? `<span class="cc-task-est">${estStr}</span>` : ''}
                ${checklistPreview}
                ${sourceStr ? `<span class="cc-task-source">${sourceStr}</span>` : ''}
            </div>
            ${checklistHtml}
            ${task.status === 'blocked' && task.blocker ? `
                <div class="cc-blocker-info">
                    <span>${task.blocker.type === 'person' ? 'Waiting on ' + ccEsc(task.blocker.who || '?') : ccEsc(task.blocker.note || 'Blocked')}${task.blocker.check_back_at ? ' &middot; check back ' + ccFormatDate(task.blocker.check_back_at) : ''}</span>
                    <button class="cc-blocker-resolve-btn" onclick="event.stopPropagation(); ccResolveBlocker('${task.blocker.id}')">Resolved</button>
                </div>
            ` : ''}
            <div class="cc-task-actions">
                ${task.status === 'active' ? `<button class="cc-task-action-btn cc-start-btn" onclick="event.stopPropagation(); ccStartTask('${task.id}')">Start</button>` : ''}
                ${task.status === 'active' ? `<button class="cc-task-action-btn" onclick="event.stopPropagation(); ccDoneTask('${task.id}')">Done</button>` : ''}
            </div>
        </div>
    `;
}

function ccToggleSectionVisibility(sectionId, hasItems) {
    const el = document.getElementById(sectionId);
    if (el) el.style.display = hasItems ? '' : 'none';
}

function ccShowEmptyState() {
    let el = document.getElementById('cc-empty-state');
    if (!el) {
        el = document.createElement('div');
        el.id = 'cc-empty-state';
        el.style.cssText = 'text-align:center;padding:60px 24px;color:#8a90a0;';
        el.innerHTML = `
            <div style="font-size:48px;margin-bottom:16px">&#127775;</div>
            <div style="font-size:20px;color:var(--cc-accent);margin-bottom:8px;font-family:'Rajdhani',sans-serif;font-weight:600;">All clear. Nice work.</div>
            <div style="margin-bottom:20px;max-width:400px;margin-left:auto;margin-right:auto;font-size:14px;line-height:1.6;">
                Nothing on the board. Add a task, set up recurring rhythms, or just enjoy the quiet.
            </div>
            <button class="cc-energy-btn" style="padding:8px 20px;font-size:14px" onclick="ccShowAddTask()">+ Add Task</button>
            <button class="cc-energy-btn" style="padding:8px 20px;font-size:14px;margin-left:8px" onclick="ccShowRecurring()">Set Up Recurring</button>
        `;
        const main = document.querySelector('.cc-main');
        if (main) main.insertBefore(el, main.querySelector('.cc-section'));
    }
    el.style.display = '';
    // Hide empty sections
    ['cc-quickwins', 'cc-frog', 'cc-today', 'cc-personal'].forEach(id => {
        const s = document.getElementById(id);
        if (s) s.style.display = 'none';
    });
}

function ccUpdateProgress() {
    const total = (ccData.quick_wins?.length || 0) + (ccData.today?.length || 0) + (ccData.frog ? 1 : 0);
    const done = ccData.completed_today?.length || 0;
    const all = total + done;
    const pct = all > 0 ? Math.round((done / all) * 100) : 0;

    const fill = document.getElementById('cc-progress-fill');
    const text = document.getElementById('cc-progress-text');
    if (fill) {
        fill.style.width = pct + '%';
        // Color shifts: red→amber→green as you complete more
        fill.style.background = pct < 30 ? 'var(--cc-amber)' : pct < 70 ? 'var(--cc-indigo)' : 'var(--cc-accent)';
        fill.style.transition = 'width 600ms cubic-bezier(0.25, 1, 0.5, 1), background 400ms';
    }
    if (text) {
        const emoji = pct === 0 ? '&#128064;' : pct < 50 ? '&#9889;' : pct < 100 ? '&#128170;' : '&#127942;';
        text.innerHTML = `${emoji} ${done} of ${all}${ccLightenedDay ? ' (light day)' : ''}`;
    }

    // Update energy mode in greeting
    const greetEl = document.getElementById('cc-greeting');
    if (greetEl) {
        const hour = new Date().getHours();
        const dayNames = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        const now = new Date();
        const greeting = hour < 12 ? 'Good morning.' : hour < 17 ? 'Good afternoon.' : 'Good evening.';
        const modeLabel = hour >= 15 ? '  Afternoon mode' : '';
        greetEl.textContent = `${greeting}  ${dayNames[now.getDay()]}, ${months[now.getMonth()]} ${now.getDate()}${modeLabel}`;
    }
}

/* ── Actions ── */

function ccSetEnergy(level) {
    ccEnergyLevel = level;
    ccUpdateEnergyButtons();
    ccFetchToday();
}

/* ── Overwhelm + Lightened Day + WIP ── */

function ccShowOverwhelmBanner(count) {
    const main = document.querySelector('.cc-main');
    if (!main) return;

    let banner = document.getElementById('cc-overwhelm-banner');
    if (banner) return; // Already showing

    banner = document.createElement('div');
    banner.id = 'cc-overwhelm-banner';
    banner.style.cssText = 'background:#1a2548;border:1px solid #f5a623;border-radius:8px;padding:14px;margin-bottom:16px;font-family:"DM Sans",sans-serif;font-size:14px;color:#c0c8d4;line-height:1.6';
    banner.innerHTML = `
        <div style="color:#f5a623;font-family:'Space Mono',monospace;font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">Heads up</div>
        <div>${count} tasks on the list today. That's a lot. Two options:</div>
        <div style="display:flex;gap:8px;margin-top:10px">
            <button onclick="ccLightenDay()" style="font-family:'Space Mono',monospace;font-size:11px;padding:6px 14px;border:1px solid #f5a623;border-radius:6px;background:#2a2a1e;color:#f5a623;cursor:pointer">Lighten my day (top 3 only)</button>
            <button onclick="ccDismissOverwhelm()" style="font-family:'Space Mono',monospace;font-size:11px;padding:6px 14px;border:1px solid #2a2a4a;border-radius:6px;background:transparent;color:#8892a0;cursor:pointer">I'm fine, keep going</button>
        </div>
    `;

    // Insert after brief or at top
    const brief = document.getElementById('cc-brief');
    if (brief && brief.style.display !== 'none') {
        brief.after(banner);
    } else {
        const header = document.querySelector('.cc-header');
        if (header) header.after(banner);
    }
}

function ccLightenDay() {
    ccLightenedDay = true;
    ccDismissOverwhelm();
    ccRender();
}

function ccDismissOverwhelm() {
    ccOverwhelmDismissed = true;
    const banner = document.getElementById('cc-overwhelm-banner');
    if (banner) banner.remove();
}

function ccCheckWipLimit() {
    // WIP limit: prevent starting more than CC_WIP_LIMIT tasks
    // Count tasks that are currently "in progress" (have been started but not done/blocked)
    if (!ccData) return true;

    // For now, we track WIP by counting active tasks the user has started this session
    // Since we only have one timer, WIP = 1 if timer is running, 0 if not
    // Future: track multiple in-progress tasks
    return !ccActiveTaskId; // Can start if nothing is running
}

function ccSelectTask(taskId) {
    ccSelectedTaskId = ccSelectedTaskId === taskId ? null : taskId;
    ccRender();
}

function ccToggleSection(sectionId) {
    const el = document.getElementById(sectionId);
    if (el) el.classList.toggle('cc-section-collapsed');
}

function ccDismissBrief() {
    ccBriefDismissed = true;
    const el = document.getElementById('cc-brief');
    if (el) el.style.display = 'none';
}

async function ccAddTask() {
    const input = document.getElementById('cc-add-input');
    const typeSelect = document.getElementById('cc-add-type');
    const title = input.value.trim();
    if (!title) return;

    await fetch('/api/cc/tasks', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({title, type: typeSelect.value})
    });

    input.value = '';
    ccFetchToday();
}

async function ccDoneTask(taskId) {
    await fetch(`/api/cc/tasks/${taskId}`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({status: 'done'})
    });
    if (ccActiveTaskId === taskId) ccStopTimer();
    ccCheckBreakReminder();
    ccFetchToday();
}

async function ccToggleCheck(itemId) {
    await fetch(`/api/cc/tasks/_/checklist/${itemId}/toggle`, {method: 'POST'});
    ccFetchToday();
}

/* ── Timer ── */

function ccStartTask(taskId) {
    if (ccActiveTaskId) ccStopTimer();
    ccActiveTaskId = taskId;
    ccTimerSeconds = 0;

    const task = ccFindTask(taskId);
    if (!task) return;

    const activeEl = document.getElementById('cc-active-task');
    if (!activeEl) return;

    const checklist = task.checklist || [];

    const doneCount = checklist.filter(c => c.done).length;
    const checkPct = checklist.length > 0 ? Math.round((doneCount / checklist.length) * 100) : 0;

    activeEl.innerHTML = `
        <div class="cc-active-title">${ccEsc(task.title)}</div>
        <div class="cc-timer">
            <div class="cc-timer-display" id="cc-timer-display">00:00</div>
            <div class="cc-timer-bar">
                <div class="cc-timer-fill" id="cc-timer-fill" style="width:0%"></div>
            </div>
        </div>
        ${checklist.length > 0 ? `
            <div class="cc-checklist" id="cc-active-checklist">
                ${checklist.map(c => `
                    <div class="cc-checklist-item ${c.done ? 'done' : ''}" data-id="${c.id}" onclick="ccToggleActiveCheck('${c.id}')">
                        <div class="cc-checklist-check">${c.done ? '&#10003;' : ''}</div>
                        <span>${ccEsc(c.title)}</span>
                    </div>
                `).join('')}
                <div class="cc-checklist-progress">
                    <div class="cc-checklist-progress-fill" style="width:${checkPct}%"></div>
                </div>
            </div>
        ` : ''}
        <div class="cc-active-actions">
            <button class="cc-done-btn" onclick="ccDoneTask('${taskId}')">Done</button>
            <button class="cc-blocked-btn" onclick="ccBlockTask('${taskId}')">Blocked</button>
            <button onclick="ccStopTimer()">Pause</button>
            <button onclick="ccExtendTimer(10)">+10 min</button>
            <button onclick="ccSkipTask('${taskId}')">Skip</button>
        </div>
    `;
    activeEl.style.display = '';

    ccTimerInterval = setInterval(() => {
        ccTimerSeconds++;
        const min = Math.floor(ccTimerSeconds / 60);
        const sec = ccTimerSeconds % 60;
        const display = document.getElementById('cc-timer-display');
        if (display) display.textContent = `${String(min).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;

        const estMin = task.estimated_minutes || 25;
        const pct = Math.min(100, Math.round((ccTimerSeconds / (estMin * 60)) * 100));
        const fill = document.getElementById('cc-timer-fill');
        if (fill) {
            fill.style.width = pct + '%';
            fill.style.background = pct > 80 ? '#f5a623' : '#4ecca3';
        }
    }, 1000);
}

function ccStopTimer() {
    if (ccTimerInterval) {
        clearInterval(ccTimerInterval);
        ccTimerInterval = null;
    }

    if (ccActiveTaskId && ccTimerSeconds > 0) {
        fetch(`/api/cc/tasks/${ccActiveTaskId}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({actual_minutes: Math.ceil(ccTimerSeconds / 60)})
        });
    }

    ccActiveTaskId = null;
    ccTimerSeconds = 0;
    const activeEl = document.getElementById('cc-active-task');
    if (activeEl) activeEl.style.display = 'none';
}

function ccBlockTask(taskId) {
    // Show inline blocker form instead of browser prompts
    ccShowBlockerForm(taskId);
}

let ccBlockerFormTaskId = null;
let ccBlockerFormType = 'person';

function ccShowBlockerForm(taskId) {
    ccBlockerFormTaskId = taskId;
    ccBlockerFormType = 'person';

    // Insert form into the active task area or below the task card
    const container = document.getElementById('cc-active-task');
    if (!container || container.style.display === 'none') {
        // Not in active view — show a floating form
        const main = document.querySelector('.cc-main');
        if (!main) return;
        let formEl = document.getElementById('cc-blocker-form-float');
        if (!formEl) {
            formEl = document.createElement('div');
            formEl.id = 'cc-blocker-form-float';
            main.prepend(formEl);
        }
        formEl.innerHTML = ccBlockerFormHtml(taskId);
        formEl.scrollIntoView({behavior: 'smooth'});
    } else {
        // In active view — append to it
        let formEl = document.getElementById('cc-blocker-form-inline');
        if (!formEl) {
            formEl = document.createElement('div');
            formEl.id = 'cc-blocker-form-inline';
            container.appendChild(formEl);
        }
        formEl.innerHTML = ccBlockerFormHtml(taskId);
    }
}

function ccBlockerFormHtml(taskId) {
    return `
        <div class="cc-blocker-form">
            <div class="cc-blocker-title">What's blocking you?</div>
            <div class="cc-blocker-types">
                <button class="cc-blocker-type-btn active" data-type="person" onclick="ccSetBlockerType(this, 'person')">Waiting on someone</button>
                <button class="cc-blocker-type-btn" data-type="data" onclick="ccSetBlockerType(this, 'data')">Need data/file</button>
                <button class="cc-blocker-type-btn" data-type="unknown" onclick="ccSetBlockerType(this, 'unknown')">I don't know how</button>
                <button class="cc-blocker-type-btn" data-type="toobig" onclick="ccSetBlockerType(this, 'toobig')">Too big / overwhelmed</button>
            </div>
            <div class="cc-blocker-fields">
                <div class="cc-blocker-row" id="cc-blocker-who-row">
                    <input type="text" id="cc-blocker-who" placeholder="Who? (Tommy, Anik, RMFG...)" />
                </div>
                <input type="text" id="cc-blocker-note" placeholder="Brief note (optional)" />
                <div class="cc-blocker-row">
                    <select id="cc-blocker-checkback">
                        <option value="">Check back...</option>
                        <option value="2h">In 2 hours</option>
                        <option value="tomorrow">Tomorrow</option>
                        <option value="2d">In 2 days</option>
                        <option value="1w">In 1 week</option>
                        <option value="monitor">Auto-monitor (Slack/Gmail)</option>
                    </select>
                </div>
            </div>
            <div class="cc-blocker-actions">
                <button class="cc-blocker-submit" onclick="ccSubmitBlocker('${taskId}')">Set Blocker</button>
                <button class="cc-blocker-cancel" onclick="ccCancelBlockerForm()">Cancel</button>
            </div>
        </div>
    `;
}

function ccSetBlockerType(btn, type) {
    ccBlockerFormType = type;
    document.querySelectorAll('.cc-blocker-type-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    const whoRow = document.getElementById('cc-blocker-who-row');
    if (whoRow) {
        whoRow.style.display = (type === 'person') ? '' : 'none';
    }

    // For "too big" — change note placeholder
    const noteEl = document.getElementById('cc-blocker-note');
    if (noteEl) {
        if (type === 'toobig') {
            noteEl.placeholder = "What's the ONE next tiny action you can do?";
        } else if (type === 'unknown') {
            noteEl.placeholder = "What do you need to figure out?";
        } else {
            noteEl.placeholder = "Brief note (optional)";
        }
    }
}

async function ccSubmitBlocker(taskId) {
    const who = document.getElementById('cc-blocker-who')?.value || null;
    const note = document.getElementById('cc-blocker-note')?.value || '';
    const checkback = document.getElementById('cc-blocker-checkback')?.value || '';

    let checkBackAt = null;
    const now = new Date();
    if (checkback === '2h') {
        checkBackAt = new Date(now.getTime() + 2 * 60 * 60 * 1000).toISOString();
    } else if (checkback === 'tomorrow') {
        const tomorrow = new Date(now);
        tomorrow.setDate(tomorrow.getDate() + 1);
        tomorrow.setHours(9, 0, 0, 0);
        checkBackAt = tomorrow.toISOString();
    } else if (checkback === '2d') {
        checkBackAt = new Date(now.getTime() + 2 * 24 * 60 * 60 * 1000).toISOString();
    } else if (checkback === '1w') {
        checkBackAt = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000).toISOString();
    }

    let type = ccBlockerFormType;
    let monitorSource = 'none';

    if (type === 'toobig') {
        type = 'unknown'; // Store as unknown in DB, the note captures the real intent
    }
    if (type === 'person' && checkback === 'monitor') {
        monitorSource = 'slack';
    }

    await fetch('/api/cc/blockers', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            task_id: taskId,
            type: type,
            who: who,
            note: note,
            monitor_source: monitorSource,
            monitor_query: who || '',
            check_back_at: checkBackAt
        })
    });

    ccCancelBlockerForm();
    ccStopTimer();
    ccFetchToday();
}

function ccCancelBlockerForm() {
    const float = document.getElementById('cc-blocker-form-float');
    if (float) float.innerHTML = '';
    const inline = document.getElementById('cc-blocker-form-inline');
    if (inline) inline.innerHTML = '';
    ccBlockerFormTaskId = null;
}

async function ccResolveBlocker(blockerId) {
    await fetch(`/api/cc/blockers/${blockerId}/resolve`, {method: 'POST'});
    ccFetchToday();
}

async function ccSkipTask(taskId) {
    ccStopTimer();
    try {
        await fetch(`/api/cc/tasks/${taskId}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({notes: 'Skipped — deferred to later'})
        });
    } catch (e) {
        console.error('Skip failed:', e);
    }
    ccFetchToday();
}

async function ccExtendTimer(minutes) {
    const task = ccFindTask(ccActiveTaskId);
    if (task) {
        const newEstimate = (task.estimated_minutes || 25) + minutes;
        task.estimated_minutes = newEstimate;
        try {
            await fetch(`/api/cc/tasks/${ccActiveTaskId}`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({estimated_minutes: newEstimate})
            });
        } catch (e) {
            console.error('Extend timer persist failed:', e);
        }
    }
}

/* ── Auto-Pause on Window Unfocus ── */

let ccUnfocusTimeout = null;
let ccTimerPaused = false;

document.addEventListener('visibilitychange', () => {
    if (!ccActiveTaskId || !ccTimerInterval) return;

    if (document.hidden) {
        // Start 3-minute countdown to auto-pause
        ccUnfocusTimeout = setTimeout(() => {
            if (ccTimerInterval) {
                clearInterval(ccTimerInterval);
                ccTimerInterval = null;
                ccTimerPaused = true;
                const display = document.getElementById('cc-timer-display');
                if (display) display.style.opacity = '0.5';
            }
        }, 3 * 60 * 1000);
    } else {
        // Came back
        if (ccUnfocusTimeout) {
            clearTimeout(ccUnfocusTimeout);
            ccUnfocusTimeout = null;
        }
        if (ccTimerPaused && ccActiveTaskId) {
            // Resume timer
            ccTimerPaused = false;
            const display = document.getElementById('cc-timer-display');
            if (display) display.style.opacity = '1';
            const task = ccFindTask(ccActiveTaskId);
            ccTimerInterval = setInterval(() => {
                ccTimerSeconds++;
                const min = Math.floor(ccTimerSeconds / 60);
                const sec = ccTimerSeconds % 60;
                if (display) display.textContent = `${String(min).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
                const estMin = (task?.estimated_minutes) || 25;
                const pct = Math.min(100, Math.round((ccTimerSeconds / (estMin * 60)) * 100));
                const fill = document.getElementById('cc-timer-fill');
                if (fill) {
                    fill.style.width = pct + '%';
                    fill.style.background = pct > 80 ? '#f5a623' : '#4ecca3';
                }
            }, 1000);
        }
    }
});

/* ── Live Checklist in Active Task ── */

async function ccToggleActiveCheck(itemId) {
    await fetch(`/api/cc/tasks/_/checklist/${itemId}/toggle`, {method: 'POST'});

    // Animate the item
    const items = document.querySelectorAll('#cc-active-checklist .cc-checklist-item');
    items.forEach(el => {
        if (el.getAttribute('data-id') === itemId) {
            el.classList.add('cc-check-spring');
            setTimeout(() => el.classList.remove('cc-check-spring'), 300);
        }
    });

    // Refresh active task checklist without full reload
    const resp = await fetch(`/api/cc/tasks/${ccActiveTaskId}`);
    const task = await resp.json();
    const checklistEl = document.getElementById('cc-active-checklist');
    if (checklistEl && task.checklist) {
        const doneCount = task.checklist.filter(c => c.done).length;
        const total = task.checklist.length;
        checklistEl.innerHTML = task.checklist.map(c => `
            <div class="cc-checklist-item ${c.done ? 'done' : ''}" data-id="${c.id}" onclick="ccToggleActiveCheck('${c.id}')">
                <div class="cc-checklist-check">${c.done ? '&#10003;' : ''}</div>
                <span>${ccEsc(c.title)}</span>
            </div>
        `).join('') + `
            <div class="cc-checklist-progress">
                <div class="cc-checklist-progress-fill" style="width:${total > 0 ? Math.round((doneCount/total)*100) : 0}%"></div>
            </div>
        `;

        // If all done, pulse the Done button
        if (doneCount === total && total > 0) {
            const doneBtn = document.querySelector('.cc-done-btn');
            if (doneBtn) {
                doneBtn.classList.add('cc-pulse');
                setTimeout(() => doneBtn.classList.remove('cc-pulse'), 600);
            }
        }
    }
}

/* ── Chat (connected to Claude API) ── */

let ccChatModel = 'claude-haiku-4-5-20251001';
let ccChatLoading = false;

async function ccSendChat() {
    const input = document.getElementById('cc-chat-input');
    const msg = input.value.trim();
    if (!msg || ccChatLoading) return;

    const messages = document.getElementById('cc-chat-messages');
    messages.innerHTML += `<div class="cc-chat-user">${ccEsc(msg)}</div>`;
    input.value = '';
    ccChatLoading = true;

    // Show typing indicator
    const typingId = 'cc-typing-' + Date.now();
    messages.innerHTML += `<div class="cc-chat-assistant" id="${typingId}" style="color:#5a6070">Thinking...</div>`;
    messages.scrollTop = messages.scrollHeight;

    try {
        const resp = await fetch('/api/cc/chat', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                message: msg,
                model: ccChatModel,
                energy_level: ccEnergyLevel
            })
        });
        const data = await resp.json();

        // Remove typing indicator
        const typingEl = document.getElementById(typingId);
        if (typingEl) typingEl.remove();

        // Show response
        const responseHtml = ccFormatChatResponse(data.response || 'No response');
        const modelLabel = (data.model || ccChatModel).split('-').pop();
        const budgetInfo = data.budget ? ` · $${(data.budget.spent_cents/100).toFixed(2)}/$${(data.budget.limit_cents/100).toFixed(2)}` : '';

        messages.innerHTML += `
            <div class="cc-chat-assistant">
                ${responseHtml}
                <div style="font-size:10px;color:#5a6070;margin-top:6px;font-family:'Space Mono',monospace">
                    ${modelLabel}${data.tokens ? ` · ${data.tokens.input + data.tokens.output} tokens` : ''}${budgetInfo}
                </div>
            </div>
        `;

        // Action buttons for certain responses
        if (data.response && (data.response.includes('Subject:') || data.response.includes('Hi ') || data.response.includes('Dear '))) {
            messages.innerHTML += `
                <div style="display:flex;gap:6px;padding:4px 0">
                    <button onclick="ccChatAction('copy')" style="font-size:10px;padding:3px 8px;border:1px solid #2a2a4a;border-radius:4px;background:transparent;color:#8892a0;cursor:pointer;font-family:'Space Mono',monospace">Copy</button>
                    <button onclick="ccChatAction('task')" style="font-size:10px;padding:3px 8px;border:1px solid #2a2a4a;border-radius:4px;background:transparent;color:#8892a0;cursor:pointer;font-family:'Space Mono',monospace">Add to tasks</button>
                </div>
            `;
        }
    } catch (e) {
        const typingEl = document.getElementById(typingId);
        if (typingEl) typingEl.textContent = 'Connection error. Check if API key is configured.';
    }

    ccChatLoading = false;
    messages.scrollTop = messages.scrollHeight;
}

function ccFormatChatResponse(text) {
    // Basic markdown-like formatting
    return ccEsc(text)
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>')
        .replace(/`(.*?)`/g, '<code style="background:#2a2a4a;padding:1px 4px;border-radius:3px;font-family:\'Rajdhani\',monospace;font-size:12px">$1</code>');
}

function ccChatAction(action) {
    if (action === 'copy') {
        const msgs = document.querySelectorAll('.cc-chat-assistant');
        const last = msgs[msgs.length - 1];
        if (last) navigator.clipboard.writeText(last.textContent);
    } else if (action === 'task') {
        const msgs = document.querySelectorAll('.cc-chat-assistant');
        const last = msgs[msgs.length - 1];
        if (last) {
            const title = 'Follow up: ' + last.textContent.substring(0, 60);
            fetch('/api/cc/tasks', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({title, type: 'work', source: 'chat'})
            }).then(() => ccFetchToday());
        }
    }
}

function ccSetChatModel(model) {
    ccChatModel = model;
}

/* ── End of Day + Weekly Review ── */

async function ccShowEOD() {
    try {
        const resp = await fetch('/api/cc/eod');
        const data = await resp.json();
        ccRenderEOD(data);
    } catch (e) {
        console.error('EOD fetch failed:', e);
    }
}

function ccRenderEOD(data) {
    const main = document.querySelector('.cc-main');
    if (!main) return;

    const completedHtml = data.completed.map(t =>
        `<div style="padding:3px 0;color:#c0c8d4">&#10003; ${ccEsc(t.title)}${t.actual_minutes ? ` <span style="color:#5a6070">(${t.actual_minutes}min)</span>` : ''}</div>`
    ).join('') || '<div style="color:#5a6070">No tasks completed yet</div>';

    const carryHtml = data.carrying_forward.map(t =>
        `<div style="padding:3px 0;color:#c0c8d4">&rarr; ${ccEsc(t.title)}</div>`
    ).join('') || '<div style="color:#5a6070">Nothing carrying forward</div>';

    const blockerHtml = data.open_blockers.map(b =>
        `<div style="padding:3px 0;color:#f5a623">&bull; ${ccEsc(b.title)} &mdash; ${ccEsc(b.who || b.note || b.type)}</div>`
    ).join('') || '<div style="color:#5a6070">No open blockers</div>';

    const tomorrowHtml = data.tomorrow_preview.map(t =>
        `<div style="padding:3px 0;color:#c0c8d4">&rarr; ${ccEsc(t.title)}</div>`
    ).join('') || '<div style="color:#5a6070">No recurring tasks tomorrow</div>';

    const hours = Math.floor(data.minutes_tracked / 60);
    const mins = data.minutes_tracked % 60;
    const timeStr = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;

    // Insert EOD card at top of main
    let eodEl = document.getElementById('cc-eod-card');
    if (!eodEl) {
        eodEl = document.createElement('div');
        eodEl.id = 'cc-eod-card';
        main.prepend(eodEl);
    }

    eodEl.innerHTML = `
        <div style="background:#16213e;border:1px solid #4ecca3;border-radius:10px;padding:16px;margin-bottom:16px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <div style="font-family:'Space Mono',monospace;font-size:11px;color:#4ecca3;text-transform:uppercase;letter-spacing:1.5px">Wrap Up &middot; ${data.day_of_week}</div>
                <button onclick="document.getElementById('cc-eod-card').remove()" style="background:none;border:none;color:#5a6070;font-size:16px;cursor:pointer">&times;</button>
            </div>

            <div style="margin-bottom:12px">
                <div style="font-family:'Space Mono',monospace;font-size:10px;color:#8892a0;text-transform:uppercase;margin-bottom:6px">Done today (${data.completed_count})</div>
                ${completedHtml}
            </div>

            <div style="margin-bottom:12px">
                <div style="font-family:'Space Mono',monospace;font-size:10px;color:#8892a0;text-transform:uppercase;margin-bottom:6px">Moving to tomorrow (${data.carrying_count})</div>
                ${carryHtml}
            </div>

            ${data.blocker_count > 0 ? `
                <div style="margin-bottom:12px">
                    <div style="font-family:'Space Mono',monospace;font-size:10px;color:#8892a0;text-transform:uppercase;margin-bottom:6px">Open blockers (${data.blocker_count})</div>
                    ${blockerHtml}
                </div>
            ` : ''}

            <div style="margin-bottom:12px">
                <div style="font-family:'Space Mono',monospace;font-size:10px;color:#8892a0;text-transform:uppercase;margin-bottom:6px">Tomorrow</div>
                ${tomorrowHtml}
            </div>

            <div style="border-top:1px solid #2a2a4a;padding-top:10px;font-family:'DM Sans',sans-serif;font-size:13px;color:#8892a0">
                ${data.completed_count} tasks &middot; ${timeStr} tracked
            </div>
        </div>
    `;

    eodEl.scrollIntoView({behavior: 'smooth'});
}

async function ccShowWeeklyReview() {
    try {
        const resp = await fetch('/api/cc/weekly-review');
        const data = await resp.json();
        ccRenderWeeklyReview(data);
    } catch (e) {
        console.error('Weekly review fetch failed:', e);
    }
}

function ccRenderWeeklyReview(data) {
    const main = document.querySelector('.cc-main');
    if (!main) return;

    const hours = Math.floor(data.total_actual_minutes / 60);
    const mins = data.total_actual_minutes % 60;
    const timeStr = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;

    const estHours = Math.floor(data.total_estimated_minutes / 60);
    const estMins = data.total_estimated_minutes % 60;
    const estStr = estHours > 0 ? `${estHours}h ${estMins}m` : `${estMins}m`;

    const fasterMsg = data.faster_than_estimated
        ? `<div style="color:#4ecca3;margin-top:6px">You were ${data.time_saved_minutes}min faster than estimated. You're faster than you think.</div>`
        : '';

    const sourceHtml = Object.entries(data.by_source).map(([src, count]) =>
        `<span style="color:#c0c8d4">${src}: ${count}</span>`
    ).join(' &middot; ');

    const waitingHtml = data.waiting_on.map(w =>
        `<div style="padding:3px 0;display:flex;justify-content:space-between;color:#f5a623">
            <span>${ccEsc(w.title)}</span>
            <span style="color:#5a6070;font-size:12px">${ccEsc(w.who || w.note || '')}</span>
        </div>`
    ).join('') || '<div style="color:#5a6070">Nothing waiting</div>';

    const streakHtml = data.streaks.slice(0, 5).map(s =>
        `<div style="display:flex;justify-content:space-between;padding:2px 0;color:#c0c8d4">
            <span>${ccEsc(s.title)}</span>
            <span style="color:#f5a623">&#128293; ${s.weeks}w</span>
        </div>`
    ).join('') || '<div style="color:#5a6070">No streaks yet</div>';

    let reviewEl = document.getElementById('cc-weekly-review');
    if (!reviewEl) {
        reviewEl = document.createElement('div');
        reviewEl.id = 'cc-weekly-review';
        main.prepend(reviewEl);
    }

    reviewEl.innerHTML = `
        <div style="background:#16213e;border:1px solid #6366f1;border-radius:10px;padding:16px;margin-bottom:16px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <div style="font-family:'Space Mono',monospace;font-size:11px;color:#6366f1;text-transform:uppercase;letter-spacing:1.5px">Weekly Review &middot; ${data.week_start}</div>
                <button onclick="document.getElementById('cc-weekly-review').remove()" style="background:none;border:none;color:#5a6070;font-size:16px;cursor:pointer">&times;</button>
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:14px">
                <div style="background:#1a2548;border-radius:6px;padding:10px;text-align:center">
                    <div style="font-family:'Rajdhani',sans-serif;font-size:24px;color:#4ecca3">${data.completed_count}</div>
                    <div style="font-family:'Space Mono',monospace;font-size:10px;color:#8892a0">COMPLETED</div>
                </div>
                <div style="background:#1a2548;border-radius:6px;padding:10px;text-align:center">
                    <div style="font-family:'Rajdhani',sans-serif;font-size:24px;color:#eaeaea">${timeStr}</div>
                    <div style="font-family:'Space Mono',monospace;font-size:10px;color:#8892a0">TRACKED</div>
                </div>
                <div style="background:#1a2548;border-radius:6px;padding:10px;text-align:center">
                    <div style="font-family:'Rajdhani',sans-serif;font-size:24px;color:#eaeaea">${data.blockers_resolved}/${data.blockers_created}</div>
                    <div style="font-family:'Space Mono',monospace;font-size:10px;color:#8892a0">BLOCKERS</div>
                </div>
            </div>

            ${fasterMsg}

            <div style="margin:12px 0">
                <div style="font-family:'Space Mono',monospace;font-size:10px;color:#8892a0;text-transform:uppercase;margin-bottom:6px">Task sources</div>
                <div style="font-family:'DM Sans',sans-serif;font-size:13px">${sourceHtml || 'No data'}</div>
            </div>

            <div style="margin:12px 0">
                <div style="font-family:'Space Mono',monospace;font-size:10px;color:#8892a0;text-transform:uppercase;margin-bottom:6px">Still waiting on</div>
                ${waitingHtml}
            </div>

            <div style="margin:12px 0">
                <div style="font-family:'Space Mono',monospace;font-size:10px;color:#8892a0;text-transform:uppercase;margin-bottom:6px">Streaks</div>
                ${streakHtml}
            </div>
        </div>
    `;

    reviewEl.scrollIntoView({behavior: 'smooth'});
}

/* ── Helpers ── */

function ccFindTask(taskId) {
    if (!ccData) return null;
    const all = [
        ...(ccData.quick_wins || []),
        ...(ccData.today || []),
        ...(ccData.personal || []),
        ...(ccData.blocked || []),
        ...(ccData.completed_today || []),
    ];
    if (ccData.frog) all.push(ccData.frog);
    return all.find(t => t.id === taskId) || null;
}

function ccEsc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function ccFormatDate(isoStr) {
    if (!isoStr) return '';
    try {
        const d = new Date(isoStr);
        const now = new Date();
        const diffH = Math.round((d - now) / (1000 * 60 * 60));
        if (diffH <= 0) return 'now';
        if (diffH < 24) return `in ${diffH}h`;
        const diffD = Math.round(diffH / 24);
        if (diffD === 1) return 'tomorrow';
        return `in ${diffD}d`;
    } catch (e) {
        return '';
    }
}

/* ── Bad Day Protocol — Triage Carryovers ── */

async function ccBadDayTriage() {
    try {
        const resp = await fetch('/api/cc/carryovers');
        const tasks = await resp.json();
        if (tasks.length === 0) {
            alert('No carried-forward tasks. All clear!');
            return;
        }
        ccShowTriageFlow(tasks, 0);
    } catch (e) {
        console.error('Triage fetch failed:', e);
    }
}

function ccShowTriageFlow(tasks, index) {
    if (index >= tasks.length) {
        // Done triaging
        const main = document.querySelector('.cc-main');
        const triageEl = document.getElementById('cc-triage-card');
        if (triageEl) triageEl.remove();
        ccFetchToday();
        return;
    }

    const task = tasks[index];
    const main = document.querySelector('.cc-main');
    if (!main) return;

    let triageEl = document.getElementById('cc-triage-card');
    if (!triageEl) {
        triageEl = document.createElement('div');
        triageEl.id = 'cc-triage-card';
        main.prepend(triageEl);
    }

    const remaining = tasks.length - index;
    const ageDays = task.age_days || 0;

    triageEl.innerHTML = `
        <div style="background:#16213e;border:1px solid #f5a623;border-radius:10px;padding:16px;margin-bottom:16px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
                <div style="font-family:'Space Mono',monospace;font-size:11px;color:#f5a623;text-transform:uppercase;letter-spacing:1px">
                    Triage &middot; ${remaining} remaining
                </div>
                <button onclick="document.getElementById('cc-triage-card').remove()" style="background:none;border:none;color:#5a6070;font-size:16px;cursor:pointer">&times;</button>
            </div>

            <div style="font-family:'DM Sans',sans-serif;font-size:15px;color:#eaeaea;margin-bottom:4px">${ccEsc(task.title)}</div>
            <div style="font-family:'Space Mono',monospace;font-size:11px;color:#5a6070;margin-bottom:12px">
                Added ${ageDays} day${ageDays !== 1 ? 's' : ''} ago
                ${task.source !== 'manual' ? ' &middot; from ' + task.source : ''}
            </div>

            <div style="display:flex;gap:8px;flex-wrap:wrap">
                <button onclick="ccTriageAction('${task.id}', 'keep', ${JSON.stringify(tasks).replace(/'/g, "\\'")})" style="font-family:'Space Mono',monospace;font-size:11px;padding:8px 16px;border:1px solid #4ecca3;border-radius:6px;background:transparent;color:#4ecca3;cursor:pointer">Still need to do it</button>
                <button onclick="ccTriageAction('${task.id}', 'done', ${JSON.stringify(tasks).replace(/'/g, "\\'")})" style="font-family:'Space Mono',monospace;font-size:11px;padding:8px 16px;border:1px solid #6366f1;border-radius:6px;background:transparent;color:#6366f1;cursor:pointer">It handled itself</button>
                <button onclick="ccTriageAction('${task.id}', 'archive', ${JSON.stringify(tasks).replace(/'/g, "\\'")})" style="font-family:'Space Mono',monospace;font-size:11px;padding:8px 16px;border:1px solid #5a6070;border-radius:6px;background:transparent;color:#5a6070;cursor:pointer">Not important</button>
            </div>
        </div>
    `;

    triageEl.scrollIntoView({behavior: 'smooth'});
}

async function ccTriageAction(taskId, action, tasks) {
    await fetch(`/api/cc/triage/${taskId}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action})
    });

    const index = tasks.findIndex(t => t.id === taskId);
    ccShowTriageFlow(tasks, index + 1);
}

/* ── Break Reminder ── */

let ccTasksCompletedSinceBreak = 0;

function ccCheckBreakReminder() {
    ccTasksCompletedSinceBreak++;
    if (ccTasksCompletedSinceBreak >= 3) {
        ccTasksCompletedSinceBreak = 0;
        const chat = document.getElementById('cc-chat-messages');
        if (chat) {
            chat.innerHTML += `<div class="cc-chat-system">You've done 3 tasks in a row. Good time for a 5-minute break.</div>`;
            chat.scrollTop = chat.scrollHeight;
        }
    }
}

/* ── Keyboard Nav ── */

let ccFocusIndex = -1;

function ccGetAllTaskIds() {
    if (!ccData) return [];
    const ids = [];
    (ccData.quick_wins || []).forEach(t => ids.push(t.id));
    if (ccData.frog) ids.push(ccData.frog.id);
    (ccData.today || []).forEach(t => ids.push(t.id));
    (ccData.personal || []).forEach(t => ids.push(t.id));
    return ids;
}

function ccNavigate(direction) {
    const ids = ccGetAllTaskIds();
    if (ids.length === 0) return;

    ccFocusIndex += direction;
    if (ccFocusIndex < 0) ccFocusIndex = 0;
    if (ccFocusIndex >= ids.length) ccFocusIndex = ids.length - 1;

    ccSelectedTaskId = ids[ccFocusIndex];
    ccRender();

    // Scroll the focused card into view
    const card = document.querySelector(`.cc-task-card[onclick*="${ccSelectedTaskId}"]`);
    if (card) card.scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

document.addEventListener('keydown', (e) => {
    if (typeof currentView !== 'undefined' && currentView !== 'commandcenter') return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

    if (e.key === 'j') {
        e.preventDefault();
        ccNavigate(1);
    } else if (e.key === 'k') {
        e.preventDefault();
        ccNavigate(-1);
    } else if (e.key === 'Enter' && ccSelectedTaskId && !ccActiveTaskId) {
        e.preventDefault();
        ccStartTask(ccSelectedTaskId);
    } else if (e.key === 'd' && ccActiveTaskId) {
        e.preventDefault();
        ccDoneTask(ccActiveTaskId);
    } else if (e.key === 'b' && ccActiveTaskId) {
        e.preventDefault();
        ccBlockTask(ccActiveTaskId);
    } else if (e.key === 'Escape') {
        if (ccActiveTaskId) {
            ccStopTimer();
        } else if (ccSelectedTaskId) {
            ccSelectedTaskId = null;
            ccFocusIndex = -1;
            ccRender();
        }
    } else if (e.key === ' ' && ccActiveTaskId) {
        // Space checks top unchecked subtask
        e.preventDefault();
        const task = ccFindTask(ccActiveTaskId);
        if (task?.checklist) {
            const unchecked = task.checklist.find(c => !c.done);
            if (unchecked) ccToggleActiveCheck(unchecked.id);
        }
    }
});

// ── Decisions Queue ──────────────────────────────────────────────────

async function ccFetchDecisions() {
    try {
        const resp = await fetch('/api/cc/decisions');
        const decisions = await resp.json();
        ccRenderDecisions(decisions);
    } catch (e) { /* silent */ }
}

function ccRenderDecisions(decisions) {
    let container = document.getElementById('cc-decisions');
    if (!container) {
        container = document.createElement('div');
        container.id = 'cc-decisions';
        container.className = 'cc-section';
        const main = document.querySelector('.cc-main');
        const firstSection = main?.querySelector('.cc-section');
        if (main && firstSection) main.insertBefore(container, firstSection);
    }
    if (!decisions.length) { container.style.display = 'none'; return; }
    container.style.display = '';
    container.innerHTML = `
        <div class="cc-section-label" style="color:var(--cc-indigo);">DECISIONS NEEDED <span class="cc-badge">${decisions.length}</span></div>
        <div class="cc-task-list">${decisions.map(d => `
            <div class="cc-task-card cc-decision-card">
                <div class="cc-task-title" style="color:var(--cc-indigo);">${ccEsc(d.question)}</div>
                ${d.context ? `<div style="font-size:12px;color:var(--cc-text-3);margin:4px 0;">${ccEsc(d.context)}</div>` : ''}
                <div class="cc-decision-options">
                    ${d.options.length > 0
                        ? d.options.map(opt => `<button class="cc-decision-opt" onclick="ccAnswerDecision('${d.id}','${ccEsc(opt)}')">${ccEsc(opt)}</button>`).join('')
                        : `<input type="text" class="cc-decision-input" placeholder="Type answer..." onkeydown="if(event.key==='Enter')ccAnswerDecision('${d.id}',this.value)">`
                    }
                </div>
            </div>
        `).join('')}</div>
    `;
}

async function ccAnswerDecision(id, answer) {
    await fetch(`/api/cc/decisions/${id}/answer`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ answer })
    });
    ccFetchDecisions();
}

// ── Activity Feed (sidebar) ─────────────────────────────────────────

async function ccFetchActivity() {
    try {
        const resp = await fetch('/api/cc/activity?limit=20');
        const events = await resp.json();
        ccRenderActivity(events);
    } catch (e) { /* silent */ }
}

function ccRenderActivity(events) {
    let container = document.getElementById('cc-activity-feed');
    if (!container) {
        const sidebar = document.querySelector('.cc-sidebar');
        if (!sidebar) return;
        container = document.createElement('div');
        container.id = 'cc-activity-feed';
        container.style.cssText = 'margin-top:16px;';
        sidebar.appendChild(container);
    }
    if (!events.length) { container.innerHTML = ''; return; }
    const eventIcons = {
        decision_created: '&#10067;', decision_answered: '&#9989;',
        task_created: '&#10133;', task_completed: '&#9989;',
        brief_built: '&#128203;', slack_trawl: '&#128172;',
        blocker_resolved: '&#128275;'
    };
    container.innerHTML = `
        <div style="font-family:'Space Mono',monospace;font-size:10px;color:var(--cc-text-3);text-transform:uppercase;letter-spacing:2px;margin-bottom:8px;">RECENT ACTIVITY</div>
        ${events.slice(0, 10).map(e => {
            const icon = eventIcons[e.event] || '&#128900;';
            const time = new Date(e.ts).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
            return `<div style="display:flex;gap:8px;padding:4px 0;font-size:12px;color:var(--cc-text-2);border-bottom:1px solid var(--cc-border);">
                <span>${icon}</span>
                <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${ccEsc(e.detail || e.event)}</span>
                <span style="color:var(--cc-text-3);font-family:'Space Mono',monospace;font-size:10px;">${time}</span>
            </div>`;
        }).join('')}
    `;
}

// ── Mascot Helper ────────────────────────────────────────────────────

function ccUpdateMascot() {
    const speech = document.getElementById('cc-helper-speech');
    const mouth = document.getElementById('cc-mouth');
    const eyeL = document.getElementById('cc-eye-l');
    const eyeR = document.getElementById('cc-eye-r');
    if (!speech || !ccData) return;

    const done = ccData.completed_today?.length || 0;
    const total = (ccData.quick_wins?.length || 0) + (ccData.today?.length || 0) + (ccData.frog ? 1 : 0) + done;
    const blocked = ccData.blocked?.length || 0;
    const pct = total > 0 ? done / total : 0;

    // Expression + speech based on state
    if (pct >= 1 && total > 0) {
        speech.textContent = "All done! Go enjoy yourself.";
        if (mouth) mouth.setAttribute('d', 'M42,78 Q60,92 78,78');  // big smile
        if (eyeL) eyeL.setAttribute('ry', '2');  // happy squint
        if (eyeR) eyeR.setAttribute('ry', '2');
    } else if (pct >= 0.7) {
        speech.textContent = "Almost there. Strong finish.";
        if (mouth) mouth.setAttribute('d', 'M45,80 Q60,88 75,80');  // smile
    } else if (blocked > 2) {
        speech.textContent = `${blocked} tasks blocked. Check back later.`;
        if (mouth) mouth.setAttribute('d', 'M45,82 L75,82');  // flat
    } else if (total > 10) {
        speech.textContent = "Big day. One task at a time.";
        if (mouth) mouth.setAttribute('d', 'M45,80 Q60,86 75,80');  // mild smile
    } else if (ccEnergyLevel === 'low') {
        speech.textContent = "Low energy. Quick wins first.";
        if (mouth) mouth.setAttribute('d', 'M48,83 Q60,80 72,83');  // slight frown
    } else if (done === 0) {
        speech.textContent = "Pick one. Start small.";
        if (mouth) mouth.setAttribute('d', 'M45,80 Q60,87 75,80');
    } else {
        speech.textContent = `${done} done. Keep going.`;
        if (mouth) mouth.setAttribute('d', 'M45,80 Q60,88 75,80');
    }

    // Blink animation
    if (!ccUpdateMascot._blinkInterval) {
        ccUpdateMascot._blinkInterval = setInterval(() => {
            const el = document.getElementById('cc-eye-l');
            const er = document.getElementById('cc-eye-r');
            if (!el || !er) return;
            el.setAttribute('ry', '1'); er.setAttribute('ry', '1');
            setTimeout(() => { el.setAttribute('ry', '5'); er.setAttribute('ry', '5'); }, 150);
        }, 3000 + Math.random() * 4000);
    }
}

// ── Global Search ────────────────────────────────────────────────────

function ccInjectSearchBar() {
    const header = document.querySelector('.cc-header');
    if (!header || document.getElementById('cc-search-bar')) return;
    const bar = document.createElement('div');
    bar.id = 'cc-search-bar';
    bar.style.cssText = 'margin-bottom:16px;';
    bar.innerHTML = `<input type="text" id="cc-search-input" placeholder="Search tasks, activity, decisions..."
        style="width:100%;padding:10px 14px;min-height:40px;background:var(--cc-surface);border:1px solid var(--cc-border);
        border-radius:var(--cc-radius);color:var(--cc-text-1);font-family:'DM Sans',sans-serif;font-size:13px;outline:none;
        transition:border-color 200ms;"
        onfocus="this.style.borderColor='var(--cc-accent)'"
        onblur="this.style.borderColor='var(--cc-border)'"
    ><div id="cc-search-results" style="display:none;margin-top:8px;"></div>`;
    header.after(bar);
    let _searchTimeout;
    document.getElementById('cc-search-input').addEventListener('input', (e) => {
        clearTimeout(_searchTimeout);
        const q = e.target.value.trim();
        if (q.length < 2) { document.getElementById('cc-search-results').style.display = 'none'; return; }
        _searchTimeout = setTimeout(() => ccDoSearch(q), 300);
    });
}

async function ccDoSearch(q) {
    const container = document.getElementById('cc-search-results');
    if (!container) return;
    try {
        const resp = await fetch(`/api/cc/search?q=${encodeURIComponent(q)}`);
        const data = await resp.json();
        if (data.total === 0) {
            container.style.display = 'block';
            container.innerHTML = `<div style="color:var(--cc-text-3);font-size:12px;padding:8px;">No results for "${ccEsc(q)}"</div>`;
            return;
        }
        let html = '';
        if (data.tasks.length) {
            html += `<div style="font-family:'Space Mono',monospace;font-size:10px;color:var(--cc-accent);text-transform:uppercase;letter-spacing:1.5px;margin:8px 0 4px;">TASKS (${data.tasks.length})</div>`;
            html += data.tasks.slice(0, 5).map(t =>
                `<div class="cc-task-card" style="padding:8px 12px;margin-bottom:4px;cursor:pointer;" onclick="ccSelectTask('${t.id}')">
                    <div class="cc-task-title" style="font-size:13px;">${ccEsc(t.title)}</div>
                    <div style="font-size:11px;color:var(--cc-text-3);">${t.status} · ${t.type}</div>
                </div>`
            ).join('');
        }
        if (data.activity.length) {
            html += `<div style="font-family:'Space Mono',monospace;font-size:10px;color:var(--cc-accent);text-transform:uppercase;letter-spacing:1.5px;margin:8px 0 4px;">ACTIVITY (${data.activity.length})</div>`;
            html += data.activity.slice(0, 5).map(a =>
                `<div style="font-size:12px;color:var(--cc-text-2);padding:4px 0;">${ccEsc(a.event)}: ${ccEsc(a.detail || '')}</div>`
            ).join('');
        }
        if (data.decisions.length) {
            html += `<div style="font-family:'Space Mono',monospace;font-size:10px;color:var(--cc-indigo);text-transform:uppercase;letter-spacing:1.5px;margin:8px 0 4px;">DECISIONS (${data.decisions.length})</div>`;
            html += data.decisions.slice(0, 3).map(d =>
                `<div style="font-size:12px;color:var(--cc-text-2);padding:4px 0;">${ccEsc(d.question)}</div>`
            ).join('');
        }
        container.style.display = 'block';
        container.innerHTML = html;
    } catch (e) { container.style.display = 'none'; }
}

// ── Recurring Weekly Grid ────────────────────────────────────────────

async function ccFetchRecurringGrid() {
    try {
        const resp = await fetch('/api/cc/recurring-grid');
        const data = await resp.json();
        ccRenderRecurringGrid(data);
    } catch (e) { /* silent */ }
}

function ccRenderRecurringGrid(data) {
    let container = document.getElementById('cc-recurring-grid');
    if (!container) {
        const sidebar = document.querySelector('.cc-sidebar');
        if (!sidebar) return;
        container = document.createElement('div');
        container.id = 'cc-recurring-grid';
        container.style.cssText = 'margin-top:16px;';
        sidebar.appendChild(container);
    }
    const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    const today = days[new Date().getDay() === 0 ? 6 : new Date().getDay() - 1];

    // Show only today + tomorrow in sidebar (vertical, readable)
    const todayIdx = days.indexOf(today);
    const showDays = [today, days[(todayIdx + 1) % 7]];

    container.innerHTML = `
        <div style="font-family:'Space Mono',monospace;font-size:11px;color:var(--cc-text-3);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:10px;">WEEKLY RHYTHM</div>
        ${showDays.map(d => {
            const isToday = d === today;
            const tasks = data.grid[d] || [];
            return `<div style="margin-bottom:10px;">
                <div style="font-family:'Space Mono',monospace;font-size:11px;font-weight:600;color:${isToday ? 'var(--cc-accent)' : 'var(--cc-text-2)'};margin-bottom:4px;">${isToday ? 'TODAY' : 'TOMORROW'} · ${d.slice(0,3)}</div>
                ${tasks.length ? tasks.map(t => `<div style="font-size:13px;color:var(--cc-text-1);padding:3px 0;font-family:'DM Sans',sans-serif;">${ccEsc(t.title)}${t.estimated_minutes ? ` <span style="color:var(--cc-text-3);font-size:11px;">${t.estimated_minutes}m</span>` : ''}</div>`).join('') : `<div style="font-size:12px;color:var(--cc-text-3);">Nothing scheduled</div>`}
            </div>`;
        }).join('')}
    `;
}

// ── Health Dot ───────────────────────────────────────────────────────

async function ccCheckHealth() {
    try {
        const resp = await fetch('/api/cc/health');
        const data = await resp.json();
        let dot = document.getElementById('cc-health-dot');
        if (!dot) {
            dot = document.createElement('span');
            dot.id = 'cc-health-dot';
            dot.style.cssText = 'width:8px;height:8px;border-radius:50%;display:inline-block;margin-left:8px;transition:background 200ms;';
            const greeting = document.querySelector('.cc-greeting');
            if (greeting) greeting.appendChild(dot);
        }
        dot.style.background = data.status === 'ok' ? 'var(--cc-accent)' : 'var(--cc-amber)';
        dot.title = data.status === 'ok'
            ? `CC OK · ${data.active_tasks} active · ${data.recurring} recurring · ${data.pending_decisions} decisions`
            : `CC Error: ${data.error || 'unknown'}`;
    } catch (e) {
        const dot = document.getElementById('cc-health-dot');
        if (dot) { dot.style.background = '#ff3b5c'; dot.title = 'CC unreachable'; }
    }
}

// ── Guided Morning Ritual ────────────────────────────────────────────

function ccShowMorningRitual() {
    const overlay = document.createElement('div');
    overlay.id = 'cc-ritual-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(8,9,13,0.85);z-index:100;display:flex;align-items:center;justify-content:center;animation:cc-card-in 300ms both;';

    const totalTasks = ccData ? (ccData.quick_wins?.length || 0) + (ccData.today?.length || 0) + (ccData.frog ? 1 : 0) + (ccData.personal?.length || 0) : 0;
    const carryovers = ccData?.carried_forward || 0;
    const frogTitle = ccData?.frog ? ccEsc(ccData.frog.title) : 'None set';
    const days = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
    const dayContext = {
        1: 'Shipping review, Gorgias triage, plan week',
        2: 'Tommy call, demand pull, cut order (7PM EST)',
        3: 'React tool prep, inventory review, make PO',
        4: 'React tool run, swaps, Shopify sync',
        5: 'Ship day, RMFG email, gel packs, weekly review',
        6: 'Shipping monitoring (light)',
        0: 'Off'
    }[new Date().getDay()] || '';

    overlay.innerHTML = `
        <div style="background:var(--cc-surface);border:1px solid var(--cc-accent);border-radius:var(--cc-radius);padding:32px 40px;max-width:480px;width:90%;box-shadow:0 0 40px rgba(78,204,163,0.2);">
            <div style="font-family:'Rajdhani',sans-serif;font-size:24px;font-weight:600;color:var(--cc-accent);margin-bottom:4px;">Good morning</div>
            <div style="font-family:'Space Mono',monospace;font-size:11px;color:var(--cc-text-3);margin-bottom:20px;">${days[new Date().getDay()]} · ${dayContext}</div>

            <div style="font-family:'DM Sans',sans-serif;font-size:14px;color:var(--cc-text-1);line-height:1.8;margin-bottom:20px;">
                <div>&#128203; <strong>${totalTasks}</strong> tasks today${carryovers > 0 ? ` (${carryovers} carried forward)` : ''}</div>
                <div>&#128056; Frog: <strong>${frogTitle}</strong></div>
            </div>

            <div style="font-family:'Space Mono',monospace;font-size:11px;color:var(--cc-text-3);margin-bottom:12px;">HOW'S YOUR ENERGY?</div>
            <div style="display:flex;gap:8px;margin-bottom:24px;">
                <button class="cc-energy-btn" onclick="ccRitualEnergy('high')" style="flex:1;padding:12px;">&#9889; High</button>
                <button class="cc-energy-btn" onclick="ccRitualEnergy('medium')" style="flex:1;padding:12px;">&#9962; Medium</button>
                <button class="cc-energy-btn" onclick="ccRitualEnergy('low')" style="flex:1;padding:12px;">&#127769; Low</button>
            </div>

            <button onclick="ccDismissRitual()" style="width:100%;padding:12px;background:var(--cc-accent);color:#0a0a0d;border:none;border-radius:var(--cc-radius-sm);font-family:'Space Mono',monospace;font-size:12px;font-weight:600;cursor:pointer;">LET'S GO</button>
        </div>
    `;
    document.body.appendChild(overlay);
}

function ccRitualEnergy(level) {
    ccSetEnergy(level);
    document.querySelectorAll('#cc-ritual-overlay .cc-energy-btn').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');
}

function ccDismissRitual() {
    const overlay = document.getElementById('cc-ritual-overlay');
    if (overlay) {
        overlay.style.opacity = '0';
        overlay.style.transition = 'opacity 200ms';
        setTimeout(() => overlay.remove(), 200);
    }
    // Refresh tasks w/ chosen energy
    ccFetchToday();
}

// ── Adaptive Rescheduling ────────────────────────────────────────────

async function ccCarryForward() {
    // Auto-carry yesterday's incomplete tasks → today (called on load)
    try {
        const resp = await fetch('/api/cc/carryovers');
        const tasks = await resp.json();
        if (tasks.length > 0) {
            // Update mascot
            const speech = document.getElementById('cc-helper-speech');
            if (speech) speech.textContent = `${tasks.length} carried forward from yesterday.`;
        }
    } catch (e) { /* silent */ }
}

/* ── Auto-load on first view ── */
if (typeof currentView !== 'undefined' && currentView === 'commandcenter') {
    ccLoad();
}
