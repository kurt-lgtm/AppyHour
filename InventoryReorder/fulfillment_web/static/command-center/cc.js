/* Command Center — cc.js */

let ccData = null;
let ccEnergyLevel = 'medium';
let ccActiveTaskId = null;
let ccTimerInterval = null;
let ccTimerSeconds = 0;
let ccBriefDismissed = false;
let ccSelectedTaskId = null;
let ccLightenedDay = false;
let ccOverwhelmDismissed = false;
const CC_WIP_LIMIT = 3;  // Max active tasks at once

/* ── Load ── */

function ccLoad() {
    ccAutoEnergy();
    ccSpawnRecurring();
    ccFetchToday();
    ccFetchBrief();
    ccFetchStreaks();
    ccUpdateGreeting();

    // Auto-refresh every 5 minutes (re-check energy, re-fetch tasks)
    if (!ccLoad._interval) {
        ccLoad._interval = setInterval(() => {
            if (typeof currentView !== 'undefined' && currentView === 'commandcenter') {
                ccAutoEnergy();
                ccFetchToday();
            }
        }, 5 * 60 * 1000);
    }
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
        el.innerHTML = '<div class="cc-sidebar-label">STREAKS</div><div style="color:#5a6070;font-size:12px;font-family:DM Sans">Complete recurring tasks to build streaks</div>';
        return;
    }

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
    const greeting = hour < 12 ? 'Good morning.' : hour < 17 ? 'Good afternoon.' : 'Good evening.';
    el.textContent = `${greeting}  ${dayNames[now.getDay()]}, ${months[now.getMonth()]} ${now.getDate()}`;
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

    // Progress
    ccUpdateProgress();
}

function ccRenderList(containerId, tasks, isPersonal) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = tasks.map(t => ccRenderCard(t, false, isPersonal)).join('');
}

function ccRenderCard(task, isFrog, isPersonal) {
    const classes = ['cc-task-card'];
    if (isFrog) classes.push('cc-task-frog');
    if (isPersonal) classes.push('cc-task-personal');
    if (task.status === 'blocked') classes.push('cc-task-blocked');
    if (task.status === 'done') classes.push('cc-task-done');
    if (ccSelectedTaskId === task.id) classes.push('cc-task-focused');

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
        <div class="${classes.join(' ')}" onclick="ccSelectTask('${task.id}')">
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

function ccUpdateProgress() {
    const total = (ccData.quick_wins?.length || 0) + (ccData.today?.length || 0) + (ccData.frog ? 1 : 0);
    const done = ccData.completed_today?.length || 0;
    const all = total + done;
    const pct = all > 0 ? Math.round((done / all) * 100) : 0;

    const fill = document.getElementById('cc-progress-fill');
    const text = document.getElementById('cc-progress-text');
    if (fill) fill.style.width = pct + '%';
    if (text) {
        let label = `${done} of ${all} tasks`;
        if (ccLightenedDay) label += ' (lightened)';
        text.textContent = label;
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
    ccFetchToday();
}

function ccExtendTimer(minutes) {
    // Just keeps running — the estimated_minutes doesn't change, timer just goes longer
    // Visual: timer bar resets its % calc
    const task = ccFindTask(ccActiveTaskId);
    if (task) {
        task.estimated_minutes = (task.estimated_minutes || 25) + minutes;
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

let ccChatModel = 'claude-haiku-4-5';
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

/* ── Auto-load on first view ── */
if (typeof currentView !== 'undefined' && currentView === 'commandcenter') {
    ccLoad();
}
