// ── State ────────────────────────────────────────────────────────────
let results = [];
let weeksData = [];
let calendarData = null;
let score = 0;
let sortCol = 'net';
let sortAsc = true;
let dragSku = null;
let pickerCur = null;
let pickerSlot = null;
let rmfgLoaded = false;  // true after RMFG folder is loaded
let currentView = 'dashboard';
let demandMode = localStorage.getItem('demandMode') || 'discrete';  // 'discrete' or 'churned'

// Mascot state
const mascot = {
    x: 60, y: 200,        // current position
    targetX: 60, targetY: 200, // where it's heading
    vx: 0, vy: 0,
    state: 'idle',
    facing: 'right',
    walkPhase: 0,
    walking: false,
    wanderTimer: 0,
    mouseX: 0, mouseY: 0,
    nearMouse: false,
    petCooldown: 0,
    lastInteraction: Date.now(),
    reminderTimer: 0,
    blinkTimer: 0,
    idleAction: null,
};

// ── Init ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    loadAssignments();
    log('Fulfillment Planner loaded. Auto-running...', '');
    setMascotExpression('loading', 'Booting up...');

    // Auto-run full pipeline on startup
    setTimeout(() => runAll(), 300);

    // Smart auto-refresh: sync interval from settings, day-aware
    initAutoSync();

    // Mouse tracking
    document.addEventListener('mousemove', e => {
        mascot.mouseX = e.clientX;
        mascot.mouseY = e.clientY;
        mascot.lastInteraction = Date.now();
    });

    // Pet reactions handled by enhanced handler at bottom of file

    // Keyboard shortcuts
    document.addEventListener('keydown', e => {
        if (e.ctrlKey && e.key === 'Enter') { (rmfgLoaded ? calculateRMFG : calculate)(); e.preventDefault(); }
        if (e.ctrlKey && e.key === 'i') { importCSV(); e.preventDefault(); }
        if (e.ctrlKey && e.key === 'e') { exportCSV(); e.preventDefault(); }
        if (e.key === 'Escape') closePicker();
    });

    // Start mascot loop
    requestAnimationFrame(mascotLoop);

    // Animated background — floating inventory crates/shelves
    initBgCanvas();
});

// ── Background canvas — supply chain constellation ───────────────────
function initBgCanvas() {
    const canvas = document.getElementById('bg-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    let W, H;

    function resize() {
        W = canvas.width = window.innerWidth;
        H = canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener('resize', resize);

    const CONNECT_DIST = 140;
    const NODE_COUNT = 40;
    const nodes = [];

    // Node types: warehouse (circle), sku (cheese wedge), order (small dot)
    const types = ['warehouse', 'warehouse', 'sku', 'sku', 'sku', 'order', 'order', 'order', 'order'];

    for (let i = 0; i < NODE_COUNT; i++) {
        nodes.push({
            x: Math.random() * W,
            y: Math.random() * H,
            vx: (Math.random() - 0.5) * 0.2,
            vy: (Math.random() - 0.5) * 0.2,
            type: types[Math.floor(Math.random() * types.length)],
            size: 2 + Math.random() * 4,
            pulse: Math.random() * Math.PI * 2,
        });
    }

    // Occasional "shipment" particles that travel between nodes
    const shipments = [];

    function spawnShipment() {
        if (shipments.length > 5) return;
        const a = nodes[Math.floor(Math.random() * nodes.length)];
        const b = nodes[Math.floor(Math.random() * nodes.length)];
        if (a === b) return;
        const dx = b.x - a.x, dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist > CONNECT_DIST * 1.5 || dist < 30) return;
        shipments.push({
            ax: a.x, ay: a.y, bx: b.x, by: b.y,
            t: 0, speed: 0.008 + Math.random() * 0.006,
        });
    }

    function draw() {
        ctx.clearRect(0, 0, W, H);

        // Update nodes
        nodes.forEach(n => {
            n.x += n.vx;
            n.y += n.vy;
            n.pulse += 0.015;
            // Soft bounce off edges
            if (n.x < 0 || n.x > W) n.vx *= -1;
            if (n.y < 0 || n.y > H) n.vy *= -1;
            n.x = Math.max(0, Math.min(W, n.x));
            n.y = Math.max(0, Math.min(H, n.y));
        });

        // Draw connections between nearby nodes
        for (let i = 0; i < nodes.length; i++) {
            for (let j = i + 1; j < nodes.length; j++) {
                const a = nodes[i], b = nodes[j];
                const dx = b.x - a.x, dy = b.y - a.y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < CONNECT_DIST) {
                    const alpha = (1 - dist / CONNECT_DIST) * 0.2;
                    ctx.beginPath();
                    ctx.moveTo(a.x, a.y);
                    ctx.lineTo(b.x, b.y);
                    ctx.strokeStyle = `rgba(0, 212, 255, ${alpha})`;
                    ctx.lineWidth = 0.5;
                    ctx.stroke();
                }
            }
        }

        // Draw nodes
        nodes.forEach(n => {
            const pulseAlpha = 0.35 + Math.sin(n.pulse) * 0.15;
            ctx.globalAlpha = pulseAlpha;

            if (n.type === 'warehouse') {
                // Hollow circle — warehouse/hub
                ctx.beginPath();
                ctx.arc(n.x, n.y, n.size + 1, 0, Math.PI * 2);
                ctx.strokeStyle = '#00d4ff';
                ctx.lineWidth = 0.8;
                ctx.stroke();
                // Inner dot
                ctx.beginPath();
                ctx.arc(n.x, n.y, 1.2, 0, Math.PI * 2);
                ctx.fillStyle = '#00d4ff';
                ctx.fill();
            } else if (n.type === 'sku') {
                // Cheese wedge triangle
                const s = n.size;
                ctx.beginPath();
                ctx.moveTo(n.x, n.y - s);
                ctx.lineTo(n.x + s * 0.8, n.y + s * 0.6);
                ctx.lineTo(n.x - s * 0.8, n.y + s * 0.6);
                ctx.closePath();
                ctx.strokeStyle = 'rgba(240, 192, 64, 0.7)';
                ctx.lineWidth = 0.6;
                ctx.stroke();
            } else {
                // Small dot — order
                ctx.beginPath();
                ctx.arc(n.x, n.y, 1.2, 0, Math.PI * 2);
                ctx.fillStyle = 'rgba(0, 212, 255, 0.6)';
                ctx.fill();
            }
        });

        ctx.globalAlpha = 1;

        // Shipments — small bright dots moving along connection lines
        if (Math.random() < 0.02) spawnShipment();

        for (let i = shipments.length - 1; i >= 0; i--) {
            const s = shipments[i];
            s.t += s.speed;
            if (s.t >= 1) { shipments.splice(i, 1); continue; }
            const x = s.ax + (s.bx - s.ax) * s.t;
            const y = s.ay + (s.by - s.ay) * s.t;
            const glow = Math.sin(s.t * Math.PI); // brightest in middle
            ctx.beginPath();
            ctx.arc(x, y, 1.5, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(0, 212, 255, ${0.5 + glow * 0.5})`;
            ctx.fill();
            // Tiny trail
            const tx = s.ax + (s.bx - s.ax) * Math.max(0, s.t - 0.08);
            const ty = s.ay + (s.by - s.ay) * Math.max(0, s.t - 0.08);
            ctx.beginPath();
            ctx.moveTo(tx, ty);
            ctx.lineTo(x, y);
            ctx.strokeStyle = `rgba(0, 212, 255, ${glow * 0.4})`;
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        requestAnimationFrame(draw);
    }

    draw();
}

function randomPick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

// ── API ──────────────────────────────────────────────────────────────
async function api(url, opts = {}) {
    const resp = await fetch(url, opts);
    return resp.json();
}

// ── Logging ──────────────────────────────────────────────────────────
function log(msg, style = '') {
    const el = document.getElementById('log');
    const ts = new Date().toLocaleTimeString('en-US', { hour12: false });
    const cls = style ? `log-${style}` : '';
    el.innerHTML += `<div class="log-entry"><span class="log-time">[${ts}]</span> <span class="${cls}">${msg}</span></div>`;
    el.scrollTop = el.scrollHeight;
}

// ══════════════════════════════════════════════════════════════════════
//  MASCOT ENGINE -- roaming, mouse-tracking, expressions, suggestions
// ══════════════════════════════════════════════════════════════════════

function mascotLoop(ts) {
    const el = document.getElementById('mascot');
    const dt = 1; // frame

    mascot.wanderTimer--;
    mascot.blinkTimer--;
    if (mascot.petCooldown > 0) mascot.petCooldown--;
    mascot.reminderTimer--;

    // -- Eye tracking: pupils follow mouse --
    updatePupils();

    // -- Blink --
    if (mascot.blinkTimer <= 0) {
        blink();
        mascot.blinkTimer = 120 + Math.random() * 240; // 2-6 sec at 60fps
    }

    // -- Decide movement target --
    const dx = mascot.mouseX - mascot.x - 55;
    const dy = mascot.mouseY - mascot.y - 47;
    const distToMouse = Math.sqrt(dx * dx + dy * dy);

    // Follow mouse if close-ish (within 250px) but keep ~80px distance
    if (distToMouse < 250 && distToMouse > 90 && mascot.state === 'idle') {
        mascot.targetX = mascot.mouseX - 55 + (dx > 0 ? -80 : 80);
        mascot.targetY = mascot.mouseY - 47;
        mascot.nearMouse = true;
    } else if (distToMouse <= 90) {
        mascot.nearMouse = true;
        // Stay put, just look at mouse
    } else {
        mascot.nearMouse = false;
    }

    // Wander randomly when idle and mouse is far
    if (!mascot.nearMouse && mascot.state === 'idle' && mascot.wanderTimer <= 0) {
        mascot.wanderTimer = 180 + Math.random() * 300; // 3-8 sec
        // Pick a spot: prefer panel edges, toolbar area, or near summary widgets
        const spots = getInterestingSpots();
        const spot = randomPick(spots);
        mascot.targetX = spot.x;
        mascot.targetY = spot.y;
        mascot.idleAction = spot.action || null;
    }

    // -- Move toward target --
    const tx = mascot.targetX - mascot.x;
    const ty = mascot.targetY - mascot.y;
    const dist = Math.sqrt(tx * tx + ty * ty);

    if (dist > 5) {
        const speed = mascot.nearMouse ? 2.5 : 1.5;
        mascot.vx = (tx / dist) * speed;
        mascot.vy = (ty / dist) * speed;
        mascot.x += mascot.vx;
        mascot.y += mascot.vy;
        mascot.walking = true;
        mascot.walkPhase += 0.15;
        mascot.facing = mascot.vx > 0 ? 'right' : 'left';
    } else {
        mascot.vx = 0; mascot.vy = 0;
        mascot.walking = false;

        // Do idle action when arrived
        if (mascot.idleAction) {
            doIdleAction(mascot.idleAction);
            mascot.idleAction = null;
        }
    }

    // Keep in bounds (invisible fence — prevent head clipping)
    mascot.x = Math.max(10, Math.min(window.innerWidth - 120, mascot.x));
    mascot.y = Math.max(70, Math.min(window.innerHeight - 100, mascot.y));

    // -- Animate legs + speed lines --
    animateLegs();
    const sl = document.getElementById('mascot-speed-lines');
    if (sl && mascot.state !== 'loading') {
        sl.setAttribute('opacity', mascot.walking ? '0.5' : '0');
    }

    // -- Apply position --
    el.style.left = mascot.x + 'px';
    el.style.top = mascot.y + 'px';
    el.classList.toggle('flipped', mascot.facing === 'left');

    // -- Position speech bubble near Kori --
    const bubble = document.getElementById('mascot-speech');
    if (bubble) {
        const bw = bubble.offsetWidth || 160;
        const bh = bubble.offsetHeight || 50;
        const tail = document.getElementById('mascot-speech-tail');
        // Anchor to Kori's head (center-x, top of mascot)
        let bx = mascot.x + 55 - bw / 2;
        let by = mascot.y - bh - 8; // snug above head

        if (by < 5) {
            // Near top — flip bubble below Wedge
            by = mascot.y + 100;
            if (tail) { tail.style.bottom = ''; tail.style.top = '-6px'; tail.style.transform = 'rotate(-135deg)'; }
        } else {
            if (tail) { tail.style.top = ''; tail.style.bottom = '-6px'; tail.style.transform = 'rotate(45deg)'; }
        }
        bx = Math.max(5, Math.min(window.innerWidth - bw - 5, bx));
        bubble.style.left = bx + 'px';
        bubble.style.top = by + 'px';
    }

    // -- Reminders & suggestions --
    if (mascot.reminderTimer <= 0) {
        mascot.reminderTimer = 600 + Math.random() * 600; // 10-20 sec
        doReminder();
    }

    requestAnimationFrame(mascotLoop);
}

function getInterestingSpots() {
    const spots = [];
    const w = window.innerWidth;
    const h = window.innerHeight;

    // Near toolbar buttons
    spots.push({ x: 200, y: 45, action: 'toolbar' });
    spots.push({ x: 400, y: 45, action: 'toolbar' });

    // Near summary widgets
    spots.push({ x: w * 0.55, y: 80, action: 'summary' });
    spots.push({ x: w * 0.75, y: 80, action: 'summary' });

    // Near assignment panel (left side)
    spots.push({ x: 30, y: 200, action: 'assignments' });
    spots.push({ x: 30, y: 350, action: 'assignments' });

    // Along bottom of NET table
    spots.push({ x: w * 0.6, y: h - 180, action: 'net_table' });

    // Near shelf life
    spots.push({ x: 30, y: h - 150, action: 'shelf' });

    // Random walk
    spots.push({ x: 50 + Math.random() * (w - 150), y: 50 + Math.random() * (h - 200) });

    return spots;
}

function doIdleAction(action) {
    if (mascot.state !== 'idle') return;

    switch (action) {
        case 'summary':
            if (results.length > 0) {
                const shortages = results.filter(r => r.status === 'SHORTAGE');
                if (shortages.length > 0) {
                    setMascotExpression('worried',
                        `${shortages.length} shortage${shortages.length > 1 ? 's' : ''} still...`);
                    setTimeout(() => setMascotExpression('idle'), 3000);
                }
            }
            break;
        case 'assignments':
            // Peek at assignments
            break;
        case 'net_table':
            if (results.length === 0) {
                setMascotExpression('thinking', 'No data yet... hit Calculate?');
                setTimeout(() => setMascotExpression('idle'), 3000);
            }
            break;
    }
}

function doReminder() {
    if (mascot.state !== 'idle') return;
    const idle = Date.now() - mascot.lastInteraction;

    // Only remind if user hasn't done anything for 15+ seconds
    if (idle < 15000) return;

    const reminders = [];

    // Context-aware suggestions
    if (results.length === 0) {
        reminders.push({ msg: "Click Calculate to see your NETs!", priority: 3 });
        reminders.push({ msg: "Import a CSV to load demand data", priority: 2 });
    } else {
        const shortages = results.filter(r => r.status === 'SHORTAGE');
        const tight = results.filter(r => r.status === 'TIGHT');

        if (shortages.length > 0) {
            const worst = shortages[0];
            reminders.push({
                msg: `${worst.sku} is short by ${Math.abs(worst.net)}. Try Suggest Fixes!`,
                priority: 3,
            });
            reminders.push({
                msg: `${shortages.length} shortages -- try Auto-Assign to rebalance`,
                priority: 2,
            });
            if (shortages.length >= 3) {
                reminders.push({
                    msg: "Lots of shortages! Maybe generate a Wednesday PO?",
                    priority: 2,
                });
            }
        }

        if (tight.length > 0) {
            reminders.push({
                msg: `${tight[0].sku} is tight (NET ${tight[0].net}). Keep an eye on it.`,
                priority: 1,
            });
        }

        if (shortages.length === 0 && tight.length === 0) {
            reminders.push({ msg: "Everything looks great! Export your report?", priority: 1 });
            reminders.push({ msg: "Check Next Saturday tab -- plan ahead!", priority: 1 });
        }

        // Week projection reminders
        if (weeksData.length > 0) {
            const w2 = weeksData[0];
            if (w2 && w2.shortages > 0) {
                reminders.push({
                    msg: `Next week has ${w2.shortages} projected shortages. Plan POs!`,
                    priority: 2,
                });
            }
        }

        // Variety check reminder
        reminders.push({ msg: "Run a Variety Check to catch overlaps!", priority: 0 });
    }

    // General tips
    reminders.push({ msg: "Drag a cheese from the NET table onto an assignment!", priority: 0 });
    reminders.push({ msg: "Click me for a surprise!", priority: 0 });

    if (reminders.length === 0) return;

    // Pick highest priority, with some randomness
    reminders.sort((a, b) => b.priority - a.priority);
    const top = reminders.filter(r => r.priority === reminders[0].priority);
    const pick = randomPick(top);

    const expr = pick.priority >= 2 ? 'thinking' : 'idle';
    setMascotExpression(expr, pick.msg);
    log(`Wedge: ${pick.msg}`, 'cyan');

    // Return to situation-appropriate mood after reminder
    setTimeout(() => {
        if (mascot.state === expr) {
            const mood = mascot.currentMood || 'idle';
            const moodState = { alert: 'worried', worried: 'worried', thinking: 'idle', happy: 'idle' }[mood] || 'idle';
            setMascotExpression(moodState);
        }
    }, 5000);
}

// -- Pupil tracking (Cheddy coordinate space: eyes at ~95,91 and ~138,93) --
function updatePupils() {
    const el = document.getElementById('mascot');
    const rect = el.getBoundingClientRect();
    const cx = rect.left + rect.width * 0.53;
    const cy = rect.top + rect.height * 0.47;

    const dx = mascot.mouseX - cx;
    const dy = mascot.mouseY - cy;
    const dist = Math.sqrt(dx * dx + dy * dy);
    const maxOffset = 3;

    let ox = 0, oy = 0;
    if (dist > 10) {
        ox = (dx / dist) * maxOffset;
        oy = (dy / dist) * maxOffset;
    }

    const flip = mascot.facing === 'left' ? -1 : 1;

    // Move iris ellipses (Wedge's dark iris IS the pupil)
    const irisEls = document.querySelectorAll('.mascot-eye-iris');
    if (irisEls.length === 2) {
        irisEls[0].setAttribute('cx', 67 + ox * flip);
        irisEls[0].setAttribute('cy', 99 + oy);
        irisEls[1].setAttribute('cx', 112 + ox * flip);
        irisEls[1].setAttribute('cy', 99 + oy);
    }
}

// -- Blink --
function blink() {
    const eyeL = document.getElementById('eye-bg-l');
    const eyeR = document.getElementById('eye-bg-r');
    const eyes = document.getElementById('mascot-eyes');
    if (!eyeL || !eyeR) return;
    if (mascot.state === 'loading') return;

    // Hide entire eyes group briefly, show wink arcs
    const winkL = document.getElementById('mascot-wink-l');
    const winkR = document.getElementById('mascot-wink-r');
    if (eyes) eyes.setAttribute('opacity', '0');
    if (winkL) winkL.setAttribute('opacity', '1');
    if (winkR) winkR.setAttribute('opacity', '1');
    setTimeout(() => {
        if (mascot.state !== 'loading' && mascot.state !== 'thinking') {
            if (eyes) eyes.setAttribute('opacity', '1');
        }
        if (mascot.state !== 'loading') {
            if (winkL) winkL.setAttribute('opacity', '0');
        }
        if (mascot.state !== 'loading' && mascot.state !== 'thinking') {
            // winkL might be active for thinking
        }
        if (winkR && mascot.state !== 'loading') winkR.setAttribute('opacity', '0');
    }, 120);
}

// -- Leg animation (Cheddy: legs are groups with rect+ellipse, translate whole group) --
function animateLegs() {
    if (!mascot.walking) return;
    const phase = Math.sin(mascot.walkPhase);
    const legL = document.getElementById('leg-l');
    const legR = document.getElementById('leg-r');
    if (!legL) return;

    const swing = phase * 6;
    legL.setAttribute('transform', `translate(${swing}, 0)`);
    legR.setAttribute('transform', `translate(${-swing}, 0)`);
}

// ── Mascot expression (Cheddy) ──────────────────────────────────────
function setMascotExpression(state, msg) {
    mascot.state = state;
    const el = document.getElementById('mascot');
    const msgEl = document.getElementById('mascot-msg');
    const stateEl = document.getElementById('mascot-state');
    const mouth = document.getElementById('mascot-mouth');
    const mouthFill = document.getElementById('mascot-mouth-fill');
    const extra = document.getElementById('mascot-extra');
    const browL = document.getElementById('brow-l');
    const browR = document.getElementById('brow-r');
    const blushL = document.getElementById('blush-l');
    const blushR = document.getElementById('blush-r');
    const armL = document.getElementById('mascot-arm-l');
    const armR = document.getElementById('mascot-arm-r');
    const eyeBgL = document.getElementById('eye-bg-l');
    const eyeBgR = document.getElementById('eye-bg-r');

    el.className = state;
    if (msg) {
        msgEl.textContent = msg;
        const bubble = document.getElementById('mascot-speech');
        if (bubble) {
            bubble.classList.add('visible');
            clearTimeout(mascot._bubbleTimer);
            mascot._bubbleTimer = setTimeout(() => bubble.classList.remove('visible'), 6000);
        }
    }
    stateEl.textContent = state;

    const speedLines = document.getElementById('mascot-speed-lines');
    const winkL = document.getElementById('mascot-wink-l');
    const winkR = document.getElementById('mascot-wink-r');
    const heartL = document.getElementById('mascot-heart-l');
    const heartR = document.getElementById('mascot-heart-r');
    const tongue = document.getElementById('mascot-tongue');
    const eyes = document.getElementById('mascot-eyes');

    // Reset everything to happy (default) state
    blushL.style.opacity = '0.55'; blushR.style.opacity = '0.50';
    if (speedLines) speedLines.setAttribute('opacity', '0');
    extra.setAttribute('opacity', '0');

    // Reset eyes to normal (visible)
    if (eyes) eyes.setAttribute('opacity', '1');
    eyeBgL.setAttribute('ry', '8'); eyeBgR.setAttribute('ry', '8');
    eyeBgL.setAttribute('rx', '7'); eyeBgR.setAttribute('rx', '7');
    // Reset iris to default size
    const irisReset = document.querySelectorAll('.mascot-eye-iris');
    if (irisReset.length === 2) {
        irisReset[0].setAttribute('rx', '3.5'); irisReset[0].setAttribute('ry', '4.5');
        irisReset[1].setAttribute('rx', '3.5'); irisReset[1].setAttribute('ry', '4.5');
    }
    if (winkL) winkL.setAttribute('opacity', '0');
    if (winkR) winkR.setAttribute('opacity', '0');
    if (heartL) heartL.setAttribute('opacity', '0');
    if (heartR) heartR.setAttribute('opacity', '0');
    if (tongue) tongue.setAttribute('opacity', '0');
    browL.setAttribute('opacity', '0'); browR.setAttribute('opacity', '0');

    // Default mouth: big D-shape open smile
    mouth.setAttribute('d', 'M70,118 L105,118 Q108,118 108,122 Q108,142 88,145 Q68,142 68,122 Q68,118 70,118 Z');
    mouth.setAttribute('fill', 'url(#mouthG)');
    mouth.setAttribute('stroke', '#5C3A10');
    mouth.setAttribute('stroke-width', '2.5');
    mouth.setAttribute('opacity', '1');
    mouthFill.setAttribute('d', 'M78,136 Q88,142 98,136');
    mouthFill.setAttribute('fill', '#E87080');
    mouthFill.setAttribute('opacity', '1');

    // Default arms: at sides (from tiny torso)
    armL.setAttribute('d', 'M58,195 Q45,192 40,198 Q38,203 42,204 Q46,205 47,200 Q49,196 58,197');
    armR.setAttribute('d', 'M102,195 Q115,192 120,198 Q122,203 118,204 Q114,205 113,200 Q111,196 102,197');

    switch (state) {
        case 'happy':
        case 'celebrate':
            // Excited: big pupils, ellipse mouth, arms UP
            const irisEls = document.querySelectorAll('.mascot-eye-iris');
            if (irisEls.length === 2) {
                irisEls[0].setAttribute('rx', '4.5'); irisEls[0].setAttribute('ry', '5.5');
                irisEls[1].setAttribute('rx', '4.5'); irisEls[1].setAttribute('ry', '5.5');
            }
            // Bigger open mouth
            mouth.setAttribute('d', 'M65,118 L110,118 Q114,118 114,122 Q114,148 88,152 Q62,148 62,122 Q62,118 65,118 Z');
            mouth.setAttribute('opacity', '1');
            mouthFill.setAttribute('d', 'M76,142 Q88,150 100,142');
            mouthFill.setAttribute('fill', '#E87080');
            // Arms raised
            armL.setAttribute('d', 'M58,192 Q42,182 36,174 Q33,169 37,167 Q41,166 43,172 Q47,180 58,188');
            armR.setAttribute('d', 'M102,192 Q118,182 124,174 Q127,169 123,167 Q119,166 117,172 Q113,180 102,188');
            if (state === 'celebrate') {
                extra.textContent = '\u2605'; extra.setAttribute('opacity', '1');
                extra.style.fill = '#00d4ff';
            }
            break;
        case 'worried':
            // Surprised-ish: wide eyes, O mouth, sweatdrop
            eyeBgL.setAttribute('ry', '10'); eyeBgR.setAttribute('ry', '10');
            eyeBgL.setAttribute('rx', '9'); eyeBgR.setAttribute('rx', '9');
            // Small O mouth
            mouth.setAttribute('d', '');
            mouth.setAttribute('opacity', '0');
            mouthFill.setAttribute('d', 'M78,120 A10,12 0 1,0 98,120 A10,12 0 1,0 78,120');
            mouthFill.setAttribute('fill', 'url(#mouthG)');
            // One arm raised
            armL.setAttribute('d', 'M58,192 Q42,182 36,174 Q33,169 37,167 Q41,166 43,172 Q47,180 58,188');
            extra.textContent = '\u2019'; extra.setAttribute('opacity', '1');
            extra.style.fill = '#60a5fa';
            break;
        case 'alert':
            // Surprised: wide eyes, O mouth, !! marks
            eyeBgL.setAttribute('ry', '10'); eyeBgR.setAttribute('ry', '10');
            eyeBgL.setAttribute('rx', '9'); eyeBgR.setAttribute('rx', '9');
            // O mouth
            mouth.setAttribute('d', '');
            mouth.setAttribute('opacity', '0');
            mouthFill.setAttribute('d', 'M78,120 A10,12 0 1,0 98,120 A10,12 0 1,0 78,120');
            mouthFill.setAttribute('fill', 'url(#mouthG)');
            // Both arms raised
            armL.setAttribute('d', 'M58,192 Q42,182 36,174 Q33,169 37,167 Q41,166 43,172 Q47,180 58,188');
            armR.setAttribute('d', 'M102,192 Q118,182 124,174 Q127,169 123,167 Q119,166 117,172 Q113,180 102,188');
            extra.textContent = '!!'; extra.setAttribute('opacity', '1');
            extra.style.fill = '#ff3b5c';
            blushL.style.opacity = '0'; blushR.style.opacity = '0';
            break;
        case 'loading':
            // Laughing: both eyes closed, big mouth, arms at belly
            if (eyes) eyes.setAttribute('opacity', '0');
            if (winkL) winkL.setAttribute('opacity', '1');
            if (winkR) winkR.setAttribute('opacity', '1');
            // Big ellipse mouth
            mouth.setAttribute('d', '');
            mouth.setAttribute('opacity', '0');
            mouthFill.setAttribute('d', 'M65,118 A23,18 0 1,0 110,118 A23,18 0 1,0 65,118');
            mouthFill.setAttribute('fill', 'url(#mouthG)');
            if (speedLines) speedLines.setAttribute('opacity', '0.6');
            break;
        case 'thinking':
            // Wink: left eye closed, right open, smirk + tongue
            if (eyes) eyes.setAttribute('opacity', '1');
            if (winkL) winkL.setAttribute('opacity', '1');
            // Hide left eye elements only
            eyeBgL.setAttribute('ry', '0');
            // Smirk mouth (curve, no fill)
            mouth.setAttribute('d', 'M70,120 Q88,138 105,120');
            mouth.setAttribute('fill', 'none');
            mouth.setAttribute('stroke', '#5C3A10');
            mouth.setAttribute('stroke-width', '3');
            mouthFill.setAttribute('opacity', '0');
            if (tongue) tongue.setAttribute('opacity', '1');
            // One arm raised
            armL.setAttribute('d', 'M58,192 Q42,182 36,174 Q33,169 37,167 Q41,166 43,172 Q47,180 58,188');
            extra.textContent = '?'; extra.setAttribute('opacity', '1');
            extra.style.fill = '#00d4ff';
            break;
        default: // idle — happy face (Wedge's default happy expression)
            // Reset iris size to normal
            const irisDefault = document.querySelectorAll('.mascot-eye-iris');
            if (irisDefault.length === 2) {
                irisDefault[0].setAttribute('rx', '3.5'); irisDefault[0].setAttribute('ry', '4.5');
                irisDefault[1].setAttribute('rx', '3.5'); irisDefault[1].setAttribute('ry', '4.5');
            }
            break;
    }
}

// Shorthand used by old code
function setMascot(state, msg) { setMascotExpression(state, msg); }

// ── Score ─────────────────────────────────────────────────────────────
function addScore(delta, x, y) {
    if (delta > 0) score += delta;
    const el = document.getElementById('score');
    el.textContent = score > 0 ? `Score: ${score}` : '';
    if (delta > 0) {
        el.classList.remove('pop');
        void el.offsetWidth;
        el.classList.add('pop');
    }
    if (x !== undefined && y !== undefined && delta > 0) {
        const pop = document.createElement('div');
        pop.className = 'score-pop';
        pop.textContent = `+${delta}`;
        pop.style.left = x + 'px';
        pop.style.top = y + 'px';
        document.body.appendChild(pop);
        setTimeout(() => pop.remove(), 1000);
    }
}

// ── Confetti ─────────────────────────────────────────────────────────
function spawnConfetti() {
    const emojis = ['🧀', '🎉', '✨', '⭐', '🟢', '🟡'];
    for (let i = 0; i < 30; i++) {
        const c = document.createElement('div');
        c.className = 'confetti';
        c.textContent = emojis[Math.floor(Math.random() * emojis.length)];
        c.style.left = (Math.random() * 100) + 'vw';
        c.style.top = '-30px';
        c.style.animationDelay = (Math.random() * 0.8) + 's';
        c.style.animationDuration = (1.5 + Math.random()) + 's';
        document.body.appendChild(c);
        setTimeout(() => c.remove(), 3000);
    }
}

// ── Assignments ──────────────────────────────────────────────────────
async function loadAssignments() {
    const rows = await api('/api/assignments');
    renderAssignments(rows);
    loadMonthlyBoxes();
}

async function loadMonthlyBoxes() {
    const data = await api('/api/monthly_boxes');
    renderMonthlyBoxes(data);
}

function renderMonthlyBoxes(data) {
    const container = document.getElementById('monthly-boxes-container');
    if (!container) return;
    container.innerHTML = '';
    for (const [boxType, info] of Object.entries(data)) {
        const panel = document.createElement('div');
        panel.className = 'monthly-box-panel';
        let slotsHtml = '';
        info.slots.forEach((slot, idx) => {
            const assigned = slot.sku ? slot.sku : '\u2014';
            const cls = slot.sku ? 'assigned' : 'unassigned';
            slotsHtml += `<tr>
                <td class="mb-slot-name">${slot.slot}</td>
                <td class="mb-sku-cell ${cls}" data-box="${boxType}" data-idx="${idx}">${assigned}</td>
            </tr>`;
        });
        const countDisplay = info.from_orders
            ? `<span class="mb-count-label" title="From Shopify + Recharge orders">${info.count} orders</span>`
            : `<input type="number" class="mb-count-input" value="${info.count}" min="0"
                       data-box="${boxType}" title="Box count (manual)">`;
        panel.innerHTML = `
            <div class="mb-header">
                <span class="mb-type">${boxType}</span>
                ${countDisplay}
            </div>
            <table class="assign-table mb-table">
                <thead><tr><th>Slot</th><th>SKU</th></tr></thead>
                <tbody>${slotsHtml}</tbody>
            </table>
        `;
        // Click handlers for SKU cells
        panel.querySelectorAll('.mb-sku-cell').forEach(td => {
            td.addEventListener('click', () => openMonthlyBoxPicker(td.dataset.box, parseInt(td.dataset.idx)));
        });
        // Count input handler (only for manual counts)
        const countInput = panel.querySelector('.mb-count-input');
        if (countInput) {
            countInput.addEventListener('change', async () => {
                await fetch('/api/monthly_box_count', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ box_type: boxType, count: parseInt(countInput.value) || 0 }),
                });
                log(`${boxType} count set to ${countInput.value}`, 'cyan');
            });
        }
        container.appendChild(panel);
    }
}

async function openMonthlyBoxPicker(boxType, slotIndex) {
    const candidates = await api(`/api/monthly_box_candidates/${boxType}/${slotIndex}`);
    const list = document.getElementById('picker-list');
    list.innerHTML = '';
    document.getElementById('picker-title').textContent = `Assign ${boxType} Slot ${slotIndex + 1}`;
    candidates.forEach(c => {
        const div = document.createElement('div');
        div.className = 'picker-item';
        div.innerHTML = `
            <span class="pi-sku">${c.sku}</span>
            <span class="pi-qty">${c.qty} avail</span>
            <span class="pi-constraint pi-ok">${c.name || ''}</span>
        `;
        div.addEventListener('click', async () => {
            const resp = await fetch('/api/monthly_box_assign', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ box_type: boxType, slot_index: slotIndex, sku: c.sku }),
            });
            const data = await resp.json();
            if (data.ok) {
                log(`Assigned: ${c.sku} -> ${boxType} slot ${slotIndex + 1}`, 'green');
                closePicker();
                loadMonthlyBoxes();
            }
        });
        list.appendChild(div);
    });
    document.getElementById('picker-overlay').classList.add('visible');
}

function renderAssignments(rows) {
    const tbody = document.getElementById('assign-body');
    tbody.innerHTML = '';
    rows.forEach(r => {
        const tr = document.createElement('tr');
        tr.dataset.cur = r.curation;
        tr.innerHTML = `
            <td>${r.curation}</td>
            <td class="cheese-cell pr-cheese" data-slot="prcjam">${r.prcjam_cheese}</td>
            <td class="qty-cell" id="pr-qty-${r.curation}">${r.pr_qty > 0 ? r.pr_qty : ''}</td>
            <td class="cheese-cell ec-cheese" data-slot="cexec">${r.cexec_cheese}</td>
            <td class="qty-cell" id="ec-qty-${r.curation}">${r.ec_qty > 0 ? r.ec_qty : ''}</td>
            <td class="split-cell">${r.split}</td>
            <td class="${r.constraint === 'OK' ? 'constraint-ok' : 'constraint-bad'}">${r.constraint}</td>
        `;
        tr.querySelectorAll('.cheese-cell').forEach(td => {
            td.addEventListener('click', () => openPicker(r.curation, td.dataset.slot));
        });
        tr.addEventListener('dragover', e => { e.preventDefault(); tr.classList.add('drop-hover'); });
        tr.addEventListener('dragleave', () => tr.classList.remove('drop-hover'));
        tr.addEventListener('drop', e => {
            e.preventDefault(); tr.classList.remove('drop-hover');
            if (!dragSku) return;
            const rect = tr.getBoundingClientRect();
            const slot = (e.clientX - rect.left) < rect.width / 2 ? 'prcjam' : 'cexec';
            assignCheese(r.curation, slot, dragSku, e.clientX, e.clientY);
            dragSku = null;
        });
        tbody.appendChild(tr);
    });
}

// ── Global Extras ────────────────────────────────────────────────────
async function loadGlobalExtras() {
    const data = await api('/api/global_extras');
    renderGlobalExtras(data);
}

function renderGlobalExtras(data) {
    const tbody = document.getElementById('extras-body');
    if (!tbody) return;
    tbody.innerHTML = '';
    for (const [slot, info] of Object.entries(data)) {
        const tr = document.createElement('tr');
        const hasAssignment = info.sku && info.sku.length > 0;
        tr.innerHTML = `
            <td class="extras-slot-cell">${slot}</td>
            <td class="extras-type-cell">${info.category}</td>
            <td class="extras-sku-cell ${hasAssignment ? 'assigned' : 'unassigned'}">${info.sku || '—'}</td>
            <td class="qty-cell">${hasAssignment ? info.qty : ''}</td>
        `;
        tr.querySelector('.extras-sku-cell').addEventListener('click', () => openExtraPicker(slot));
        tbody.appendChild(tr);
    }
}

async function openExtraPicker(slot) {
    const candidates = await api(`/api/global_extra_candidates/${slot}`);
    const list = document.getElementById('picker-list');
    list.innerHTML = '';
    document.getElementById('picker-title').textContent = `Assign ${slot}`;
    candidates.forEach(c => {
        const div = document.createElement('div');
        div.className = 'picker-item';
        div.innerHTML = `
            <span class="pi-sku">${c.sku}</span>
            <span class="pi-qty">${c.qty} avail</span>
            <span class="pi-constraint pi-ok">${c.name || ''}</span>
        `;
        div.addEventListener('click', async () => {
            const resp = await fetch('/api/set_global_extra', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ slot, sku: c.sku }),
            });
            const data = await resp.json();
            if (data.ok) {
                log(`Assigned: ${c.sku} -> ${slot}`, 'green');
                setMascot('happy', `${c.sku} -> ${slot}!`);
                closePicker();
                loadGlobalExtras();
                if (results.length) { rmfgLoaded ? calculateRMFG() : calculate(); }
                setTimeout(() => setMascot('idle'), 2500);
            } else {
                log(`Failed: ${data.error}`, 'red');
            }
        });
        list.appendChild(div);
    });
    document.getElementById('picker-overlay').classList.add('visible');
}

async function autoAssignExtras() {
    setMascot('loading', 'Auto-assigning extras...');
    const data = await api('/api/auto_assign_extras', { method: 'POST' });
    if (data.changes && data.changes.length > 0) {
        data.changes.forEach(c => log(`Extra: ${c}`, 'green'));
        setMascot('happy', `${data.count} extras assigned!`);
    } else {
        log('Extras unchanged', 'yellow');
        setMascot('idle', 'No changes needed');
    }
    loadGlobalExtras();
    if (results.length) { rmfgLoaded ? calculateRMFG() : calculate(); }
    setTimeout(() => setMascot('idle'), 2500);
}

// ── Cheese Picker ────────────────────────────────────────────────────
async function openPicker(curation, slot) {
    pickerCur = curation; pickerSlot = slot;
    document.getElementById('picker-title').textContent =
        `${slot === 'prcjam' ? 'PR-CJAM' : 'CEX-EC'} - ${curation}`;
    const candidates = await api(`/api/candidates/${curation}/${slot}`);
    const list = document.getElementById('picker-list');
    list.innerHTML = '';
    candidates.forEach(c => {
        const div = document.createElement('div');
        div.className = `picker-item${c.constraint !== 'OK' ? ' blocked' : ''}`;
        div.innerHTML = `
            <span class="pi-sku">${c.sku}</span>
            <span class="pi-qty">${c.qty} avail</span>
            <span class="pi-constraint ${c.constraint === 'OK' ? 'pi-ok' : 'pi-bad'}">${c.constraint}</span>
        `;
        div.addEventListener('click', () => {
            if (c.constraint !== 'OK') {
                log(`Warning: ${c.sku} for ${curation} has adjacency overlap (${c.constraint}) — assigning anyway`, 'yellow');
                setMascot('thinking', `${c.sku} overlaps nearby — proceed with caution`);
            }
            if (pickerCallback) {
                pickerCallback(curation, slot, c.sku);
                pickerCallback = null;
            } else {
                assignCheese(curation, slot, c.sku);
            }
            closePicker();
        });
        list.appendChild(div);
    });
    document.getElementById('picker-overlay').classList.add('visible');
}

function closePicker() {
    document.getElementById('picker-overlay').classList.remove('visible');
}

async function assignCheese(curation, slot, cheese, x, y) {
    const resp = await fetch('/api/assign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ curation, slot, cheese }),
    });
    const data = await resp.json();
    if (!data.ok) {
        setMascot('alert', data.error || 'Assignment failed!');
        log(`Blocked: ${cheese} for ${curation} - ${data.error}`, 'red');
        flashRow(curation, false);
        return;
    }
    setMascot('happy', `${cheese} -> ${curation} ${slot.toUpperCase()}!`);
    log(`Assigned: ${cheese} -> ${curation} ${slot.toUpperCase()}`, 'green');
    flashRow(curation, true);
    addScore(1, x || window.innerWidth / 2, y || 100);
    loadAssignments();
    if (results.length) { rmfgLoaded ? calculateRMFG() : calculate(); }
    setTimeout(() => setMascot('idle', 'Ready to plan!'), 2500);
}

function flashRow(curation, success) {
    document.querySelectorAll('#assign-body tr').forEach(tr => {
        if (tr.dataset.cur === curation) {
            tr.classList.add(success ? 'flash-success' : 'flash-fail');
            setTimeout(() => tr.classList.remove('flash-success', 'flash-fail'), 600);
        }
    });
}

// ── Drag & Drop ──────────────────────────────────────────────────────
function setupDrag(row, sku) {
    const cell = row.querySelector('.sku-cell');
    if (!cell) return;
    cell.setAttribute('draggable', 'true');
    cell.addEventListener('dragstart', e => {
        dragSku = sku;
        e.dataTransfer.setData('text/plain', sku);
        document.getElementById('drag-ghost').textContent = sku;
        document.getElementById('drag-ghost').style.display = 'block';
        const img = new Image();
        img.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
        e.dataTransfer.setDragImage(img, 0, 0);
    });
    cell.addEventListener('drag', e => {
        const g = document.getElementById('drag-ghost');
        if (e.clientX > 0) { g.style.left = (e.clientX + 12) + 'px'; g.style.top = (e.clientY - 10) + 'px'; }
    });
    cell.addEventListener('dragend', () => {
        document.getElementById('drag-ghost').style.display = 'none';
        dragSku = null;
        document.querySelectorAll('.drop-hover').forEach(el => el.classList.remove('drop-hover'));
    });
}

// ── Calculate ────────────────────────────────────────────────────────
async function calculate() {
    setMascot('loading', 'Nom nom... crunching numbers...');
    log('Calculating Saturday NET...', '');

    const data = await api('/api/calculate', { method: 'POST' });
    results = data.results;
    weeksData = data.weeks || [];

    // Summary — animated counters
    animateCounter(document.getElementById('stat-skus'), data.total_skus);
    animateCounter(document.getElementById('stat-units'), data.total_units);
    animateCounter(document.getElementById('stat-shortages'), data.shortages);
    document.getElementById('stat-shortages').style.color =
        data.shortages > 0 ? 'var(--red)' : 'var(--green)';

    // Screen flash on shortages
    if (data.shortages > 0) {
        const flash = document.createElement('div');
        flash.className = 'screen-flash';
        document.body.appendChild(flash);
        setTimeout(() => flash.remove(), 500);
    }

    renderNetTable(results);
    renderWeekTabs(weeksData);
    updateProgress(results);

    // Assignment demands
    if (data.assign_demands) {
        for (const [cur, d] of Object.entries(data.assign_demands)) {
            const prEl = document.getElementById(`pr-qty-${cur}`);
            const ecEl = document.getElementById(`ec-qty-${cur}`);
            if (prEl) prEl.textContent = d.pr_qty || '';
            if (ecEl) ecEl.textContent = d.ec_qty || '';
        }
    }

    renderShelfLife(data.shelf_life || []);

    // Banner + mascot mood reflects situation
    const banner = document.getElementById('banner');
    const tightCount = results.filter(r => r.status === 'TIGHT').length;
    const worstShortage = results.filter(r => r.net < 0).sort((a,b) => a.net - b.net)[0];

    if (data.shortages >= 5) {
        setMascot('alert', `${data.shortages} shortages! We need help!`);
        banner.classList.remove('visible');
    } else if (data.shortages > 0) {
        const worstMsg = worstShortage ? ` Worst: ${worstShortage.sku} (${worstShortage.net})` : '';
        setMascot('worried', `${data.shortages} shortage${data.shortages > 1 ? 's' : ''}.${worstMsg}`);
        banner.classList.remove('visible');
    } else if (tightCount > 3) {
        setMascot('thinking', `No shortages but ${tightCount} SKUs are tight...`);
        banner.classList.remove('visible');
    } else if (data.shortages === 0 && data.total_skus > 0) {
        setMascot('celebrate', 'LEVEL CLEAR! No shortages!');
        banner.classList.add('visible');
        spawnConfetti();
    } else {
        setMascot('idle', 'Ready to plan!');
        banner.classList.remove('visible');
    }

    // Store mood for idle reminders
    mascot.currentMood = data.shortages >= 5 ? 'alert' : data.shortages > 0 ? 'worried' : tightCount > 3 ? 'thinking' : 'happy';

    log(`Done: ${data.total_skus} SKUs, ${data.shortages} shortages, ${data.total_units.toLocaleString()} units`,
        data.shortages === 0 ? 'green' : 'red');
}

function updateProgress(rows) {
    const chRows = rows.filter(r => r.sku && r.sku.startsWith('CH-') && r.total_demand > 0);
    const total = chRows.length;
    const ok = chRows.filter(r => r.net >= 0).length;
    const short = total - ok;
    const pct = total > 0 ? Math.round((ok / total) * 100) : 0;

    const val = document.getElementById('progress-value');
    const fill = document.getElementById('progress-fill');
    const detail = document.getElementById('progress-detail');
    if (val) val.textContent = `${pct}%`;
    if (fill) fill.style.width = `${pct}%`;
    if (detail) {
        if (short === 0) {
            detail.textContent = `All ${total} SKUs covered`;
            detail.style.color = 'var(--green)';
        } else {
            detail.textContent = `${ok}/${total} SKUs OK  //  ${short} shortage${short > 1 ? 's' : ''} remaining`;
            detail.style.color = 'var(--fg3)';
        }
    }
    if (val) val.style.color = pct === 100 ? 'var(--green)' : pct > 80 ? 'var(--yellow)' : 'var(--red)';
}

function headroomBar(net, total) {
    if (total === 0) return '<span class="headroom-bar" style="opacity:0.3">---</span>';
    if (net < 0) {
        const pct = Math.min(100, Math.abs(net) / total * 100);
        return `<span class="headroom-bar headroom-neg"><span class="headroom-fill" style="width:${pct}%;background:var(--red)"></span><span class="headroom-label">-${Math.abs(net)}</span></span>`;
    }
    const pct = Math.min(100, net / (total * 2) * 100);
    const color = pct < 20 ? 'var(--yellow)' : 'var(--green)';
    return `<span class="headroom-bar"><span class="headroom-fill" style="width:${pct}%;background:${color}"></span><span class="headroom-label">+${net}</span></span>`;
}

function netCellClass(net, available) {
    if (net < -50) return 'net-cell-critical';
    if (net < 0) return 'net-cell-bad';
    if (available > 0 && net < available * 0.1) return 'net-cell-warn';
    if (net > available * 0.5) return 'net-cell-great';
    if (net > 0) return 'net-cell-good';
    return 'net-cell-ok';
}

// Animated number counter
function animateCounter(el, target) {
    const start = parseInt(el.textContent) || 0;
    if (start === target) return;
    const diff = target - start;
    const duration = 400;
    const startTime = performance.now();
    function step(now) {
        const t = Math.min(1, (now - startTime) / duration);
        const ease = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
        el.textContent = Math.round(start + diff * ease).toLocaleString();
        if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
}

// Click-to-copy SKU
function setupCopyClick(cell, sku) {
    cell.addEventListener('click', e => {
        if (e.detail > 1) return; // ignore double-click
        navigator.clipboard.writeText(sku).then(() => {
            cell.classList.add('copied');
            setTimeout(() => cell.classList.remove('copied'), 400);
            const toast = document.createElement('div');
            toast.className = 'copy-toast';
            toast.textContent = 'Copied!';
            toast.style.left = e.clientX + 'px';
            toast.style.top = (e.clientY - 20) + 'px';
            document.body.appendChild(toast);
            setTimeout(() => toast.remove(), 800);
        });
    });
}

function renderNetTable(data) {
    const filter = document.getElementById('filter-select').value;
    const tbody = document.getElementById('net-body');
    const thead = document.querySelector('#net-table thead tr');
    tbody.innerHTML = '';

    // Adapt columns based on data mode
    const isRMFG = data.length > 0 && data[0].sat_demand !== undefined;
    if (isRMFG) {
        thead.innerHTML = `
            <th onclick="sortTable('sku')" data-col="sku">SKU</th>
            <th onclick="sortTable('available')" data-col="available" class="num">Avail</th>
            <th onclick="sortTable('potential')" data-col="potential" class="num">Pot.</th>
            <th onclick="sortTable('sat_demand')" data-col="sat_demand" class="num">Sat Dmd</th>
            <th onclick="sortTable('tue_demand')" data-col="tue_demand" class="num">Tue</th>
            <th onclick="sortTable('next_sat_demand')" data-col="next_sat_demand" class="num">Next Sat</th>
            <th onclick="sortTable('total_demand')" data-col="total_demand" class="num">Total</th>
            <th onclick="sortTable('net')" data-col="net" class="num">NET</th>
            <th>Headroom</th>
            <th onclick="sortTable('status')" data-col="status">Status</th>
        `;
    } else {
        thead.innerHTML = `
            <th onclick="sortTable('sku')" data-col="sku">SKU</th>
            <th onclick="sortTable('available')" data-col="available" class="num">Avail</th>
            <th onclick="sortTable('direct')" data-col="direct" class="num">Direct</th>
            <th onclick="sortTable('prcjam')" data-col="prcjam" class="num">PRCJAM</th>
            <th onclick="sortTable('cexec')" data-col="cexec" class="num">CEXEC</th>
            <th onclick="sortTable('exec')" data-col="exec" class="num">EXEC</th>
            <th onclick="sortTable('total_demand')" data-col="total_demand" class="num">Total</th>
            <th onclick="sortTable('net')" data-col="net" class="num">NET</th>
            <th>Headroom</th>
            <th onclick="sortTable('status')" data-col="status">Status</th>
        `;
    }
    updateSortIndicators();

    const filtered = data.filter(r => {
        if (r.status === 'NO DEMAND' && r.available === 0) return false;
        if (filter === 'CH-*' && !r.sku.startsWith('CH-')) return false;
        if (filter === 'Shortages' && !['SHORTAGE','MFG'].includes(r.status)) return false;
        if (filter === 'Tight' && !['SHORTAGE','MFG','TIGHT'].includes(r.status)) return false;
        if (filter === 'Surplus' && r.status !== 'SURPLUS') return false;
        return true;
    });

    filtered.forEach(r => {
        const cls = { SHORTAGE:'shortage', TIGHT:'tight', SURPLUS:'surplus', 'NO DEMAND':'no-demand' }[r.status] || 'ok';
        const tr = document.createElement('tr');
        tr.className = cls;

        // Tooltip
        const tipParts = [`${r.sku}: ${r.available} avail`];
        if (isRMFG) {
            if (r.potential > 0) tipParts.push(`Potential: +${r.potential} (${r.wheel_count} wheels)`);
            tipParts.push(`Sat: ${r.sat_demand}, Tue: ${r.tue_demand || 0}, Next Sat: ${r.next_sat_demand || 0}`);
            tipParts.push(`NET Sat: ${r.net_sat >= 0 ? '+' : ''}${r.net_sat}`);
            if (r.net_final !== undefined) tipParts.push(`NET Final: ${r.net_final >= 0 ? '+' : ''}${r.net_final}`);
        } else {
            tipParts.push(`${r.direct} direct, ${r.prcjam} PR, ${r.cexec} CEX, ${r.exec} EX`);
        }
        tipParts.push(`NET: ${r.net >= 0 ? '+' : ''}${r.net}`);
        tr.title = tipParts.join(' | ');

        const netClass = netCellClass(r.net, r.available);
        if (isRMFG) {
            const potCell = r.potential > 0
                ? `<span style="color:var(--blue)" title="${r.wheel_count} wheels">+${r.potential}</span>`
                : '-';
            tr.innerHTML = `
                <td class="sku-cell">${r.sku} ${renderSparkline(r.sku)}</td>
                <td class="num">${r.available}</td>
                <td class="num">${potCell}</td>
                <td class="num">${r.sat_demand}</td>
                <td class="num">${r.tue_demand || 0}</td>
                <td class="num">${r.next_sat_demand || 0}</td>
                <td class="num">${r.total_demand}</td>
                <td class="num net-cell ${netClass}">${r.net >= 0 ? '+' : ''}${r.net}</td>
                <td>${headroomBar(r.net, r.total_demand)}</td>
                <td><span class="status-badge status-${r.status.replace(/\s+/g, '-')}">${r.status}</span></td>
            `;
        } else {
            tr.innerHTML = `
                <td class="sku-cell">${r.sku} ${renderSparkline(r.sku)}</td>
                <td class="num">${r.available}</td>
                <td class="num">${r.direct}</td>
                <td class="num">${r.prcjam}</td>
                <td class="num">${r.cexec}</td>
                <td class="num">${r.exec}</td>
                <td class="num">${r.total_demand}</td>
                <td class="num net-cell ${netClass}">${r.net >= 0 ? '+' : ''}${r.net}</td>
                <td>${headroomBar(r.net, r.total_demand)}</td>
                <td><span class="status-badge status-${r.status.replace(/\s+/g, '-')}">${r.status}</span></td>
            `;
        }
        setupDrag(tr, r.sku);
        setupCopyClick(tr.querySelector('.sku-cell'), r.sku);
        tbody.appendChild(tr);
    });
}

function renderWeekTabs(weeks) {
    weeks.forEach(w => {
        const tbody = document.getElementById(`week-${w.week}-body`);
        if (!tbody) return;
        tbody.innerHTML = '';
        w.results.forEach(r => {
            if (r.demand === 0 && r.carry_fwd === 0) return;
            const cls = { 'PLAN PO':'shortage', TIGHT:'tight', 'NO DEMAND':'no-demand' }[r.status] || 'ok';
            const statusCls = r.status.replace(/\s+/g, '-');
            const tr = document.createElement('tr');
            tr.className = cls;
            tr.innerHTML = `
                <td>${r.sku}</td>
                <td class="num">${r.carry_fwd}</td>
                <td class="num">${r.demand}</td>
                <td class="num net-cell">${r.net >= 0 ? '+' : ''}${r.net}</td>
                <td><span class="status-badge status-${statusCls}">${r.status}</span></td>
            `;
            tbody.appendChild(tr);
        });
    });
}

function renderShelfLife(items) {
    const tbody = document.getElementById('shelf-body');
    tbody.innerHTML = '';
    items.filter(s => s.qty > 0).forEach(s => {
        const cls = s.days_left < 0 ? 'shelf-expired' : s.days_left <= 7 ? 'shelf-expired' : 'shelf-expiring';
        const tr = document.createElement('tr');
        tr.className = cls;
        tr.innerHTML = `<td>${s.sku}</td><td>${s.days_left}d</td><td>${s.qty}</td><td>${s.action}</td>`;
        tbody.appendChild(tr);
    });
}

// ── Sorting ──────────────────────────────────────────────────────────
function sortTable(col) {
    if (sortCol === col) sortAsc = !sortAsc;
    else { sortCol = col; sortAsc = true; }
    const numeric = ['available', 'direct', 'prcjam', 'cexec', 'exec', 'total_demand', 'net', 'sat_demand', 'tue_demand', 'next_sat_demand'];
    if (numeric.includes(col)) {
        results.sort((a, b) => sortAsc ? a[col] - b[col] : b[col] - a[col]);
    } else {
        results.sort((a, b) => sortAsc
            ? (a[col]||'').toString().localeCompare((b[col]||'').toString())
            : (b[col]||'').toString().localeCompare((a[col]||'').toString()));
    }
    renderNetTable(results);
    updateSortIndicators();
}

function updateSortIndicators() {
    document.querySelectorAll('#net-table th[data-col]').forEach(th => {
        const col = th.dataset.col;
        // Strip old indicator
        th.textContent = th.textContent.replace(/ [▲▼]$/, '');
        th.classList.remove('sorted');
        if (col === sortCol) {
            th.textContent += sortAsc ? ' ▲' : ' ▼';
            th.classList.add('sorted');
        }
    });
}

function applyFilter() { renderNetTable(results); }

// ── Tabs ─────────────────────────────────────────────────────────────
function switchTab(tabId, btn) {
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById(`tab-${tabId}`).classList.add('active');
    btn.classList.add('active');
}

// ── Import / Export / Auto / Fixes / Wed PO / Variety ────────────────
function importCSV() { document.getElementById('csv-input').click(); }

async function handleCSVFile(input) {
    if (!input.files.length) return;
    setMascot('loading', 'Importing CSV...');
    const form = new FormData();
    form.append('file', input.files[0]);
    try {
        const data = await api('/api/import_csv', { method: 'POST', body: form });
        if (data.error) {
            setMascot('alert', data.error);
            log(`Import error: ${data.error}`, 'red');
        } else {
            setMascot('happy', `Imported ${data.rows} rows, ${data.skus} cheeses`);
            log(`CSV imported: ${data.rows} rows, ${data.skus} CH-* SKUs, ${data.units} units`, 'green');
            addScore(1, window.innerWidth / 2, 80);
        }
    } catch (e) {
        setMascot('alert', 'Import failed!');
        log(`Import error: ${e}`, 'red');
    }
    input.value = '';
}

function exportCSV() {
    window.open('/api/export_csv', '_blank');
    log('Exporting CSV...', 'cyan');
}

async function autoAssign() {
    setMascot('thinking', 'Auto-assigning...');
    log('Running auto-assign...', '');
    const data = await api('/api/auto_assign', { method: 'POST' });
    if (data.count === 0) {
        setMascot('happy', 'Already optimal!');
        log('Auto-assign: no changes needed', 'green');
        return;
    }
    data.changes.forEach(c => log(`  ${c}`, 'cyan'));
    addScore(data.count, window.innerWidth / 2, 80);
    setMascot('happy', `${data.count} assignments updated!`);
    log(`Auto-assign: ${data.count} changes applied`, 'green');
    loadAssignments();
    if (results.length) { rmfgLoaded ? calculateRMFG() : calculate(); }
    setTimeout(() => setMascot('idle'), 3000);
}

async function suggestFixes() {
    const data = await api('/api/suggest_fixes');
    if (data.length === 0) {
        setMascot('happy', 'No shortages!');
        log('No shortages to fix!', 'green');
        return;
    }
    log(`--- Shortage Suggestions (${data.length}) ---`, 'red');
    data.forEach(s => log(`  ${s.sku}: NEED ${s.deficit} | ${s.fixes.join(' | ')}`, 'yellow'));
    setMascot('worried', `${data.length} shortages need attention`);
}

async function wedPO() {
    setMascot('loading', 'Generating Wednesday PO...');
    const data = await api('/api/wed_po');
    if (data.length === 0) {
        log('No shortages for Wed PO.', 'green');
        setMascot('happy', 'All covered!');
        return;
    }
    log('--- Wednesday PO ---', 'cyan');
    let totalUnits = 0;
    data.forEach(p => {
        totalUnits += p.order_qty;
        log(`  ${p.sku}: need ${p.deficit}, order ${p.order_qty} (${p.cases}x${p.case_qty}) from ${p.vendor}`, 'cyan');
    });
    log(`Total: ${data.length} lines, ${totalUnits} units`, 'green');
    setMascot('happy', `${data.length} PO lines, ${totalUnits} units`);
    addScore(1, window.innerWidth / 2, 80);
}

async function varietyCheck() {
    const data = await api('/api/variety_check');
    if (data.length === 0) { log('Variety: all clear!', 'green'); setMascot('happy', 'Good variety!'); return; }
    log(`--- Variety Issues (${data.length}) ---`, 'yellow');
    data.forEach(i => log(`  ${i}`, 'yellow'));
    setMascot('alert', `${data.length} variety issues!`);
}

// ── Consolidated Order List ──────────────────────────────────────────
async function showOrderList() {
    setMascot('loading', 'Building order list...');
    const data = await api('/api/order_list');
    if (!data || data.length === 0) {
        log('No items to order!', 'green');
        setMascot('happy', 'Nothing to order!');
        return;
    }

    const drawer = document.getElementById('order-drawer');
    const body = document.getElementById('order-drawer-body');

    let html = '<table class="net-table" style="width:100%">';
    html += '<thead><tr><th>Type</th><th>SKU</th><th>Name</th><th class="num">Demand</th><th class="num">Avail</th><th class="num">Deficit</th><th class="num">Order</th><th>Vendor</th></tr></thead><tbody>';

    let lastCat = '';
    let totalOrder = 0;
    data.forEach(r => {
        if (r.category !== lastCat) {
            html += `<tr class="cat-divider"><td colspan="8" style="background:var(--bg3);color:var(--accent);font-weight:700;padding:6px 10px;font-size:11px;text-transform:uppercase;letter-spacing:2px">${r.category}</td></tr>`;
            lastCat = r.category;
        }
        totalOrder += r.order_qty;
        html += `<tr>
            <td style="font-size:10px;color:var(--fg3)">${r.category}</td>
            <td>${r.sku}</td>
            <td style="color:var(--fg2);font-size:11px">${r.name || ''}</td>
            <td class="num">${r.demand}</td>
            <td class="num">${r.avail}</td>
            <td class="num" style="color:var(--red)">${r.deficit}</td>
            <td class="num" style="color:var(--accent);font-weight:700">${r.order_qty}</td>
            <td style="font-size:10px">${r.vendor || ''}</td>
        </tr>`;
    });
    html += `<tr style="border-top:2px solid var(--accent)"><td colspan="6" style="text-align:right;font-weight:700;color:var(--fg)">TOTAL</td><td class="num" style="color:var(--accent);font-weight:700">${totalOrder}</td><td></td></tr>`;
    html += '</tbody></table>';

    body.innerHTML = html;
    closeDrawer('po-drawer');
    closeDrawer('mfg-drawer');
    drawer.classList.add('visible');

    log(`Order list: ${data.length} items, ${totalOrder} total units`, 'green');
    setMascot('happy', `${data.length} items to order`);
}

function exportOrderCSV() {
    window.location.href = '/api/order_list_csv';
}

// ══════════════════════════════════════════════════════════════════════
//  RMFG AUTOMATION — Load folder, calculate, substitutions, run all
// ══════════════════════════════════════════════════════════════════════

let rmfgFolder = '';

async function autoLoadRMFG() {
    try {
        const folders = await api('/api/rmfg_folders');
        if (folders.length > 0) {
            rmfgFolder = folders[folders.length - 1].name;
            log(`Found RMFG folder: ${rmfgFolder}`, 'cyan');
            setMascot('thinking', `Found ${rmfgFolder}. Click Run All!`);
        }
    } catch (e) { /* ignore */ }
}

async function loadFolder() {
    setMascot('thinking', 'Looking for RMFG folders...');
    const folders = await api('/api/rmfg_folders');
    if (folders.length === 0) {
        setMascot('alert', 'No RMFG_* folders found!');
        log('No RMFG folders found in project directory', 'red');
        return;
    }

    // If only one folder, use it directly
    if (folders.length === 1) {
        rmfgFolder = folders[0].name;
        await doLoadFolder(rmfgFolder);
        return;
    }

    // Show picker in log
    log('--- Available RMFG Folders ---', 'cyan');
    folders.forEach((f, i) => {
        log(`  [${i + 1}] ${f.name} (${f.files_found}/${f.total_files} files detected)`, '');
    });

    // Use most recent (last in sorted list)
    rmfgFolder = folders[folders.length - 1].name;
    log(`Auto-selecting: ${rmfgFolder}`, 'cyan');
    await doLoadFolder(rmfgFolder);
}

async function doLoadFolder(folder) {
    setMascot('loading', 'Loading RMFG data...');
    log(`Loading folder: ${folder}...`, '');

    const data = await api('/api/load_rmfg', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder }),
    });

    if (data.error) {
        setMascot('alert', data.error);
        log(`Load error: ${data.error}`, 'red');
        return false;
    }

    // Log results
    data.log.forEach(l => log(`  ${l}`, 'green'));
    data.warnings.forEach(w => log(`  WARNING: ${w}`, 'yellow'));

    // Show detected files
    log('Files detected:', 'cyan');
    Object.entries(data.files).forEach(([key, val]) => {
        const icon = val ? '\u2713' : '\u2717';
        const color = val ? 'green' : 'yellow';
        log(`  ${icon} ${key}: ${val || 'not found'}`, color);
    });

    rmfgLoaded = true;
    setMascot('happy', `Loaded! ${data.cheese_count} cheeses, ${data.sat_units} demand units`);
    addScore(2, window.innerWidth / 2, 80);
    // Refresh assignments (they may have been updated by load)
    loadAssignments();
    return true;
}

async function calculateRMFG() {
    setMascot('loading', 'Crunching Saturday NETs...');
    log('Calculating multi-window NET...', '');

    const data = await api('/api/calculate_rmfg', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({demand_mode: demandMode}),
    });

    if (data.error) {
        // Fallback to legacy calculate
        log(`RMFG: ${data.error}. Using legacy calculate.`, 'yellow');
        return calculate();
    }

    results = data.results;
    weeksData = data.weeks || [];

    // Summary — animated counters
    animateCounter(document.getElementById('stat-skus'), data.total_skus);
    animateCounter(document.getElementById('stat-units'), data.total_units);
    animateCounter(document.getElementById('stat-shortages'), data.shortages);
    document.getElementById('stat-shortages').style.color =
        data.shortages > 0 ? 'var(--red)' : 'var(--green)';

    // Screen flash on shortages
    if (data.shortages > 0) {
        const flash = document.createElement('div');
        flash.className = 'screen-flash';
        document.body.appendChild(flash);
        setTimeout(() => flash.remove(), 500);
    }

    renderNetTable(results);
    renderWeekTabs(weeksData);
    updateProgress(results);

    // PR-CJAM / CEX-EC counts in log
    if (data.prcjam_counts && Object.keys(data.prcjam_counts).length) {
        log('PR-CJAM assignments:', 'cyan');
        Object.entries(data.prcjam_counts).forEach(([k, v]) => {
            log(`  PR-CJAM-${k}: ${v}`, '');
        });
    }
    if (data.cexec_counts && Object.keys(data.cexec_counts).length) {
        log('CEX-EC assignments:', 'cyan');
        Object.entries(data.cexec_counts).forEach(([k, v]) => {
            const label = k === 'BARE' ? '(bare - skipped)' : k;
            log(`  CEX-EC-${label}: ${v}`, k === 'BARE' ? 'yellow' : '');
        });
    }

    renderShelfLife(data.shelf_life || []);

    // Churn mode indicator
    if (data.churn_info) {
        const ci = data.churn_info;
        const modeBtn = document.getElementById('demand-mode-btn');
        if (modeBtn) {
            modeBtn.textContent = ci.mode === 'churned' ? 'CHURNED' : 'DISCRETE';
            modeBtn.style.borderColor = ci.mode === 'churned' ? 'var(--orange)' : 'var(--accent)';
        }
        if (ci.mode === 'churned' && ci.sat_reduction > 0) {
            log(`Churn applied: Sat -${ci.sat_reduction}, Tue -${ci.tue_reduction}, Next -${ci.next_reduction}`, 'orange');
        }
    }

    // Banner + mascot mood
    const banner = document.getElementById('banner');
    const tightCount = results.filter(r => r.status === 'TIGHT').length;
    const worstShortage = results.filter(r => r.net < 0).sort((a, b) => a.net - b.net)[0];

    if (data.shortages >= 5) {
        setMascot('alert', `${data.shortages} shortages! We need help!`);
        banner.classList.remove('visible');
    } else if (data.shortages > 0) {
        const worstMsg = worstShortage ? ` Worst: ${worstShortage.sku} (${worstShortage.net})` : '';
        setMascot('worried', `${data.shortages} shortage${data.shortages > 1 ? 's' : ''}.${worstMsg}`);
        banner.classList.remove('visible');
    } else if (tightCount > 3) {
        setMascot('thinking', `No shortages but ${tightCount} SKUs are tight...`);
        banner.classList.remove('visible');
    } else if (data.shortages === 0 && data.total_skus > 0) {
        setMascot('celebrate', 'LEVEL CLEAR! No shortages!');
        banner.classList.add('visible');
        spawnConfetti();
    } else {
        setMascot('idle', 'Ready to plan!');
        banner.classList.remove('visible');
    }

    mascot.currentMood = data.shortages >= 5 ? 'alert' : data.shortages > 0 ? 'worried' : tightCount > 3 ? 'thinking' : 'happy';

    log(`Done: ${data.total_skus} SKUs, ${data.shortages} shortages, ${data.total_units.toLocaleString()} units`,
        data.shortages === 0 ? 'green' : 'red');
}

async function syncDropbox() {
    setMascot('loading', 'Syncing from Dropbox...');
    log('Fetching latest inventory from Dropbox...', 'cyan');

    const data = await api('/api/dropbox_sync', { method: 'POST' });
    if (data.error) {
        setMascot('alert', 'Dropbox sync failed');
        log(`Dropbox error: ${data.error}`, 'red');
        return false;
    }

    log(`Dropbox: ${data.file} (${data.modified})`, 'green');
    log(`  ${data.inventory_count} SKUs (${data.cheese_count} cheese)`, 'green');
    if (data.wheel_skus > 0) {
        log(`  ${data.wheel_skus} wheel SKUs, +${data.potential_yield.toLocaleString()} potential yield`, 'blue');
    }
    rmfgLoaded = true;
    lastDropboxSync = Date.now();
    setMascot('happy', `Loaded ${data.cheese_count} cheeses from Dropbox`);
    return true;
}

async function syncRecharge(opts = {}) {
    const force = opts.force || false;
    setMascot('loading', 'Pulling from Recharge...');
    log(force ? 'Force-fetching from Recharge API...' : 'Syncing Recharge (cached if fresh)...', 'cyan');

    const data = await api('/api/recharge_sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force }),
    });
    if (data.error) {
        setMascot('alert', 'Recharge sync failed');
        log(`Recharge error: ${data.error}`, 'red');
        return false;
    }

    if (data.from_cache) {
        const mins = Math.round((data.cache_age_seconds || 0) / 60);
        log(`Recharge: ${data.total_charges} charges (cached, ${mins}m old)`, 'green');
    } else {
        const rcTime = data.api_seconds ? ` (${data.api_seconds}s, ${data.api_pages} pages)` : '';
        log(`Recharge: ${data.total_charges} charges across ${data.months.join(', ')}${rcTime}`, 'green');
    }
    if (data.weeks && !data.from_cache) {
        data.weeks.forEach(w => {
            log(`  ${w.label} (${w.date}): ${w.skus} SKUs, ${w.units} units`, 'green');
        });
    }
    log(`  Total cheese demand: ${data.cheese_demand_units} units`, 'green');
    lastRechargeSync = Date.now();
    setMascot('happy', `Loaded ${data.total_charges} charges`);
    return true;
}

async function syncShopify() {
    setMascot('loading', 'Pulling Shopify orders...');
    log('Fetching unfulfilled Shopify orders...', 'cyan');

    const data = await api('/api/shopify_sync', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({force: true})
    });
    if (data.error) {
        setMascot('alert', 'Shopify sync failed');
        log(`Shopify error: ${data.error}`, 'red');
        return false;
    }

    const shTime = data.api_seconds ? ` (${data.api_seconds}s, ${data.api_pages} pages)` : '';
    log(`Shopify: ${data.orders_analyzed || data.orders} orders, ${data.skus} SKUs${shTime}`, 'green');
    lastShopifySync = Date.now();
    setMascot('happy', `${data.orders} Shopify orders loaded`);
    return true;
}

async function runAll() {
    switchView('dashboard');  // Show log panel during pipeline
    setMascot('loading', 'Running full pipeline...');
    log('=== RUN ALL ===', 'cyan');

    // 1. Inventory: Dropbox > RMFG folder > settings
    const dbStatus = await api('/api/dropbox_status');
    let invLoaded = false;

    if (dbStatus.configured) {
        log('Dropbox configured — syncing inventory...', 'cyan');
        invLoaded = await syncDropbox();
    }

    if (!invLoaded) {
        // Use settings inventory as fallback
        log('Using settings inventory (no Dropbox)', 'yellow');
        await api('/api/load_settings_inventory', { method: 'POST' });
        invLoaded = true;
    }

    // 2. Demand: pull from Recharge
    const rcStatus = await api('/api/recharge_status');
    let demandLoaded = false;
    if (rcStatus.configured) {
        log('Pulling demand from Recharge...', 'cyan');
        demandLoaded = await syncRecharge({force: true});
    }

    if (!demandLoaded) {
        log('No Recharge data — using Shopify demand from settings', 'yellow');
    }

    // 2b. Shopify orders (adds to Recharge demand)
    const shStatus = await api('/api/shopify_status');
    if (shStatus.configured) {
        log('Pulling Shopify orders...', 'cyan');
        await syncShopify();
    }

    // 3a. Fetch depletion files from email
    log('Fetching depletion files from email...', 'cyan');
    const emailDep = await api('/api/fetch_depletions_email', { method: 'POST' });
    if (emailDep.ok && emailDep.files && emailDep.files.length > 0) {
        log(`Email: downloaded ${emailDep.files.length} depletion file(s): ${emailDep.files.join(', ')}`, 'green');
    } else if (emailDep.error) {
        log(`Email depletion fetch: ${emailDep.error}`, 'yellow');
    }

    // 3b. Apply shipment depletion (auto-find WeeklyProductionQuery XLSX)
    log('Checking for shipment depletion file...', 'cyan');
    const depResult = await api('/api/auto_deplete', { method: 'POST' });
    if (depResult.ok) {
        log(`Depletion applied: ${depResult.file} — ${depResult.total_depleted} units across ${depResult.skus_affected} SKUs (${depResult.order_count} orders)`, 'green');
        if (depResult.unmatched && depResult.unmatched.length > 0) {
            log(`  Unmatched products: ${depResult.unmatched.join(', ')}`, 'yellow');
        }
    } else if (depResult.skipped) {
        log(`Depletion skipped: ${depResult.reason}`, 'yellow');
    } else if (depResult.error) {
        log(`Depletion error: ${depResult.error}`, 'red');
    }

    // 4. Calculate (after depletion so numbers reflect post-shipment inventory)
    await calculateRMFG();

    // 4b. Show substitutions if there are shortages
    const shortages = results.filter(r => r.status === 'SHORTAGE');
    if (shortages.length > 0) {
        await showSubstitutions();
    }

    // 4. Load assignments + global extras
    await loadAssignments();
    await loadGlobalExtras();

    // 5. Build action calendar
    await loadCalendar();

    // Update last-sync timestamp
    const syncEl = document.getElementById('last-sync');
    if (syncEl) {
        const now = new Date();
        const h = now.getHours() % 12 || 12;
        const m = String(now.getMinutes()).padStart(2, '0');
        const ap = now.getHours() >= 12 ? 'pm' : 'am';
        syncEl.textContent = `synced ${h}:${m}${ap}`;
    }

    // Load SKU history for sparklines
    await loadSkuHistory();

    // Load morning briefing
    await loadBriefing();

    // Pre-load cut order data so it's ready when user clicks the tab
    await loadCutOrderInteractive();

    // Auto-switch to runway view and load it
    switchView('runway');
    log('=== RUN ALL COMPLETE ===', 'green');
}

async function showSubstitutions() {
    setMascot('thinking', 'Finding substitutes...');
    const data = await api('/api/substitutions');
    if (data.length === 0) {
        log('No shortages to substitute.', 'green');
        setMascot('happy', 'No shortages!');
        return;
    }

    // Build visual panel
    const list = document.getElementById('subs-list');
    list.innerHTML = '';
    data.forEach(s => {
        const div = document.createElement('div');
        div.className = 'sub-shortage';
        const potInfo = s.potential > 0
            ? ` <span style="color:var(--blue);font-size:10px" title="${s.wheel_count} wheels available">| POT +${s.potential} (${s.wheel_count} wh) = NET ${s.net_with_potential >= 0 ? '+' : ''}${s.net_with_potential}</span>`
            : '';
        let inner = `
            <div class="sub-shortage-header">
                <span class="sub-shortage-sku">${s.sku}</span>
                <span class="sub-shortage-info">SHORT ${s.deficit} (avail ${s.available}, demand ${s.demand})${potInfo}</span>
            </div>
        `;
        // If wheels cover the deficit, show MFG note
        if (s.potential > 0 && s.net_with_potential >= 0) {
            inner += `<div style="padding:2px 8px;font-family:'Space Mono',monospace;font-size:10px;color:var(--blue)">MFG: Cut ${Math.ceil(s.deficit / (s.potential / s.wheel_count))} wheels to cover deficit</div>`;
        }
        if (s.substitutes.length === 0 && !(s.potential > 0 && s.net_with_potential >= 0)) {
            inner += '<div class="sub-none">No good substitutes found</div>';
        } else {
            s.substitutes.forEach(sub => {
                const tagClass = sub.covers_all ? 'sub-full' : 'sub-partial';
                const tagText = sub.covers_all ? 'FULL' : 'PARTIAL';
                const noDemand = sub.no_demand ? ' (unused)' : '';
                inner += `
                    <div class="sub-item" style="display:flex;align-items:center">
                        <span class="sub-item-sku">${sub.sku}</span>
                        <span class="sub-item-info" style="flex:1">headroom ${sub.headroom}, covers ${sub.can_cover}${noDemand}</span>
                        <span class="sub-item-tag ${tagClass}">${tagText}</span>
                        <button class="btn btn-orange btn-sm" style="margin-left:6px;font-size:10px;padding:2px 8px"
                                onclick="swapPreview('${s.sku}','${sub.sku}')">Swap</button>
                    </div>
                `;
            });
        }
        div.innerHTML = inner;
        list.appendChild(div);
    });

    document.getElementById('subs-overlay').classList.add('visible');
    log(`${data.length} shortages with substitution options`, 'cyan');
    setMascot('thinking', `${data.length} shortages need subs`);
}

async function swapPreview(oldSku, newSku) {
    log(`Previewing swap: ${oldSku} → ${newSku}...`, 'cyan');
    setMascot('thinking', 'Finding swap targets...');

    const data = await api('/api/swap_preview', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({old_sku: oldSku, new_sku: newSku}),
    });

    if (!data || data.error) {
        log(`Swap preview error: ${data?.error || 'unknown'}`, 'red');
        setMascot('alert', data?.error || 'Preview failed');
        return;
    }

    const total = data.total || 0;
    if (total === 0) {
        log(`No orders to swap for ${oldSku}`, 'yellow');
        setMascot('idle', 'No orders need swapping.');
        return;
    }

    // Show confirmation dialog
    const targets = data.targets || [];
    let confirmHtml = `
        <div style="padding:16px;max-width:500px">
            <div style="font-family:'Space Mono',monospace;font-size:14px;font-weight:600;color:var(--accent);margin-bottom:12px">
                Swap ${oldSku} → ${newSku}
            </div>
            <div style="font-size:12px;color:var(--fg2);margin-bottom:8px">
                Ship tag: <span style="color:var(--fg)">${data.ship_tag}</span>
            </div>
            <div style="font-family:'Rajdhani',sans-serif;font-size:24px;font-weight:600;color:var(--orange);margin-bottom:12px">
                ${total} orders
            </div>
            <div style="max-height:200px;overflow-y:auto;margin-bottom:16px;border:1px solid var(--border);border-radius:4px">
    `;
    targets.forEach(t => {
        confirmHtml += `<div style="padding:4px 8px;border-bottom:1px solid var(--border);font-family:'Space Mono',monospace;font-size:10px">${t.order_name} (qty ${t.qty})</div>`;
    });
    confirmHtml += `</div>
            <div style="display:flex;gap:8px">
                <button class="btn btn-orange" onclick="swapExecute('${oldSku}','${newSku}','${data.ship_tag}','${data.new_variant_gid}')">Execute Swap</button>
                <button class="btn btn-dim" onclick="closeSwapConfirm()">Cancel</button>
            </div>
        </div>`;

    // Use subs overlay for the confirmation
    const list = document.getElementById('subs-list');
    list.innerHTML = confirmHtml;
    setMascot('thinking', `${total} orders ready to swap`);
}

async function swapExecute(oldSku, newSku, shipTag, newVariantGid) {
    log(`Executing swap: ${oldSku} → ${newSku} on ship ${shipTag}`, 'orange');
    setMascot('loading', 'Swapping orders...');

    const data = await api('/api/swap_execute', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            old_sku: oldSku,
            new_sku: newSku,
            ship_tag: shipTag,
            new_variant_gid: newVariantGid,
        }),
    });

    if (!data || data.error) {
        log(`Swap start error: ${data?.error || 'unknown'}`, 'red');
        setMascot('alert', 'Swap failed to start');
        return;
    }

    // Poll for progress
    const list = document.getElementById('subs-list');
    while (true) {
        await new Promise(r => setTimeout(r, 1000));
        const status = await api('/api/swap_progress');
        if (!status) break;

        list.innerHTML = `
            <div style="padding:16px">
                <div style="font-family:'Space Mono',monospace;font-size:14px;font-weight:600;color:var(--accent);margin-bottom:8px">
                    Swap: ${oldSku} → ${newSku}
                </div>
                <div style="font-size:12px;color:var(--fg)">${status.message || 'Working...'}</div>
                <div style="margin-top:12px">
                    <button class="btn btn-dim btn-sm" onclick="cancelSwap()">Cancel</button>
                </div>
            </div>
        `;

        if (!status.running) {
            const result = status.result;
            if (result && result.error) {
                log(`Swap error: ${result.error}`, 'red');
                setMascot('alert', 'Swap failed');
            } else if (result) {
                const msg = `Swap complete: ${result.success} ok, ${result.failed} failed`;
                log(msg, result.failed > 0 ? 'orange' : 'green');
                setMascot(result.failed > 0 ? 'alert' : 'happy', msg);

                list.innerHTML = `
                    <div style="padding:16px">
                        <div style="font-family:'Space Mono',monospace;font-size:14px;font-weight:600;color:var(--green);margin-bottom:12px">
                            Swap Complete
                        </div>
                        <div style="font-family:'Rajdhani',sans-serif;font-size:20px">
                            <span style="color:var(--green)">${result.success} swapped</span>
                            ${result.failed > 0 ? `<span style="color:var(--red);margin-left:12px">${result.failed} failed</span>` : ''}
                        </div>
                        ${result.errors.length > 0 ? `<div style="margin-top:8px;font-size:10px;color:var(--red)">${result.errors.join('<br>')}</div>` : ''}
                        <div style="margin-top:12px">
                            <button class="btn btn-dim" onclick="closeSubs()">Close</button>
                        </div>
                    </div>
                `;
            }
            break;
        }
    }
}

async function cancelSwap() {
    await api('/api/swap_cancel', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    log('Swap cancel requested', 'yellow');
}

function closeSwapConfirm() {
    // Re-show the substitution list
    showSubstitutions();
}

function closeSubs() {
    document.getElementById('subs-overlay').classList.remove('visible');
}

// ── Keyboard Navigation ─────────────────────────────────────────────
let kbRow = -1;
document.addEventListener('keydown', e => {
    const tbody = document.getElementById('net-body');
    if (!tbody) return;
    const rows = tbody.querySelectorAll('tr');
    if (rows.length === 0) return;

    if (e.key === 'ArrowDown' || e.key === 'j') {
        e.preventDefault();
        rows.forEach(r => r.classList.remove('kb-focus'));
        kbRow = Math.min(kbRow + 1, rows.length - 1);
        rows[kbRow].classList.add('kb-focus');
        rows[kbRow].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'ArrowUp' || e.key === 'k') {
        e.preventDefault();
        rows.forEach(r => r.classList.remove('kb-focus'));
        kbRow = Math.max(kbRow - 1, 0);
        rows[kbRow].classList.add('kb-focus');
        rows[kbRow].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'c' && kbRow >= 0 && kbRow < rows.length) {
        // Copy focused SKU
        const sku = rows[kbRow].querySelector('.sku-cell');
        if (sku) {
            navigator.clipboard.writeText(sku.textContent.trim());
            sku.classList.add('copied');
            setTimeout(() => sku.classList.remove('copied'), 400);
        }
    } else if (e.key === 'Escape') {
        rows.forEach(r => r.classList.remove('kb-focus'));
        kbRow = -1;
        closePicker();
    }
});

// ══════════════════════════════════════════════════════════════════════
//  VIEW SWITCHING + ACTION CALENDAR
// ══════════════════════════════════════════════════════════════════════

let pickerCallback = null;  // Override for picker click in runway view

function switchView(view) {
    currentView = view;
    document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
    document.getElementById(`view-${view}`).classList.add('active');

    const views = {
        dashboard: document.getElementById('content'),
        calendar: document.getElementById('calendar-view'),
        invoices: document.getElementById('invoices-view'),
        settings: document.getElementById('settings-view'),
        cutorder: document.getElementById('cutorder-view'),
        runway: document.getElementById('runway-view'),
        log: document.getElementById('log-view'),
    };

    Object.values(views).forEach(v => { if (v) v.style.display = 'none'; });

    const target = views[view];
    if (target) target.style.display = '';

    if (view === 'calendar' && !calendarData) loadCalendar();
    else if (view === 'invoices') loadInvoices();
    else if (view === 'settings') loadSettingsView();
    else if (view === 'cutorder') loadCutOrder();
    else if (view === 'runway') loadRunway();
    else if (view === 'log') loadActivityLog();
}

async function loadCalendar() {
    log('Loading action calendar...', 'cyan');
    setMascot('loading', 'Building calendar...');

    const data = await api('/api/action_calendar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
    });

    if (data.error) {
        log(`Calendar error: ${data.error}`, 'red');
        setMascot('alert', 'Calendar failed!');
        return;
    }

    calendarData = data;
    renderCalendar(data);
    log(`Calendar generated: ${data.weeks.length} weeks`, 'green');
    setMascot('happy', 'Calendar ready!');
    setTimeout(() => {
        const mood = mascot.currentMood || 'idle';
        setMascotExpression(mood === 'alert' ? 'worried' : 'idle');
    }, 2500);
}

function renderCalendar(data) {
    const grid = document.getElementById('calendar-grid');
    grid.innerHTML = '';

    let totalPO = 0, totalMFG = 0, totalShortages = 0;

    data.weeks.forEach(week => {
        totalPO += week.po_lines.length;
        totalMFG += week.mfg_lines.length;
        totalShortages += week.shortages;

        const weekDiv = document.createElement('div');
        weekDiv.className = 'cal-week';

        // Week header
        const shortBadge = week.shortages > 0
            ? `<span class="shortage-badge">${week.shortages} shortages</span>`
            : '<span style="color:var(--green)">All covered</span>';

        weekDiv.innerHTML = `
            <div class="cal-week-header">
                <span class="cal-week-label">${week.label}</span>
                <span class="cal-week-stats">
                    ${week.total_demand.toLocaleString()} demand units | ${shortBadge}
                    ${week.po_lines.length ? ` | ${week.po_lines.length} PO lines` : ''}
                    ${week.mfg_lines.length ? ` | ${week.mfg_lines.length} MFG orders` : ''}
                </span>
            </div>
        `;

        // Days grid
        const daysDiv = document.createElement('div');
        daysDiv.className = 'cal-days';

        week.days.forEach(day => {
            const dayDiv = document.createElement('div');
            dayDiv.className = 'cal-day';
            if (day.is_today) dayDiv.classList.add('is-today');
            if (day.is_past) dayDiv.classList.add('is-past');

            let inner = `
                <div class="cal-day-header">
                    <span class="cal-day-dow">${day.dow}</span>
                    <span class="cal-day-num">${day.day}</span>
                </div>
            `;

            day.tasks.forEach((task, idx) => {
                const hasShortages = task.type === 'FULFILL' && task.shortages > 0;
                const shortageClass = hasShortages ? ' has-shortages' : '';
                const clickAttr = (task.type === 'PO' && task.lines)
                    ? `onclick="showPODrawer(${week.week - 1}, '${day.date}')" style="cursor:pointer"`
                    : (task.type === 'MFG' && task.lines)
                    ? `onclick="showMFGDrawer(${week.week - 1}, '${day.date}')" style="cursor:pointer"`
                    : '';

                inner += `
                    <div class="cal-task cal-task-${task.type}${shortageClass}" ${clickAttr}>
                        <span class="cal-task-title">${task.title}</span>
                        <span class="cal-task-detail">${task.detail}</span>
                    </div>
                `;
            });

            dayDiv.innerHTML = inner;
            daysDiv.appendChild(dayDiv);
        });

        weekDiv.appendChild(daysDiv);
        grid.appendChild(weekDiv);
    });

    // Update summary
    document.getElementById('cal-po-count').textContent = totalPO;
    document.getElementById('cal-mfg-count').textContent = totalMFG;
    document.getElementById('cal-shortage-count').textContent = totalShortages;
    document.getElementById('cal-shortage-count').style.color =
        totalShortages > 0 ? 'var(--red)' : 'var(--green)';
}

function showPODrawer(weekIdx, date) {
    if (!calendarData) return;
    const week = calendarData.weeks[weekIdx];
    if (!week || !week.po_lines.length) return;

    const drawer = document.getElementById('po-drawer');
    const title = document.getElementById('po-drawer-title');
    const body = document.getElementById('po-drawer-body');

    title.textContent = `PO Lines - ${week.label}`;

    let html = `<table>
        <thead><tr>
            <th>SKU</th><th class="num">Deficit</th><th class="num">Order Qty</th>
            <th class="num">Cases</th><th class="num">Case Qty</th><th>Vendor</th>
        </tr></thead><tbody>`;

    week.po_lines.forEach(p => {
        html += `<tr class="shortage">
            <td>${p.sku}</td>
            <td class="num">${p.deficit}</td>
            <td class="num">${p.order_qty}</td>
            <td class="num">${p.cases}</td>
            <td class="num">${p.case_qty}</td>
            <td>${p.vendor}</td>
        </tr>`;
    });

    const totalUnits = week.po_lines.reduce((s, p) => s + p.order_qty, 0);
    html += `</tbody></table>
        <div style="margin-top:8px;font-size:11px;color:var(--fg2)">
            Total: ${week.po_lines.length} lines, ${totalUnits.toLocaleString()} units
        </div>`;

    body.innerHTML = html;
    drawer.classList.add('visible');
    document.getElementById('mfg-drawer').classList.remove('visible');
}

function showMFGDrawer(weekIdx, date) {
    if (!calendarData) return;
    const week = calendarData.weeks[weekIdx];
    if (!week || !week.mfg_lines.length) return;

    const drawer = document.getElementById('mfg-drawer');
    const title = document.getElementById('mfg-drawer-title');
    const body = document.getElementById('mfg-drawer-body');

    title.textContent = `Production Orders - ${week.label}`;

    let html = `<table>
        <thead><tr>
            <th>SKU</th><th class="num">Deficit</th><th class="num">Slices Needed</th>
            <th class="num">Wheels</th><th>Action</th>
        </tr></thead><tbody>`;

    week.mfg_lines.forEach(m => {
        html += `<tr>
            <td>${m.sku}</td>
            <td class="num">${m.deficit}</td>
            <td class="num">${m.slices_needed}</td>
            <td class="num">${m.wheels_needed}</td>
            <td>${m.action}</td>
        </tr>`;
    });

    const totalSlices = week.mfg_lines.reduce((s, m) => s + m.slices_needed, 0);
    html += `</tbody></table>
        <div style="margin-top:8px;font-size:11px;color:var(--fg2)">
            Total: ${week.mfg_lines.length} SKUs, ${totalSlices.toLocaleString()} slices
        </div>`;

    body.innerHTML = html;
    drawer.classList.add('visible');
    document.getElementById('po-drawer').classList.remove('visible');
}

function openDrawer(id) {
    document.getElementById(id).classList.add('visible');
}

function closeDrawer(id) {
    document.getElementById(id).classList.remove('visible');
}

// ── Enhanced pet reactions ──────────────────────────────────────────
let petClickCount = 0;
let petClickTimer = null;
(function initPetReactions() {
    const el = document.getElementById('mascot');
    if (!el) return;
    // Override the basic click handler with enhanced version
    el.removeEventListener('click', el._petHandler);
    el._petHandler = function(e) {
        if (mascot.petCooldown > 0 && petClickCount < 3) return;
        mascot.petCooldown = 60;
        petClickCount++;
        clearTimeout(petClickTimer);
        petClickTimer = setTimeout(() => { petClickCount = 0; }, 2000);

        el.classList.add('pet');
        setTimeout(() => el.classList.remove('pet'), 300);

        if (petClickCount >= 5) {
            setMascotExpression('alert', randomPick([
                'STOP POKING ME!', 'I have a clipboard and I WILL use it!',
                'Do I look like a stress ball?!', 'AHHH!!',
            ]));
            petClickCount = 0;
        } else if (petClickCount >= 3) {
            setMascotExpression('worried', randomPick([
                'OK OK I get it...', "Hey, I'm working here!",
                'That\'s enough!', 'Personal space please!',
            ]));
        } else {
            setMascotExpression('happy', randomPick([
                'Hey there!', 'That tickles!', "I'm helping!",
                'More cheese?', 'Nom nom!', 'Hi friend!',
            ]));
        }
        setTimeout(() => {
            const mood = mascot.currentMood || 'idle';
            const moodState = { alert: 'worried', worried: 'worried', thinking: 'idle', happy: 'idle' }[mood] || 'idle';
            setMascotExpression(moodState);
        }, 3000);
    };
    el.addEventListener('click', el._petHandler);
})();

// ══════════════════════════════════════════════════════════════════════
//  INVOICES VIEW
// ══════════════════════════════════════════════════════════════════════

let invoicesData = [];
let currentInvoiceId = null;

async function loadInvoices() {
    log('Loading invoice data...', '');
    const status = await api('/api/invoice_status');
    const el = (id) => document.getElementById(id);

    el('inv-total-count').textContent = status.total_invoices || 0;
    el('inv-pending-count').textContent = status.pending_match || 0;
    el('inv-pending-count').style.color = status.pending_match > 0 ? 'var(--yellow)' : 'var(--green)';
    el('inv-total-charge').textContent = '$' + (status.total_production_charge || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    el('inv-last-sync').textContent = status.last_sync || 'never';

    const data = await api('/api/invoices');
    invoicesData = data.invoices || [];
    renderInvoiceTable(invoicesData);
}

function renderInvoiceTable(invoices) {
    const tbody = document.getElementById('inv-body');
    if (!tbody) return;

    if (invoices.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--fg3);padding:30px">No invoices yet. Click <b>Sync Gmail</b> to check for production invoices.</td></tr>';
        return;
    }

    tbody.innerHTML = invoices.map(inv => {
        const statusClass = {
            matched: 'inv-status-matched',
            partial: 'inv-status-partial',
            pending: 'inv-status-pending',
            error: 'inv-status-error',
        }[inv.status] || '';

        return `<tr class="${inv.unmatched_count > 0 ? 'tight' : ''}">
            <td class="sku-cell">${inv.id}</td>
            <td>${inv.mfg_date || '--'}</td>
            <td>${inv.received_date || '--'}</td>
            <td class="num">${inv.products}</td>
            <td class="num">${inv.cases.toLocaleString()}</td>
            <td class="num">${inv.total_yield.toLocaleString()}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif;font-size:14px">$${inv.total_charge.toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
            <td><span class="status-badge ${statusClass}">${inv.status.toUpperCase()}</span></td>
            <td><button class="btn btn-accent btn-sm" onclick="showInvoiceDetail('${inv.id}')">Detail</button></td>
        </tr>`;
    }).join('');
}

async function syncInvoices(force = false) {
    // Show loading state in table and stats
    const tbody = document.getElementById('inv-body');
    if (tbody) tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--accent);padding:30px;font-family:'Space Mono',monospace;font-size:10px;letter-spacing:1px">${force ? 'RE-DOWNLOADING ALL INVOICES FROM GMAIL...' : 'CHECKING GMAIL FOR NEW INVOICES...'}</td></tr>`;
    document.getElementById('inv-total-count').textContent = '...';
    document.getElementById('inv-pending-count').textContent = '...';
    document.getElementById('inv-total-charge').textContent = '...';
    document.getElementById('inv-last-sync').textContent = 'syncing...';

    setMascot('loading', force ? 'Re-downloading all invoices...' : 'Connecting to Gmail...');
    log(force ? 'Force re-syncing — clearing all invoices and re-parsing from Gmail...' : 'Connecting to Gmail IMAP...', 'cyan');

    // Start the sync (returns immediately, runs in background)
    const start = await api('/api/invoice_sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force }),
    });

    if (start.error && !start.started) {
        log(`Invoice sync failed: ${start.error}`, 'red');
        setMascot('alert', start.error.includes('IMAP') ? 'Gmail connection failed!' : 'Sync error!');
        if (tbody) tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--red);padding:30px">${start.error}</td></tr>`;
        return;
    }

    // Poll for progress
    let lastProgress = '';
    while (true) {
        await new Promise(r => setTimeout(r, 800));
        const status = await api('/api/invoice_sync_progress');

        if (status.progress && status.progress !== lastProgress) {
            lastProgress = status.progress;
            if (tbody) tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--accent);padding:30px;font-family:'Space Mono',monospace;font-size:10px;letter-spacing:1px">${status.progress.toUpperCase()}</td></tr>`;
            setMascot('loading', status.progress);
            log(status.progress, 'cyan');
        }

        if (!status.running) {
            const data = status.result || {};
            if (data.error) {
                log(`Invoice sync failed: ${data.error}`, 'red');
                setMascot('alert', 'Sync failed!');
                if (tbody) tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--red);padding:30px">${data.error}</td></tr>`;
                return;
            }

            log(`Gmail sync complete: ${data.emails_checked} emails scanned, ${data.new_invoices} new invoices parsed`, 'green');
            if (data.new_invoices > 0) {
                log(`Total invoices in system: ${data.total_invoices}`, 'green');
            }
            addScore(data.new_invoices * 50);

            if (data.new_invoices > 0) {
                setMascot('celebrate', `Parsed ${data.new_invoices} invoice${data.new_invoices > 1 ? 's' : ''}!`);
            } else {
                setMascot('idle', 'Inbox checked — no new invoices');
            }
            setTimeout(() => setMascotExpression('idle'), 3000);
            break;
        }
    }

    await loadInvoices();
}

async function showInvoiceDetail(id) {
    currentInvoiceId = id;
    log(`Loading invoice ${id}...`, '');
    const inv = await api(`/api/invoice/${id}`);

    if (inv.error) {
        log(`Error loading invoice: ${inv.error}`, 'red');
        return;
    }

    document.getElementById('inv-detail-title').textContent = `Invoice ${inv.id} — ${inv.mfg_date || 'Unknown date'}`;

    const body = document.getElementById('inv-detail-body');
    let html = '';

    // Summary row
    html += `<div style="display:flex;gap:16px;margin-bottom:12px;flex-wrap:wrap">
        <div style="color:var(--fg3);font-size:11px">Full MFG: <span style="color:var(--accent);font-family:'Rajdhani',sans-serif;font-size:14px">$${(inv.full_mfg_charge || 0).toLocaleString(undefined, {minimumFractionDigits: 2})}</span></div>
        <div style="color:var(--fg3);font-size:11px">Label Only: <span style="color:var(--accent);font-family:'Rajdhani',sans-serif;font-size:14px">$${(inv.label_only_charge || 0).toLocaleString(undefined, {minimumFractionDigits: 2})}</span></div>
        <div style="color:var(--fg3);font-size:11px">Meals: <span style="color:var(--accent);font-family:'Rajdhani',sans-serif;font-size:14px">$${(inv.meals_charge || 0).toLocaleString(undefined, {minimumFractionDigits: 2})}</span></div>
        <div style="color:var(--fg3);font-size:11px">Total: <span style="color:var(--green);font-family:'Rajdhani',sans-serif;font-size:16px;font-weight:600">$${(inv.total_production_charge || 0).toLocaleString(undefined, {minimumFractionDigits: 2})}</span></div>
    </div>`;

    // Line items table
    html += `<table><thead><tr>
        <th>Section</th><th>Product</th><th>SKU</th><th class="num">Cases</th><th class="num">Yield</th><th class="num">Est. Cost</th><th>Match</th>
    </tr></thead><tbody>`;

    for (const li of (inv.line_items || [])) {
        const isUnmatched = !li.sku;
        const matchBadge = isUnmatched
            ? `<span class="inv-unmatched" onclick="mapProductSku('${li.product_name.replace(/'/g, "\\'")}')">MAP SKU</span>`
            : `<span style="color:var(--green);font-size:9px">${li.match_method} (${Math.round(li.match_confidence * 100)}%)</span>`;

        html += `<tr class="${isUnmatched ? 'shortage' : ''}">
            <td style="font-size:10px;color:var(--fg3)">${li.section.replace('_', ' ')}</td>
            <td>${li.product_name}</td>
            <td class="sku-cell">${li.sku || '--'}</td>
            <td class="num">${li.case_packouts || '--'}</td>
            <td class="num">${li.total_yield.toLocaleString()}</td>
            <td class="num">${li.estimated_cost != null ? '$' + li.estimated_cost.toFixed(2) : '--'}</td>
            <td>${matchBadge}</td>
        </tr>`;
    }
    html += '</tbody></table>';

    // Yield Analysis (fetch async, append)
    const yieldPlaceholder = `inv-yield-${inv.id}`;
    html += `<div id="${yieldPlaceholder}" style="margin-top:14px"></div>`;

    // PO Matches
    if (inv.po_matches && inv.po_matches.length > 0) {
        html += '<div style="margin-top:14px"><b style="color:var(--accent);font-size:11px;text-transform:uppercase;letter-spacing:1px">PO Reconciliation</b></div>';
        html += `<table style="margin-top:6px"><thead><tr>
            <th>SKU</th><th class="num">PO Qty</th><th class="num">Actual</th><th class="num">Variance</th><th class="num">Var %</th>
        </tr></thead><tbody>`;
        for (const m of inv.po_matches) {
            const varColor = m.variance < 0 ? 'var(--red)' : m.variance > 0 ? 'var(--green)' : 'var(--fg)';
            html += `<tr>
                <td class="sku-cell">${m.sku}</td>
                <td class="num">${m.po_qty}</td>
                <td class="num">${m.actual_yield}</td>
                <td class="num" style="color:${varColor}">${m.variance > 0 ? '+' : ''}${m.variance}</td>
                <td class="num" style="color:${varColor}">${m.variance_pct > 0 ? '+' : ''}${m.variance_pct.toFixed(1)}%</td>
            </tr>`;
        }
        html += '</tbody></table>';
    }

    body.innerHTML = html;
    document.getElementById('inv-detail-drawer').classList.add('visible');
    document.getElementById('inv-cost-drawer').classList.remove('visible');

    // Load yield analysis
    api(`/api/invoice_yield/${id}`).then(yData => {
        const el = document.getElementById(`inv-yield-${id}`);
        if (!el || !yData.annotations || yData.annotations.length === 0) return;

        let yHtml = '<b style="color:var(--accent);font-size:11px;text-transform:uppercase;letter-spacing:1px">Yield Analysis (vs Historical Avg)</b>';
        yHtml += `<table style="margin-top:6px"><thead><tr>
            <th>SKU</th><th class="num">Cases</th><th class="num">Actual</th><th class="num">Expected</th><th class="num">Variance</th><th class="num">Pcs/Wh</th><th class="num">Oz/Pc</th><th class="num">Wt (lb)</th>
        </tr></thead><tbody>`;

        for (const a of yData.annotations) {
            if (a.expected === null) {
                yHtml += `<tr style="opacity:0.5">
                    <td class="sku-cell">${a.sku}</td><td class="num">${a.cases}</td><td class="num">${a.actual}</td>
                    <td class="num" colspan="5" style="color:var(--fg3);font-size:10px">${a.note}</td>
                </tr>`;
                continue;
            }
            const varColor = a.variance < 0 ? 'var(--red)' : a.variance > 0 ? 'var(--green)' : 'var(--fg)';
            const varSign = a.variance > 0 ? '+' : '';
            const srcTag = a.weight_source === 'inventory' ? '' : ' <span style="color:var(--fg3);font-size:8px">est</span>';
            yHtml += `<tr>
                <td class="sku-cell">${a.sku}</td>
                <td class="num">${a.cases}</td>
                <td class="num">${a.actual.toLocaleString()}</td>
                <td class="num" style="color:var(--fg2)">${a.expected.toLocaleString()}</td>
                <td class="num" style="color:${varColor}">${varSign}${a.variance} (${varSign}${a.variance_pct}%)</td>
                <td class="num" style="color:var(--fg2)">${a.avg_ratio || '--'}</td>
                <td class="num" style="font-family:'Rajdhani',sans-serif;font-size:13px">${a.oz_per_pc ? a.oz_per_pc.toFixed(2) : '--'}${srcTag}</td>
                <td class="num" style="color:var(--fg3)">${a.weight_lbs || '--'}</td>
            </tr>`;
        }
        yHtml += '</tbody></table>';
        el.innerHTML = yHtml;
    });
}

function mapProductSku(productName) {
    currentMapProduct = productName;
    const overlay = document.getElementById('inv-sku-mapper');
    overlay.style.display = '';
    document.getElementById('inv-mapper-title').textContent = `Map: "${productName}"`;
    const list = document.getElementById('inv-mapper-list');
    list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--fg3)">Loading candidates...</div>';

    // Fetch ranked candidates
    api('/api/invoice_match_candidates', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_name: productName }),
    }).then(data => {
        const candidates = data.candidates || [];
        let html = '';

        if (candidates.length > 0) {
            // Recommended section
            const recommended = candidates.filter(c => c.recommended);
            const others = candidates.filter(c => !c.recommended);

            if (recommended.length > 0) {
                html += '<div class="inv-mapper-section">RECOMMENDED</div>';
                html += recommended.map(c => mapperItem(productName, c, true)).join('');
            }
            if (others.length > 0) {
                html += '<div class="inv-mapper-section">OTHER CANDIDATES</div>';
                html += others.map(c => mapperItem(productName, c, false)).join('');
            }
        }

        // Also show full inventory below
        html += '<div class="inv-mapper-section">ALL SKUS</div>';
        api('/api/data').then(fullData => {
            const inv = fullData.inventory || {};
            const skus = Object.entries(inv)
                .map(([sku, info]) => ({
                    sku,
                    name: (typeof info === 'object' ? info.name : '') || sku,
                }))
                .sort((a, b) => a.sku.localeCompare(b.sku));

            html += skus.map(s =>
                `<div class="picker-item" onclick="confirmSkuMap('${productName.replace(/'/g, "\\'")}', '${s.sku}')">
                    <span class="pi-sku">${s.sku}</span>
                    <span style="flex:1;color:var(--fg2);font-size:11px">${s.name}</span>
                </div>`
            ).join('');
            list.innerHTML = html;
        });
    });
}

function mapperItem(productName, c, isRecommended) {
    const scorePct = Math.round(c.score * 100);
    const scoreColor = scorePct >= 70 ? 'var(--green)' : scorePct >= 55 ? 'var(--yellow)' : 'var(--fg3)';
    const catIcon = c.category === 'cheese' ? '🧀' : c.category === 'meat' ? '🥩' : c.category === 'accompaniment' ? '🫒' : '📦';
    return `<div class="picker-item ${isRecommended ? 'inv-recommended' : ''}" onclick="confirmSkuMap('${productName.replace(/'/g, "\\'")}', '${c.sku}')">
        <span style="font-size:12px">${catIcon}</span>
        <span class="pi-sku">${c.sku}</span>
        <span style="flex:1;color:var(--fg2);font-size:11px">${c.name}</span>
        <span style="color:${scoreColor};font-family:'Rajdhani',sans-serif;font-size:13px;font-weight:600;min-width:40px;text-align:right">${scorePct}%</span>
    </div>`;
}

let currentMapProduct = '';

async function confirmSkuMap(productName, sku) {
    closeSkuMapper();
    log(`Saving mapping: "${productName}" → ${sku}...`, 'cyan');
    setMascot('loading', 'Saving SKU mapping...');

    const data = await api('/api/invoice_map_sku', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_name: productName, sku }),
    });

    if (data.error) {
        log(`Mapping failed: ${data.error}`, 'red');
        setMascot('alert', 'Mapping failed!');
        return;
    }

    log(`Mapped "${productName}" → ${sku} — updated ${data.updated} line item${data.updated > 1 ? 's' : ''} across all invoices`, 'green');
    setMascot('happy', 'SKU mapped!');
    setTimeout(() => setMascotExpression('idle'), 2000);
    addScore(25);

    // Refresh detail view
    if (currentInvoiceId) {
        await showInvoiceDetail(currentInvoiceId);
    }
    await loadInvoices();
}

function closeSkuMapper() {
    const overlay = document.getElementById('inv-sku-mapper');
    if (overlay) overlay.style.display = 'none';
}

async function autoMapAll() {
    setMascot('loading', 'Matching product names to SKUs...');
    log('Auto-mapping unmatched invoice products to inventory SKUs...', 'cyan');

    const data = await api('/api/invoice_auto_map', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
    });

    if (data.error) {
        log(`Auto-map failed: ${data.error}`, 'red');
        setMascot('alert', 'Auto-map failed!');
        return;
    }

    const mapped = data.mapped || [];
    const skipped = data.skipped || [];

    for (const m of mapped) {
        log(`  Matched: "${m.product_name}" → ${m.sku} (${Math.round(m.score * 100)}% confidence)`, 'green');
    }
    for (const s of skipped) {
        log(`  Low confidence: "${s.product_name}" — best guess ${s.best_sku} (${Math.round(s.best_score * 100)}%) — needs manual mapping`, 'yellow');
    }

    if (mapped.length > 0 || skipped.length > 0) {
        log(`Auto-map complete: ${mapped.length} matched, ${skipped.length} need manual review`, mapped.length > 0 ? 'green' : 'yellow');
    } else {
        log('All products already mapped — nothing to do', 'green');
    }
    addScore(mapped.length * 25);

    if (skipped.length === 0 && mapped.length > 0) {
        setMascot('celebrate', `All ${mapped.length} products matched!`);
    } else if (mapped.length > 0) {
        setMascot('happy', `${mapped.length} matched, ${skipped.length} need review`);
    } else {
        setMascot('idle', 'All products already mapped');
    }
    setTimeout(() => setMascotExpression('idle'), 3000);

    await loadInvoices();
    if (currentInvoiceId) await showInvoiceDetail(currentInvoiceId);
}

async function reconcileInvoice() {
    if (!currentInvoiceId) return;

    setMascot('loading', 'Matching invoice against open POs...');
    log(`Reconciling invoice ${currentInvoiceId} — comparing yields to open purchase orders...`, 'cyan');

    // Disable button during operation
    const btn = document.getElementById('inv-reconcile-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Reconciling...'; }

    const data = await api(`/api/invoice_reconcile/${currentInvoiceId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
    });

    if (btn) { btn.disabled = false; btn.textContent = 'Reconcile'; }

    if (data.error) {
        log(`Reconciliation failed: ${data.error}`, 'red');
        setMascot('alert', 'Reconciliation failed!');
        return;
    }

    if (data.closed_pos > 0) log(`  Closed ${data.closed_pos} purchase order${data.closed_pos > 1 ? 's' : ''} (marked Received)`, 'green');
    if (data.yield_entries > 0) log(`  Logged ${data.yield_entries} yield entr${data.yield_entries > 1 ? 'ies' : 'y'} to production history`, 'green');
    if (data.cost_entries > 0) log(`  Calculated per-unit costs for ${data.cost_entries} SKU${data.cost_entries > 1 ? 's' : ''}`, 'green');
    if (data.closed_pos === 0 && data.yield_entries === 0) log('  No matching open POs found — costs calculated only', 'yellow');
    log(`Reconciliation complete for ${currentInvoiceId}`, 'green');

    addScore(100);
    setMascot('celebrate', `Reconciled! ${data.closed_pos} POs closed`);
    setTimeout(() => setMascotExpression('idle'), 3000);

    await showInvoiceDetail(currentInvoiceId);
    await loadInvoices();
}

async function showCostAnalytics() {
    setMascot('loading', 'Crunching production costs...');
    log('Loading per-SKU production cost analytics...', 'cyan');
    const data = await api('/api/invoice_cost_history');
    const analytics = data.analytics || [];

    const body = document.getElementById('inv-cost-body');

    if (analytics.length === 0) {
        body.innerHTML = '<div style="text-align:center;color:var(--fg3);padding:20px">No cost data yet. Reconcile invoices to calculate per-unit production costs.</div>';
        log('No cost data — reconcile invoices first to generate cost analytics', 'yellow');
        setMascot('idle', 'Reconcile invoices first');
        document.getElementById('inv-cost-drawer').classList.add('visible');
        document.getElementById('inv-detail-drawer').classList.remove('visible');
        return;
    }
    log(`Cost analytics loaded: ${analytics.length} SKUs tracked`, 'green');
    setMascot('idle', `${analytics.length} SKU costs`);

    let html = `<table><thead><tr>
        <th>SKU</th><th class="num">Total Yield</th><th class="num">Total Cost</th><th class="num">Avg $/Unit</th><th class="num">Invoices</th>
    </tr></thead><tbody>`;

    const maxCost = Math.max(...analytics.map(a => a.avg_cost_per_unit));

    for (const a of analytics) {
        const barWidth = maxCost > 0 ? Math.round((a.avg_cost_per_unit / maxCost) * 100) : 0;
        html += `<tr>
            <td class="sku-cell">${a.sku}</td>
            <td class="num">${a.total_yield.toLocaleString()}</td>
            <td class="num">$${a.total_cost.toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
            <td class="num">
                <div style="display:flex;align-items:center;justify-content:flex-end;gap:6px">
                    <div class="inv-cost-bar" style="width:${barWidth}%"></div>
                    <span>$${a.avg_cost_per_unit.toFixed(2)}</span>
                </div>
            </td>
            <td class="num">${a.entries}</td>
        </tr>`;
    }
    html += '</tbody></table>';

    body.innerHTML = html;
    document.getElementById('inv-cost-drawer').classList.add('visible');
    document.getElementById('inv-detail-drawer').classList.remove('visible');
}


// ── Inventory Reconciliation ─────────────────────────────────────────

let reconSnapshots = [];

async function showReconciliation() {
    setMascot('loading', 'Loading snapshots...');
    log('Opening inventory reconciliation...', 'cyan');

    const data = await api('/api/reconcile_snapshots');
    reconSnapshots = data.snapshots || [];

    const body = document.getElementById('inv-recon-body');

    if (reconSnapshots.length < 2) {
        body.innerHTML = '<div style="text-align:center;color:var(--fg3);padding:20px">Need at least 2 inventory snapshots. Sync Dropbox or run depletion to create snapshots.</div>';
        log('Not enough snapshots for reconciliation', 'yellow');
        setMascot('idle', 'Need more snapshots');
        document.getElementById('inv-recon-drawer').classList.add('visible');
        return;
    }

    // Build snapshot picker
    let html = `<div style="padding:12px 16px;border-bottom:1px solid var(--border)">
        <div style="display:flex;gap:16px;align-items:flex-end;flex-wrap:wrap">
            <div>
                <label style="font-family:'Space Mono',monospace;font-size:9px;text-transform:uppercase;color:var(--fg3);display:block;margin-bottom:4px">Actual (Monday)</label>
                <select id="recon-monday" style="background:var(--bg2);color:var(--fg);border:1px solid var(--border);padding:4px 8px;font-family:'DM Sans',sans-serif;font-size:12px;border-radius:3px">
                    ${reconSnapshots.map(sn => `<option value="${sn.id}" ${sn.cycle_day === 'monday' ? 'selected' : ''}>${sn.label || sn.timestamp.slice(0,10)} (${sn.sku_count} SKUs${sn.cycle_day ? ', ' + sn.cycle_day : ''})</option>`).join('')}
                </select>
            </div>
            <div>
                <label style="font-family:'Space Mono',monospace;font-size:9px;text-transform:uppercase;color:var(--fg3);display:block;margin-bottom:4px">Baseline (Friday)</label>
                <select id="recon-friday" style="background:var(--bg2);color:var(--fg);border:1px solid var(--border);padding:4px 8px;font-family:'DM Sans',sans-serif;font-size:12px;border-radius:3px">
                    ${reconSnapshots.map(sn => `<option value="${sn.id}" ${(sn.cycle_day === 'friday' || sn.source === 'depletion') ? 'selected' : ''}>${sn.label || sn.timestamp.slice(0,10)} (${sn.sku_count} SKUs${sn.cycle_day ? ', ' + sn.cycle_day : ''})</option>`).join('')}
                </select>
            </div>
            <button class="btn btn-green btn-sm" onclick="runReconciliation()">Compare</button>
        </div>
    </div>
    <div id="recon-results" style="padding:12px 16px;color:var(--fg3);text-align:center">Select snapshots and click Compare</div>`;

    body.innerHTML = html;
    document.getElementById('inv-recon-drawer').classList.add('visible');
    // Close other drawers
    document.getElementById('inv-detail-drawer').classList.remove('visible');
    document.getElementById('inv-cost-drawer').classList.remove('visible');
    setMascot('idle', 'Pick snapshots to compare');
    log(`${reconSnapshots.length} snapshots available for comparison`, 'green');
}

async function runReconciliation() {
    const mondayId = document.getElementById('recon-monday').value;
    const fridayId = document.getElementById('recon-friday').value;

    if (mondayId === fridayId) {
        log('Cannot compare a snapshot to itself', 'red');
        setMascot('alert', 'Pick two different snapshots!');
        return;
    }

    setMascot('loading', 'Reconciling inventory...');
    log('Running inventory reconciliation...', 'cyan');

    const data = await api('/api/reconcile_inventory', {
        monday_snap_id: mondayId,
        friday_snap_id: fridayId,
    });

    if (data.error) {
        log(`Reconciliation error: ${data.error}`, 'red');
        setMascot('alert', 'Reconciliation failed');
        document.getElementById('recon-results').innerHTML = `<div style="color:var(--red);padding:16px">${data.error}</div>`;
        return;
    }

    const rows = data.rows || [];
    const summary = data.summary || {};

    log(`Reconciliation complete: ${summary.total_skus} SKUs checked, ${summary.flagged} flagged, ${summary.invoices_in_window} invoices in window`, summary.flagged > 0 ? 'yellow' : 'green');
    setMascot(summary.flagged > 0 ? 'alert' : 'happy', `${summary.flagged} discrepancies found`);

    // Build summary stats
    let html = `<div style="display:flex;gap:16px;padding:0 0 12px;border-bottom:1px solid var(--border);margin-bottom:12px;flex-wrap:wrap">
        <div class="cal-summary-item">
            <span class="cal-summary-label">Baseline</span>
            <span class="cal-summary-value" style="font-size:11px">${data.friday.label || data.friday.timestamp.slice(0,10)}</span>
        </div>
        <div class="cal-summary-item">
            <span class="cal-summary-label">Actual</span>
            <span class="cal-summary-value" style="font-size:11px">${data.monday.label || data.monday.timestamp.slice(0,10)}</span>
        </div>
        <div class="cal-summary-item">
            <span class="cal-summary-label">SKUs Checked</span>
            <span class="cal-summary-value">${summary.total_skus}</span>
        </div>
        <div class="cal-summary-item">
            <span class="cal-summary-label">Flagged</span>
            <span class="cal-summary-value" style="color:${summary.flagged > 0 ? 'var(--red)' : 'var(--green)'}">${summary.flagged}</span>
        </div>
        <div class="cal-summary-item">
            <span class="cal-summary-label">Total Discrepancy</span>
            <span class="cal-summary-value" style="color:var(--orange)">${summary.total_discrepancy}</span>
        </div>
        <div class="cal-summary-item">
            <span class="cal-summary-label">Invoices in Window</span>
            <span class="cal-summary-value">${summary.invoices_in_window}</span>
        </div>
    </div>`;

    // Build results table
    html += `<div class="net-table-wrap"><table class="net-table"><thead><tr>
        <th>SKU</th>
        <th class="num">Baseline</th>
        <th class="num">+ Invoice</th>
        <th class="num">Expected</th>
        <th class="num">Actual</th>
        <th class="num">Diff</th>
        <th class="num">%</th>
        <th>Status</th>
    </tr></thead><tbody>`;

    for (const r of rows) {
        const flagClass = r.flagged ? 'style="background:rgba(255,59,92,0.06)"' : '';
        const diffColor = r.diff > 0 ? 'var(--green)' : r.diff < 0 ? 'var(--red)' : 'var(--fg3)';
        const statusLabel = r.flagged
            ? (r.status === 'over' ? '<span style="color:var(--green)">OVER</span>' : '<span style="color:var(--red)">UNDER</span>')
            : (r.diff === 0 ? '<span style="color:var(--fg3)">MATCH</span>' : '<span style="color:var(--fg3)">OK</span>');

        html += `<tr ${flagClass}>
            <td class="sku-cell">${r.sku}</td>
            <td class="num">${r.friday}</td>
            <td class="num" style="color:var(--accent)">${r.invoice_yield > 0 ? '+' + r.invoice_yield : '—'}</td>
            <td class="num">${r.expected}</td>
            <td class="num" style="font-weight:500">${r.monday}</td>
            <td class="num" style="color:${diffColor};font-weight:${r.flagged ? '600' : '400'}">${r.diff > 0 ? '+' : ''}${r.diff}</td>
            <td class="num" style="color:${diffColor}">${r.pct > 0 ? '+' : ''}${r.pct}%</td>
            <td>${statusLabel}</td>
        </tr>`;
    }
    html += '</tbody></table></div>';

    document.getElementById('recon-results').innerHTML = html;
}

// ── Depletion File Parser ────────────────────────────────────────────

let lastDepletionData = null;

function uploadDepletion() {
    document.getElementById('depletion-input').click();
}

async function scanForDepletions() {
    setMascot('loading', 'Scanning for depletion files...');
    log('Scanning Gmail Sent + Downloads for depletion files...', 'cyan');

    const data = await api('/api/depletion_scan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({}),
    });

    if (!data || data.error) {
        log('Scan error: ' + (data?.error || 'unknown'), 'red');
        setMascot('alert', 'Scan failed!');
        return;
    }

    if (data.count === 0) {
        log('No new depletion files found', 'yellow');
        setMascot('idle', 'No new depletion files.');
        return;
    }

    log(`Found ${data.count} depletion file(s)`, 'green');
    setMascot('happy', `Found ${data.count} file(s)!`);

    // Show detected files in depletion drawer
    const body = document.getElementById('depletion-drawer-body');
    const title = document.getElementById('depletion-drawer-title');
    title.textContent = `Detected Depletion Files (${data.count})`;

    let html = '<div style="padding:12px">';
    for (const f of data.files) {
        const dateLabel = f.date_sent || f.date_modified || '';
        const srcLabel = f.source === 'gmail_sent' ? '📧 Gmail Sent' : '📁 Downloads';
        html += `<div style="display:flex;align-items:center;gap:12px;padding:8px;margin-bottom:6px;background:var(--surface);border-radius:6px;border:1px solid var(--border)">
            <div style="flex:1">
                <div style="font-family:'Space Mono',monospace;font-size:11px;font-weight:600;color:var(--fg)">${f.filename}</div>
                <div style="font-size:10px;color:var(--fg2)">${srcLabel} · ${dateLabel}</div>
            </div>
            <button class="btn btn-green btn-sm" onclick="loadScannedDepletion('${f.path.replace(/\\/g, '\\\\')}', '${f.filename}')">Load & Preview</button>
        </div>`;
    }
    html += '</div>';
    body.innerHTML = html;

    const drawer = document.getElementById('depletion-drawer');
    drawer.style.display = 'block';
    document.getElementById('depletion-apply-btn').disabled = true;
}

async function loadScannedDepletion(filePath, filename) {
    setMascot('loading', 'Parsing depletion file...');
    log(`Loading scanned depletion: ${filename}`, 'cyan');

    const data = await api('/api/depletion_scan_and_parse', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: filePath, filename: filename}),
    });

    if (!data || data.error) {
        log(`Parse error: ${data?.error || 'unknown'}`, 'red');
        setMascot('alert', 'Parse failed!');
        return;
    }

    lastDepletionData = data;
    renderDepletionResults(data);
    log(`Depletion parsed: ${data.order_count} orders, ${data.mapped_count} mapped, ${data.unmatched_count} unmatched`, 'green');
    setMascot('happy', `${data.order_count} orders parsed!`);
}

async function handleDepletionFile(input) {
    const file = input.files[0];
    if (!file) return;
    input.value = '';

    setMascot('loading', 'Parsing depletion file...');
    log(`Uploading depletion: ${file.name}`, 'cyan');

    const formData = new FormData();
    formData.append('file', file);

    const resp = await fetch('/api/depletion_parse', {method: 'POST', body: formData});
    const data = await resp.json();

    if (data.error) {
        log(`Depletion error: ${data.error}`, 'red');
        setMascot('alert', 'Parse failed!');
        return;
    }

    lastDepletionData = data;
    renderDepletionResults(data);
    log(`Depletion parsed: ${data.order_count} orders, ${data.mapped_count} mapped, ${data.unmatched_count} unmatched`, 'green');
    setMascot('happy', `${data.order_count} orders parsed!`);
}

function renderDepletionResults(data) {
    const body = document.getElementById('depletion-drawer-body');
    const title = document.getElementById('depletion-drawer-title');
    title.textContent = `Depletion: ${data.filename}`;

    let html = `<div style="display:flex;gap:16px;padding:10px 12px;border-bottom:1px solid var(--border)">
        <div class="cal-summary"><div class="cal-summary-val" style="font-family:'Rajdhani',sans-serif;font-size:18px;font-weight:600">${data.order_count.toLocaleString()}</div><div class="cal-summary-label">Orders</div></div>
        <div class="cal-summary"><div class="cal-summary-val" style="font-family:'Rajdhani',sans-serif;font-size:18px">${data.total_units.toLocaleString()}</div><div class="cal-summary-label">Total Units</div></div>
        <div class="cal-summary"><div class="cal-summary-val" style="font-family:'Rajdhani',sans-serif;font-size:18px">${data.product_count}</div><div class="cal-summary-label">Products</div></div>
        <div class="cal-summary"><div class="cal-summary-val" style="color:var(--green)">${data.mapped_count}</div><div class="cal-summary-label">Mapped</div></div>
        <div class="cal-summary"><div class="cal-summary-val" style="color:${data.unmatched_count > 0 ? 'var(--red)' : 'var(--green)'}">${data.unmatched_count}</div><div class="cal-summary-label">Unmatched</div></div>
    </div>`;

    // Unmatched products (if any)
    if (data.unmatched && data.unmatched.length > 0) {
        html += `<div style="padding:8px 12px;background:rgba(255,59,92,0.05);border-bottom:1px solid var(--border)">
            <div style="font-family:'Space Mono',monospace;font-size:10px;text-transform:uppercase;color:var(--red);margin-bottom:6px">Unmatched Products</div>`;
        for (const u of data.unmatched) {
            html += `<div style="display:flex;justify-content:space-between;align-items:center;padding:3px 0">
                <span style="font-family:'DM Sans',sans-serif;font-size:12px;color:var(--red)">${u.product} (${u.qty})</span>
                <button class="btn btn-dim btn-sm" onclick="mapDepletionProduct('${u.product.replace(/'/g, "\\'")}')">Map SKU</button>
            </div>`;
        }
        html += '</div>';
    }

    // SKU totals table
    const skuEntries = Object.entries(data.sku_totals).sort((a, b) => b[1] - a[1]);
    html += `<table><thead><tr>
        <th>SKU</th><th>Product</th><th class="num">Depleted</th>
    </tr></thead><tbody>`;

    // Build reverse map: sku -> product name
    const skuToProduct = {};
    for (const [product, sku] of Object.entries(data.mapped)) {
        skuToProduct[sku] = product;
    }

    for (const [sku, qty] of skuEntries) {
        html += `<tr>
            <td style="font-family:'Space Mono',monospace;font-size:10px">${sku}</td>
            <td style="font-family:'DM Sans',sans-serif;font-size:11px">${skuToProduct[sku] || ''}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif;font-weight:600">${qty.toLocaleString()}</td>
        </tr>`;
    }
    html += '</tbody></table>';

    body.innerHTML = html;
    document.getElementById('depletion-drawer').classList.add('visible');
    document.getElementById('depletion-apply-btn').disabled = false;
}

async function applyDepletion() {
    if (!lastDepletionData || !lastDepletionData.sku_totals) return;
    if (!confirm(`Apply depletion of ${Object.keys(lastDepletionData.sku_totals).length} SKUs (${lastDepletionData.total_units.toLocaleString()} units) to inventory?`)) return;

    setMascot('loading', 'Applying depletion...');
    const data = await api('/api/depletion_apply', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            sku_totals: lastDepletionData.sku_totals,
            label: lastDepletionData.filename,
            order_count: lastDepletionData.order_count,
        }),
    });

    if (data && data.ok) {
        log(`Depletion applied: ${data.skus_affected} SKUs, ${data.total_depleted.toLocaleString()} units`, 'green');
        setMascot('happy', 'Inventory updated!');
        document.getElementById('depletion-apply-btn').disabled = true;
        // Record forecast accuracy
        const depWindow = new Date().getDay() === 6 ? 'saturday' : 'tuesday';
        recordForecastAccuracy(lastDepletionData.sku_totals, depWindow);
        // Re-calculate to see new NET
        calculateRMFG();
    } else {
        log(`Depletion failed: ${data?.error || 'unknown'}`, 'red');
        setMascot('alert', 'Apply failed!');
    }
}

function mapDepletionProduct(product) {
    const sku = prompt(`Enter SKU for "${product}":`);
    if (!sku) return;

    api('/api/depletion_map_sku', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({product, sku: sku.toUpperCase()}),
    }).then(data => {
        if (data && data.ok) {
            log(`Mapped: ${product} = ${sku.toUpperCase()}`, 'green');
        }
    });
}


// ── Demand Mode Toggle ──────────────────────────────────────────────

function toggleDemandMode() {
    demandMode = demandMode === 'discrete' ? 'churned' : 'discrete';
    localStorage.setItem('demandMode', demandMode);
    log(`Demand mode: ${demandMode.toUpperCase()}`, demandMode === 'churned' ? 'orange' : 'cyan');
    calculateRMFG();
}


// ── Inventory Snapshots ──────────────────────────────────────────────

let snapshotSelectA = null;
let snapshotSelectB = null;

async function showSnapshots() {
    const data = await api('/api/snapshots');
    if (!data || !data.snapshots) {
        log('Failed to load snapshots', 'red');
        return;
    }

    const body = document.getElementById('snapshot-drawer-body');
    const snaps = data.snapshots;

    if (snaps.length === 0) {
        body.innerHTML = `<div style="padding:16px;color:var(--fg2);font-family:'Space Mono',monospace;font-size:10px;text-transform:uppercase">
            No snapshots yet. Save one using the button above, or sync Dropbox to auto-snapshot.</div>`;
        document.getElementById('snapshot-drawer').classList.add('visible');
        return;
    }

    snapshotSelectA = null;
    snapshotSelectB = null;

    let html = `<div style="padding:8px 12px;display:flex;gap:8px;align-items:center;border-bottom:1px solid var(--border)">
        <span style="font-family:'Space Mono',monospace;font-size:10px;text-transform:uppercase;color:var(--fg2)">
            Select two snapshots to compare</span>
        <button class="btn btn-accent btn-sm" id="snap-compare-btn" onclick="compareSnapshots()" disabled>Compare</button>
    </div>`;

    html += `<table><thead><tr>
        <th style="width:30px">A</th><th style="width:30px">B</th>
        <th>Label</th><th>Date</th><th>Cycle</th><th>Source</th>
        <th class="num">SKUs</th><th class="num">Units</th><th class="num">Pot. Yield</th>
        <th style="width:40px"></th>
    </tr></thead><tbody>`;

    for (const s of snaps.slice().reverse()) {
        const ts = new Date(s.timestamp);
        const dateStr = ts.toLocaleDateString('en-US', {month:'short', day:'numeric'});
        const timeStr = ts.toLocaleTimeString('en-US', {hour:'numeric', minute:'2-digit'});
        const cycleClass = {friday:'color:var(--accent)',saturday:'color:var(--green)',
            monday:'color:var(--blue)',tuesday:'color:var(--orange)',
            wednesday:'color:var(--yellow)'}[s.cycle_day] || '';
        const potYield = s.potential_yield || 0;

        html += `<tr>
            <td><input type="radio" name="snap-a" value="${s.id}" onchange="snapSelect('a','${s.id}')"></td>
            <td><input type="radio" name="snap-b" value="${s.id}" onchange="snapSelect('b','${s.id}')"></td>
            <td style="font-family:'DM Sans',sans-serif;font-size:12px">${s.label}</td>
            <td style="font-family:'Rajdhani',sans-serif;font-size:12px">${dateStr} ${timeStr}</td>
            <td style="font-family:'Space Mono',monospace;font-size:10px;text-transform:uppercase;${cycleClass}">${s.cycle_day || '-'}</td>
            <td style="font-family:'Space Mono',monospace;font-size:10px;text-transform:uppercase">${s.source}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${s.sku_count}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${s.total_units.toLocaleString()}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif;color:var(--blue)">${potYield > 0 ? '+' + potYield.toLocaleString() : '-'}</td>
            <td><button class="btn btn-dim btn-sm" onclick="deleteSnapshot('${s.id}')" title="Delete">&#128465;</button></td>
        </tr>`;
    }
    html += '</tbody></table>';

    body.innerHTML = html;
    document.getElementById('snapshot-drawer').classList.add('visible');
}

function snapSelect(which, id) {
    if (which === 'a') snapshotSelectA = id;
    else snapshotSelectB = id;
    const btn = document.getElementById('snap-compare-btn');
    btn.disabled = !(snapshotSelectA && snapshotSelectB && snapshotSelectA !== snapshotSelectB);
}

async function takeSnapshot() {
    const label = prompt('Snapshot label:', `Manual - ${new Date().toLocaleDateString()}`);
    if (!label) return;

    const data = await api('/api/snapshot', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({label}),
    });

    if (data && data.ok) {
        log(`Snapshot saved: ${data.snapshot.sku_count} SKUs, ${data.snapshot.total_units.toLocaleString()} units`, 'green');
        showSnapshots();
    } else {
        log('Failed to save snapshot', 'red');
    }
}

async function deleteSnapshot(id) {
    if (!confirm('Delete this snapshot?')) return;
    await api(`/api/snapshot/${id}`, {method: 'DELETE'});
    showSnapshots();
}

async function compareSnapshots() {
    if (!snapshotSelectA || !snapshotSelectB) return;

    const data = await api(`/api/snapshot_compare?a=${snapshotSelectA}&b=${snapshotSelectB}`);
    if (!data || data.error) {
        log('Comparison failed: ' + (data?.error || 'unknown'), 'red');
        return;
    }

    const body = document.getElementById('snapshot-compare-body');
    const title = document.getElementById('snapshot-compare-title');
    title.textContent = `${data.a.label} vs ${data.b.label}`;

    const s = data.summary;
    let html = `<div style="display:flex;gap:16px;padding:10px 12px;border-bottom:1px solid var(--border)">
        <div class="cal-summary"><div class="cal-summary-val" style="font-family:'Rajdhani',sans-serif;font-size:18px;font-weight:600">${s.net_change >= 0 ? '+' : ''}${s.net_change.toLocaleString()}</div><div class="cal-summary-label">Net Change</div></div>
        <div class="cal-summary"><div class="cal-summary-val" style="font-family:'Rajdhani',sans-serif;font-size:18px">${s.total_a.toLocaleString()}</div><div class="cal-summary-label">${data.a.cycle_day || 'A'}</div></div>
        <div class="cal-summary"><div class="cal-summary-val" style="font-family:'Rajdhani',sans-serif;font-size:18px">${s.total_b.toLocaleString()}</div><div class="cal-summary-label">${data.b.cycle_day || 'B'}</div></div>
        <div class="cal-summary"><div class="cal-summary-val" style="color:var(--green)">${s.skus_increased}</div><div class="cal-summary-label">Increased</div></div>
        <div class="cal-summary"><div class="cal-summary-val" style="color:var(--red)">${s.skus_decreased}</div><div class="cal-summary-label">Decreased</div></div>
    </div>`;

    // Sort by absolute delta descending
    const rows = data.rows.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));

    // Check if any rows have potential yield data
    const hasPotential = rows.some(r => (r.potential_a || 0) > 0 || (r.potential_b || 0) > 0);
    const potHeader = hasPotential ? '<th class="num">Pot. A</th><th class="num">Pot. B</th>' : '';

    html += `<table><thead><tr>
        <th>SKU</th>
        <th class="num">${data.a.label.substring(0, 20)}</th>
        <th class="num">${data.b.label.substring(0, 20)}</th>
        <th class="num">Delta</th>
        <th class="num">%</th>
        ${potHeader}
    </tr></thead><tbody>`;

    for (const r of rows) {
        const deltaColor = r.delta > 0 ? 'var(--green)' : r.delta < 0 ? 'var(--red)' : 'var(--fg2)';
        const pctStr = r.pct_change !== null ? `${r.pct_change > 0 ? '+' : ''}${r.pct_change}%` : '-';
        const potCells = hasPotential
            ? `<td class="num" style="font-family:'Rajdhani',sans-serif;color:var(--blue)">${(r.potential_a || 0) > 0 ? '+' + r.potential_a.toLocaleString() : '-'}</td>
               <td class="num" style="font-family:'Rajdhani',sans-serif;color:var(--blue)">${(r.potential_b || 0) > 0 ? '+' + r.potential_b.toLocaleString() : '-'}</td>`
            : '';
        html += `<tr>
            <td style="font-family:'Space Mono',monospace;font-size:10px">${r.sku}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${r.qty_a.toLocaleString()}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${r.qty_b.toLocaleString()}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif;color:${deltaColor};font-weight:600">${r.delta > 0 ? '+' : ''}${r.delta.toLocaleString()}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif;color:${deltaColor};font-size:11px">${pctStr}</td>
            ${potCells}
        </tr>`;
    }
    html += '</tbody></table>';

    body.innerHTML = html;
    document.getElementById('snapshot-drawer').classList.remove('visible');
    document.getElementById('snapshot-compare-drawer').classList.add('visible');
}

// ── Smart Auto-Sync ─────────────────────────────────────────────────

let lastDropboxSync = 0;
let lastRechargeSync = 0;
let lastShopifySync = 0;
let autoSyncInterval = null;

async function initAutoSync() {
    // Fetch settings for auto_refresh_interval
    const data = await api('/api/data');
    const intervalMins = data?.auto_refresh_interval || 60;
    if (intervalMins <= 0) return;

    const intervalMs = intervalMins * 60 * 1000;

    // Set up recurring refresh
    if (autoSyncInterval) clearInterval(autoSyncInterval);
    autoSyncInterval = setInterval(() => {
        smartSync(false);
    }, intervalMs);

    log(`Auto-sync: every ${intervalMins}min`, 'cyan');

    // Wednesday 10am cut order email check — only fetch settings on Wednesdays
    setInterval(async () => {
        const now = new Date();
        if (now.getDay() !== 3) return;
        if (now.getHours() !== 10 || now.getMinutes() > 5) return;

        const todayStr = now.toISOString().slice(0, 10);
        if (window._lastCutOrderEmailDate === todayStr) return;

        const settings = await api('/api/data');
        if (!settings?.cut_order_email_schedule?.enabled) return;

        window._lastCutOrderEmailDate = todayStr;
        log('Auto-sending Wednesday cut order email...', 'cyan');
        await loadCutOrder();
        await emailCutOrder();
    }, 60000);
}

async function smartSync(isStartup = false) {
    const now = Date.now();
    const day = new Date().getDay(); // 0=Sun, 1=Mon, 5=Fri, 6=Sat
    const minGap = 10 * 60 * 1000; // 10 minute minimum between same-source syncs

    // Dropbox: prioritize on Friday(5) and Monday(1) — inventory snapshot days
    if (now - lastDropboxSync > minGap) {
        const dbStatus = await api('/api/dropbox_status');
        if (dbStatus?.configured) {
            if (day === 1 || day === 5 || now - lastDropboxSync > 30 * 60 * 1000) {
                log('Auto-sync: Dropbox...', 'cyan');
                const ok = await syncDropbox();
                if (ok) lastDropboxSync = now;
            }
        }
    }

    // Recharge: sync if stale (>2 hours or first sync of session)
    if (now - lastRechargeSync > 2 * 60 * 60 * 1000) {
        const rcStatus = await api('/api/recharge_status');
        if (rcStatus?.configured) {
            log('Auto-sync: Recharge...', 'cyan');
            const ok = await syncRecharge();
            if (ok) lastRechargeSync = now;
        }
    }

    // Shopify: sync if stale (>2 hours)
    if (now - lastShopifySync > 2 * 60 * 60 * 1000) {
        const shStatus = await api('/api/shopify_status');
        if (shStatus?.configured) {
            log('Auto-sync: Shopify...', 'cyan');
            const ok = await syncShopify();
            if (ok) lastShopifySync = now;
        }
    }

    // Recalculate after syncs
    await calculateRMFG();
}

// ── Morning Briefing ────────────────────────────────────────────────

async function loadBriefing() {
    const data = await api('/api/briefing');
    if (!data || data.error) return;

    const card = document.getElementById('briefing-card');
    document.getElementById('briefing-day').textContent = data.weekday;
    document.getElementById('briefing-date').textContent = data.date;
    document.getElementById('briefing-hint').textContent = data.action_hint;

    const grid = document.getElementById('briefing-grid');
    let cells = '';

    // Shortages cell
    const sClass = data.shortage_count > 0 ? 'shortage' : 'ok';
    const sDetail = data.shortage_count > 0
        ? data.shortages.slice(0, 3).map(s => `${s.sku} (-${s.deficit})`).join(', ')
        : 'All clear';
    cells += `<div class="briefing-cell">
        <div class="briefing-cell-label">Shortages</div>
        <div class="briefing-cell-value ${sClass}">${data.shortage_count}</div>
        <div class="briefing-cell-detail">${sDetail}</div>
    </div>`;

    // Tight cell
    const tClass = data.tight_count > 0 ? 'tight' : 'ok';
    cells += `<div class="briefing-cell">
        <div class="briefing-cell-label">Tight</div>
        <div class="briefing-cell-value ${tClass}">${data.tight_count}</div>
        <div class="briefing-cell-detail">${data.tight_count > 0 ? data.tight.slice(0, 2).map(t => t.sku).join(', ') : 'Comfortable'}</div>
    </div>`;

    // Expiring cell
    const eClass = data.expiring_count > 0 ? 'shortage' : 'ok';
    const eDetail = data.expiring_count > 0
        ? data.expiring.slice(0, 2).map(e => `${e.sku} (${e.days_left}d)`).join(', ')
        : 'None within 7 days';
    cells += `<div class="briefing-cell">
        <div class="briefing-cell-label">Expiring</div>
        <div class="briefing-cell-value ${eClass}">${data.expiring_count}</div>
        <div class="briefing-cell-detail">${eDetail}</div>
    </div>`;

    // Tuesday coverage gaps
    const gClass = data.tue_gaps.length > 0 ? 'tight' : 'ok';
    const gDetail = data.tue_gaps.length > 0
        ? data.tue_gaps.slice(0, 2).map(g => `${g.sku} (-${g.gap})`).join(', ')
        : 'Covered';
    cells += `<div class="briefing-cell">
        <div class="briefing-cell-label">Tue Gaps</div>
        <div class="briefing-cell-value ${gClass}">${data.tue_gaps.length}</div>
        <div class="briefing-cell-detail">${gDetail}</div>
    </div>`;

    // Inventory summary
    cells += `<div class="briefing-cell">
        <div class="briefing-cell-label">Inventory</div>
        <div class="briefing-cell-value info">${data.total_cheese_skus}</div>
        <div class="briefing-cell-detail">${data.total_cheese_units.toLocaleString()} units</div>
    </div>`;

    // Forecast accuracy (if available)
    if (data.recent_accuracy && data.recent_accuracy.length > 0) {
        const last = data.recent_accuracy[data.recent_accuracy.length - 1];
        const mapeClass = last.mape <= 10 ? 'ok' : last.mape <= 20 ? 'tight' : 'shortage';
        cells += `<div class="briefing-cell" style="cursor:pointer" onclick="showAccuracy()">
            <div class="briefing-cell-label">Forecast MAPE</div>
            <div class="briefing-cell-value ${mapeClass}">${last.mape}%</div>
            <div class="briefing-cell-detail">Last ${data.recent_accuracy.length} records</div>
        </div>`;
    }

    grid.innerHTML = cells;
    card.style.display = '';
}

function closeBriefing() {
    document.getElementById('briefing-card').style.display = 'none';
}


// ── Forecast Accuracy ───────────────────────────────────────────────

async function recordForecastAccuracy(depletionSkus, window) {
    const data = await api('/api/forecast_accuracy', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            depletion_skus: depletionSkus,
            window: window,
            date: new Date().toISOString().split('T')[0],
        }),
    });
    if (data && data.ok) {
        const r = data.record;
        log(`Forecast accuracy recorded: MAPE ${r.mape}%, overall ${r.overall_pct_error > 0 ? '+' : ''}${r.overall_pct_error}%`, 'blue');
    }
}

async function showAccuracy() {
    const data = await api('/api/forecast_accuracy/summary');
    if (!data || !data.has_data) {
        log('No forecast accuracy data yet. Apply a depletion to start tracking.', 'yellow');
        return;
    }

    const body = document.getElementById('accuracy-drawer-body');
    let html = `<div style="display:flex;gap:16px;padding:10px 12px;border-bottom:1px solid var(--border)">
        <div class="cal-summary"><div class="cal-summary-val" style="font-family:'Rajdhani',sans-serif;font-size:18px;font-weight:600;color:${data.recent_mape <= 10 ? 'var(--green)' : data.recent_mape <= 20 ? 'var(--yellow)' : 'var(--red)'}">${data.recent_mape}%</div><div class="cal-summary-label">Avg MAPE</div></div>
        <div class="cal-summary"><div class="cal-summary-val" style="font-family:'Rajdhani',sans-serif;font-size:18px">${data.recent_overall_error > 0 ? '+' : ''}${data.recent_overall_error}%</div><div class="cal-summary-label">Avg Bias</div></div>
        <div class="cal-summary"><div class="cal-summary-val" style="font-family:'Rajdhani',sans-serif;font-size:18px">${data.total_records}</div><div class="cal-summary-label">Records</div></div>
    </div>`;

    // Trend table
    if (data.trend && data.trend.length > 0) {
        html += `<div style="padding:8px 12px"><div style="font-family:'Space Mono',monospace;font-size:10px;text-transform:uppercase;color:var(--fg2);margin-bottom:6px">Recent History</div>`;
        html += `<table><thead><tr>
            <th>Date</th><th>Window</th><th class="num">Predicted</th><th class="num">Actual</th><th class="num">MAPE</th><th class="num">Bias</th>
        </tr></thead><tbody>`;
        for (const r of data.trend) {
            const mColor = r.mape <= 10 ? 'var(--green)' : r.mape <= 20 ? 'var(--yellow)' : 'var(--red)';
            html += `<tr>
                <td style="font-family:'Space Mono',monospace;font-size:10px">${r.date}</td>
                <td style="font-family:'Space Mono',monospace;font-size:10px;text-transform:uppercase">${r.window}</td>
                <td class="num" style="font-family:'Rajdhani',sans-serif">${r.total_predicted.toLocaleString()}</td>
                <td class="num" style="font-family:'Rajdhani',sans-serif">${r.total_actual.toLocaleString()}</td>
                <td class="num" style="font-family:'Rajdhani',sans-serif;color:${mColor}">${r.mape != null ? r.mape + '%' : '-'}</td>
                <td class="num" style="font-family:'Rajdhani',sans-serif">${r.overall_pct_error != null ? (r.overall_pct_error > 0 ? '+' : '') + r.overall_pct_error + '%' : '-'}</td>
            </tr>`;
        }
        html += '</tbody></table></div>';
    }

    // Biased SKUs
    if (data.biased_skus && data.biased_skus.length > 0) {
        html += `<div style="padding:8px 12px"><div style="font-family:'Space Mono',monospace;font-size:10px;text-transform:uppercase;color:var(--fg2);margin-bottom:6px">Consistently Biased SKUs</div>`;
        html += `<table><thead><tr>
            <th>SKU</th><th class="num">Avg Error</th><th>Direction</th><th class="num">Samples</th>
        </tr></thead><tbody>`;
        for (const s of data.biased_skus) {
            const dColor = s.direction === 'over' ? 'var(--red)' : 'var(--blue)';
            html += `<tr>
                <td style="font-family:'Space Mono',monospace;font-size:10px">${s.sku}</td>
                <td class="num" style="font-family:'Rajdhani',sans-serif;color:${dColor}">${s.avg_error_pct > 0 ? '+' : ''}${s.avg_error_pct}%</td>
                <td style="font-family:'Space Mono',monospace;font-size:10px;color:${dColor};text-transform:uppercase">${s.direction}</td>
                <td class="num" style="font-family:'Rajdhani',sans-serif">${s.samples}</td>
            </tr>`;
        }
        html += '</tbody></table></div>';
    }

    body.innerHTML = html;
    openDrawer('accuracy-drawer');
}


// ── Settings View ───────────────────────────────────────────────────

// ── Activity Log ─────────────────────────────────────────────────────

async function loadActivityLog() {
    const container = document.getElementById('log-timeline');
    if (!container) return;
    container.innerHTML = '<span style="color:var(--fg3)">Loading activity...</span>';

    try {
        const data = await api('/api/activity_log?days=60');
        if (!data || !data.events) {
            container.innerHTML = '<span style="color:var(--fg3)">No activity data.</span>';
            return;
        }

        // Update header stats
        const countEl = document.getElementById('log-event-count');
        const inEl = document.getElementById('log-inflow-total');
        const outEl = document.getElementById('log-outflow-total');
        if (countEl) countEl.textContent = data.event_count;
        if (inEl) inEl.textContent = '+' + data.total_in;
        if (outEl) outEl.textContent = data.total_out;

        // Group events by date
        const groups = {};
        for (const evt of data.events) {
            const d = evt.date || 'Unknown';
            if (!groups[d]) groups[d] = [];
            groups[d].push(evt);
        }

        let html = '';
        for (const [date, evts] of Object.entries(groups)) {
            html += `<div class="tl-date-group">${date}</div>`;
            for (const evt of evts) {
                const icon = evt.direction === 'in' ? '\u25B2' :
                             evt.direction === 'out' ? '\u25BC' : '\u25C6';
                const dirClass = 'tl-' + evt.direction;
                const units = evt.total_units !== 0
                    ? `<span class="tl-units ${dirClass}">${evt.total_units > 0 ? '+' : ''}${evt.total_units}</span>`
                    : '';
                const typeLabel = evt.type.replace(/_/g, ' ');
                const skuCount = evt.skus ? evt.skus.length : 0;
                const expandId = `tl-${date}-${evt.type}-${Math.random().toString(36).slice(2, 6)}`;

                html += `<div class="tl-entry ${dirClass}">`;
                html += `<span class="tl-icon ${dirClass}">${icon}</span>`;
                html += `<div class="tl-content">`;
                html += `<div class="tl-header">`;
                html += `<span class="tl-type">${typeLabel}</span>`;
                html += `<span class="tl-summary">${evt.summary}</span>`;
                html += units;
                if (skuCount > 0) {
                    html += `<span class="tl-expand" onclick="toggleTlDetail('${expandId}')">${skuCount} SKUs \u25BE</span>`;
                }
                html += `</div>`;
                // Expandable detail
                if (skuCount > 0) {
                    html += `<div class="tl-detail" id="${expandId}" style="display:none">`;
                    for (const s of evt.skus) {
                        const qClass = s.qty > 0 ? 'tl-in' : 'tl-out';
                        html += `<span class="tl-sku-item"><span class="${qClass}">${s.qty > 0 ? '+' : ''}${s.qty}</span> ${s.sku}</span>`;
                    }
                    html += `</div>`;
                }
                html += `</div></div>`;
            }
        }

        if (!html) html = '<span style="color:var(--fg3)">No events in the last 60 days.</span>';
        container.innerHTML = html;

    } catch (e) {
        container.innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`;
    }
}

function toggleTlDetail(id) {
    const el = document.getElementById(id);
    if (el) el.style.display = el.style.display === 'none' ? '' : 'none';
}

// ── Settings ─────────────────────────────────────────────────────────

async function loadSettingsView() {
    const data = await api('/api/settings_config');
    if (!data) return;

    // General
    document.getElementById('set-auto-refresh').value = data.auto_refresh_interval || 60;
    document.getElementById('set-fulfillment-buffer').value = data.fulfillment_buffer || '10';
    document.getElementById('set-expiration-days').value = data.expiration_warning_days || '14';
    document.getElementById('set-recon-pct').value = data.yield_reconciliation_threshold_pct || 5;
    document.getElementById('set-recon-min').value = data.yield_reconciliation_threshold_min || 2;

    // SMTP
    document.getElementById('set-smtp-host').value = data.smtp_host || 'smtp.gmail.com';
    document.getElementById('set-smtp-port').value = data.smtp_port || '587';
    document.getElementById('set-smtp-user').value = data.smtp_user || '';
    document.getElementById('set-smtp-pass').value = data.smtp_password || '';
    document.getElementById('set-email-from').value = data.depletion_email_from || '';
    document.getElementById('set-email-to').value = data.depletion_email_to || '';

    // Vendor catalog
    renderVendorCatalog(data.vendor_catalog || {});

    // Reorder points
    renderReorderPoints(data.reorder_points || {});
}

async function saveGeneralSettings() {
    const data = await api('/api/settings_config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            auto_refresh_interval: parseInt(document.getElementById('set-auto-refresh').value) || 60,
            fulfillment_buffer: document.getElementById('set-fulfillment-buffer').value,
            expiration_warning_days: document.getElementById('set-expiration-days').value,
            yield_reconciliation_threshold_pct: parseInt(document.getElementById('set-recon-pct').value) || 5,
            yield_reconciliation_threshold_min: parseInt(document.getElementById('set-recon-min').value) || 2,
        }),
    });
    if (data?.ok) log('General settings saved', 'green');
}

async function saveSmtpSettings() {
    const data = await api('/api/settings_config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            smtp_host: document.getElementById('set-smtp-host').value,
            smtp_port: document.getElementById('set-smtp-port').value,
            smtp_user: document.getElementById('set-smtp-user').value,
            smtp_password: document.getElementById('set-smtp-pass').value,
            depletion_email_from: document.getElementById('set-email-from').value,
            depletion_email_to: document.getElementById('set-email-to').value,
        }),
    });
    if (data?.ok) log('SMTP settings saved', 'green');
}

function renderVendorCatalog(catalog) {
    const body = document.getElementById('vendor-body');
    if (!body) return;
    let html = '';
    for (const [sku, v] of Object.entries(catalog).sort()) {
        html += `<tr>
            <td style="font-family:'Space Mono',monospace;font-size:10px">${sku}</td>
            <td style="font-family:'DM Sans',sans-serif;font-size:11px">${v.vendor || ''}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">$${(v.unit_cost || 0).toFixed(2)}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${v.case_qty || 1}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${v.moq || 0}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${v.wheel_weight_lbs || '-'}</td>
            <td><button class="btn btn-red btn-sm" onclick="deleteVendor('${sku}')">Del</button></td>
        </tr>`;
    }
    body.innerHTML = html || '<tr><td colspan="7" style="color:var(--fg2);font-size:11px;padding:12px">No vendor catalog entries. Click + Add to create one.</td></tr>';
}

async function addVendorRow() {
    const sku = prompt('SKU (e.g. CH-CHED):');
    if (!sku) return;
    const vendor = prompt('Vendor name:') || '';
    const unit_cost = parseFloat(prompt('Unit cost ($):') || '0');
    const case_qty = parseInt(prompt('Case quantity:') || '1');
    const data = await api('/api/vendor_catalog', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ sku, vendor, unit_cost, case_qty, moq: 0, wheel_weight_lbs: 0 }),
    });
    if (data?.ok) {
        log(`Added vendor entry: ${sku}`, 'green');
        loadSettingsView();
    }
}

async function deleteVendor(sku) {
    await api(`/api/vendor_catalog/${sku}`, { method: 'DELETE' });
    loadSettingsView();
}

function renderReorderPoints(rp) {
    const body = document.getElementById('reorder-body');
    if (!body) return;
    let html = '';
    for (const [sku, p] of Object.entries(rp).sort()) {
        html += `<tr>
            <td style="font-family:'Space Mono',monospace;font-size:10px">${sku}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${p.min_stock || 0}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${p.preferred_qty || 0}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${p.lead_days || 7}</td>
            <td><button class="btn btn-red btn-sm" onclick="deleteReorderPoint('${sku}')">Del</button></td>
        </tr>`;
    }
    body.innerHTML = html || '<tr><td colspan="5" style="color:var(--fg2);font-size:11px;padding:12px">No reorder points. Click + Add to set per-SKU thresholds.</td></tr>';
}

async function addReorderRow() {
    const sku = prompt('SKU (e.g. CH-CHED):');
    if (!sku) return;
    const min_stock = parseInt(prompt('Minimum stock level:') || '0');
    const preferred_qty = parseInt(prompt('Preferred order quantity (0 = auto):') || '0');
    const lead_days = parseInt(prompt('Lead time (days):') || '7');
    const data = await api('/api/reorder_points', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ sku, min_stock, preferred_qty, lead_days }),
    });
    if (data?.ok) {
        log(`Added reorder point: ${sku} (min: ${min_stock})`, 'green');
        loadSettingsView();
    }
}

async function deleteReorderPoint(sku) {
    const data = await api('/api/settings_config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ reorder_points: {} }),  // We need a delete endpoint
    });
    // For now, remove via full update
    const config = await api('/api/settings_config');
    if (config?.reorder_points) {
        delete config.reorder_points[sku];
        await api('/api/settings_config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ reorder_points: config.reorder_points }),
        });
        loadSettingsView();
    }
}


// ── Undo Depletion / Audit Trail ────────────────────────────────────

async function undoDepletion() {
    if (!confirm('Undo the last depletion? This will restore inventory to the pre-depletion snapshot.')) return;
    const data = await api('/api/undo_depletion', { method: 'POST' });
    if (data?.error) {
        log(`Undo failed: ${data.error}`, 'red');
        return;
    }
    log(`Undo: restored ${data.units_restored} units from "${data.restored_from}"`, 'green');
    setMascot('happy', 'Depletion undone!');
    await calculateRMFG();
}

async function showAuditLog() {
    const data = await api('/api/audit_log');
    if (!data?.entries) return;

    const body = document.getElementById('audit-drawer-body');
    let html = `<table><thead><tr>
        <th>Time</th><th>Action</th><th>Detail</th>
    </tr></thead><tbody>`;

    for (const e of data.entries) {
        const ts = e.timestamp ? new Date(e.timestamp).toLocaleString() : '';
        const actionColors = {
            depletion_applied: 'var(--orange)',
            undo_depletion: 'var(--green)',
            waste_recorded: 'var(--red)',
            po_emailed: 'var(--blue)',
            po_received: 'var(--green)',
            snapshot_dropbox: 'var(--accent)',
            snapshot_depletion: 'var(--orange)',
            snapshot_manual: 'var(--fg2)',
            snapshot_undo: 'var(--green)',
        };
        const color = actionColors[e.action] || 'var(--fg2)';
        html += `<tr>
            <td style="font-family:'Space Mono',monospace;font-size:10px;white-space:nowrap">${ts}</td>
            <td class="audit-action" style="color:${color}">${e.action}</td>
            <td style="font-family:'DM Sans',sans-serif;font-size:11px">${e.detail || ''}</td>
        </tr>`;
    }
    html += '</tbody></table>';
    body.innerHTML = html;
    openDrawer('audit-drawer');
}


// ── Waste / Spoilage Ledger ─────────────────────────────────────────

async function showWasteLedger() {
    const data = await api('/api/waste');
    if (!data) return;

    const body = document.getElementById('waste-drawer-body');
    let html = '';

    // Summary stats
    if (data.total_wasted > 0) {
        html += `<div style="display:flex;gap:16px;padding:10px 12px;border-bottom:1px solid var(--border)">
            <div class="cal-summary"><div class="cal-summary-val" style="font-family:'Rajdhani',sans-serif;font-size:18px;font-weight:600;color:var(--red)">${data.total_wasted}</div><div class="cal-summary-label">Total Wasted</div></div>
            <div class="cal-summary"><div class="cal-summary-val" style="font-family:'Rajdhani',sans-serif;font-size:18px">${Object.keys(data.by_sku).length}</div><div class="cal-summary-label">SKUs Affected</div></div>
            <div class="cal-summary"><div class="cal-summary-val" style="font-family:'Rajdhani',sans-serif;font-size:18px">${data.entries.length}</div><div class="cal-summary-label">Entries</div></div>
        </div>`;

        // By-reason breakdown
        html += `<div style="display:flex;gap:12px;padding:8px 12px;border-bottom:1px solid var(--border)">`;
        for (const [reason, qty] of Object.entries(data.by_reason)) {
            html += `<span style="font-family:'Space Mono',monospace;font-size:10px;color:var(--fg2)">${reason}: <span style="color:var(--red)">${qty}</span></span>`;
        }
        html += '</div>';
    }

    // Table
    html += `<table><thead><tr>
        <th>Date</th><th>SKU</th><th class="num">Qty</th><th>Reason</th><th>Actions</th>
    </tr></thead><tbody>`;

    for (const e of (data.entries || []).slice().reverse()) {
        html += `<tr>
            <td style="font-family:'Space Mono',monospace;font-size:10px">${e.date}</td>
            <td style="font-family:'Space Mono',monospace;font-size:10px">${e.sku}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif;color:var(--red)">${e.qty}</td>
            <td style="font-family:'DM Sans',sans-serif;font-size:11px">${e.reason}</td>
            <td><button class="btn btn-dim btn-sm" onclick="deleteWaste('${e.id}')">Del</button></td>
        </tr>`;
    }
    html += '</tbody></table>';

    if (!data.entries || data.entries.length === 0) {
        html = `<div style="padding:16px;color:var(--fg2);font-family:'Space Mono',monospace;font-size:10px;text-transform:uppercase">No waste recorded yet.</div>`;
    }

    body.innerHTML = html;
    openDrawer('waste-drawer');
}

function showRecordWaste() {
    const sku = prompt('SKU to waste (e.g. CH-BRIE):');
    if (!sku) return;
    const qty = parseInt(prompt('Quantity wasted:') || '0');
    if (qty <= 0) return;
    const reason = prompt('Reason (spoilage/damaged/expired/other):', 'spoilage') || 'spoilage';
    recordWaste(sku, qty, reason);
}

async function recordWaste(sku, qty, reason) {
    const data = await api('/api/waste', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ sku, qty, reason }),
    });
    if (data?.ok) {
        log(`Waste: ${sku} x${qty} (${reason})`, 'orange');
        showWasteLedger();
    }
}

async function deleteWaste(id) {
    await api(`/api/waste/${id}`, { method: 'DELETE' });
    showWasteLedger();
}


// ── Email Wednesday PO ──────────────────────────────────────────────

async function emailPO() {
    if (!confirm('Email the current order list via SMTP?')) return;

    // Collect lines from the order drawer table
    const rows = document.querySelectorAll('#order-drawer-body table tbody tr');
    const lines = [];
    rows.forEach(row => {
        const cells = row.querySelectorAll('td');
        if (cells.length >= 4) {
            lines.push({
                sku: cells[0]?.textContent?.trim() || '',
                order_qty: parseInt(cells[1]?.textContent?.trim()) || 0,
                cases: parseInt(cells[2]?.textContent?.trim()) || 0,
                case_qty: parseInt(cells[3]?.textContent?.trim()) || 1,
                vendor: cells[4]?.textContent?.trim() || '',
                line_cost: parseFloat(cells[5]?.textContent?.replace('$', '').trim()) || 0,
            });
        }
    });

    if (lines.length === 0) {
        log('No order lines to email', 'yellow');
        return;
    }

    const data = await api('/api/email_po', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ lines }),
    });

    if (data?.error) {
        log(`Email failed: ${data.error}`, 'red');
    } else if (data?.ok) {
        log(`PO emailed to ${data.sent_to} (${data.lines} lines)`, 'green');
        setMascot('happy', 'PO sent!');
    }
}


// ── Wed PO Draft (from reorder points) ──────────────────────────────

async function showPODraft() {
    const data = await api('/api/wed_po_draft');
    if (!data) return;

    if (data.message) {
        log(data.message, 'yellow');
        return;
    }

    if (data.lines.length === 0) {
        log('All SKUs above reorder points — no orders needed', 'green');
        return;
    }

    // Render in order drawer
    const body = document.getElementById('order-drawer-body');
    let html = `<div style="display:flex;gap:16px;padding:10px 12px;border-bottom:1px solid var(--border)">
        <div class="cal-summary"><div class="cal-summary-val" style="font-family:'Rajdhani',sans-serif;font-size:18px;font-weight:600">${data.total_lines}</div><div class="cal-summary-label">Order Lines</div></div>
        <div class="cal-summary"><div class="cal-summary-val" style="font-family:'Rajdhani',sans-serif;font-size:18px">$${data.total_cost.toLocaleString()}</div><div class="cal-summary-label">Total Cost</div></div>
    </div>`;

    html += `<table><thead><tr>
        <th>SKU</th><th class="num">Order Qty</th><th class="num">Cases</th><th class="num">Case Qty</th>
        <th>Vendor</th><th class="num">Cost</th><th class="num">Current</th><th class="num">Min</th><th class="num">Deficit</th>
    </tr></thead><tbody>`;

    for (const l of data.lines) {
        html += `<tr>
            <td style="font-family:'Space Mono',monospace;font-size:10px">${l.sku}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif;font-weight:600">${l.order_qty}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${l.cases}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${l.case_qty}</td>
            <td style="font-family:'DM Sans',sans-serif;font-size:11px">${l.vendor || '-'}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${l.line_cost > 0 ? '$' + l.line_cost.toFixed(2) : '-'}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif;color:var(--red)">${l.current}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${l.min_stock}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif;color:var(--red)">${l.deficit}</td>
        </tr>`;
    }
    html += '</tbody></table>';

    body.innerHTML = html;
    openDrawer('order-drawer');
}


// ── Sparklines in NET Table ─────────────────────────────────────────

let skuHistoryCache = null;

async function loadSkuHistory() {
    const data = await api('/api/sku_history');
    if (data?.count > 1) {
        skuHistoryCache = data.history;
    }
}

function renderSparkline(sku) {
    if (!skuHistoryCache || !skuHistoryCache[sku]) return '';
    const vals = skuHistoryCache[sku];
    if (vals.length < 2) return '';

    const w = 48, h = 16;
    const max = Math.max(...vals, 1);
    const min = Math.min(...vals, 0);
    const range = max - min || 1;
    const step = w / (vals.length - 1);

    let path = '';
    vals.forEach((v, i) => {
        const x = i * step;
        const y = h - ((v - min) / range) * h;
        path += (i === 0 ? 'M' : 'L') + `${x.toFixed(1)},${y.toFixed(1)}`;
    });

    // Color based on trend
    const first = vals[0], last = vals[vals.length - 1];
    const color = last > first ? 'var(--green)' : last < first ? 'var(--red)' : 'var(--fg2)';

    return `<svg class="sparkline" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
        <path d="${path}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
}


// ── Supplier Lead Times ─────────────────────────────────────────────

async function showLeadTimes() {
    const data = await api('/api/lead_times');
    if (!data) return;

    const body = document.getElementById('leadtime-drawer-body');

    if (!data.has_data) {
        body.innerHTML = `<div style="padding:16px;color:var(--fg2);font-family:'Space Mono',monospace;font-size:10px;text-transform:uppercase">
            No lead time data yet. Mark POs as received to start tracking.</div>`;
        openDrawer('leadtime-drawer');
        return;
    }

    let html = '';

    // Vendor summary
    html += `<div style="padding:8px 12px"><div style="font-family:'Space Mono',monospace;font-size:10px;text-transform:uppercase;color:var(--fg2);margin-bottom:6px">By Vendor</div>`;
    html += `<table><thead><tr>
        <th>Vendor</th><th class="num">Avg Days</th><th class="num">Min</th><th class="num">Max</th><th class="num">Records</th>
    </tr></thead><tbody>`;
    for (const [vendor, s] of Object.entries(data.by_vendor).sort()) {
        html += `<tr>
            <td style="font-family:'DM Sans',sans-serif;font-size:11px">${vendor}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif;font-weight:600">${s.avg_days}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${s.min_days}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${s.max_days}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${s.count}</td>
        </tr>`;
    }
    html += '</tbody></table></div>';

    // By SKU
    if (Object.keys(data.by_sku).length > 0) {
        html += `<div style="padding:8px 12px"><div style="font-family:'Space Mono',monospace;font-size:10px;text-transform:uppercase;color:var(--fg2);margin-bottom:6px">By SKU</div>`;
        html += `<table><thead><tr>
            <th>SKU</th><th class="num">Avg Days</th><th class="num">Records</th>
        </tr></thead><tbody>`;
        for (const [sku, s] of Object.entries(data.by_sku).sort()) {
            html += `<tr>
                <td style="font-family:'Space Mono',monospace;font-size:10px">${sku}</td>
                <td class="num" style="font-family:'Rajdhani',sans-serif;font-weight:600">${s.avg_days}</td>
                <td class="num" style="font-family:'Rajdhani',sans-serif">${s.count}</td>
            </tr>`;
        }
        html += '</tbody></table></div>';
    }

    // Recent entries
    html += `<div style="padding:8px 12px"><div style="font-family:'Space Mono',monospace;font-size:10px;text-transform:uppercase;color:var(--fg2);margin-bottom:6px">Recent Receipts</div>`;
    html += `<table><thead><tr>
        <th>SKU</th><th>Vendor</th><th>Placed</th><th>Received</th><th class="num">Days</th><th class="num">Qty</th>
    </tr></thead><tbody>`;
    for (const e of data.entries.slice().reverse()) {
        html += `<tr>
            <td style="font-family:'Space Mono',monospace;font-size:10px">${e.sku}</td>
            <td style="font-family:'DM Sans',sans-serif;font-size:11px">${e.vendor || '-'}</td>
            <td style="font-family:'Space Mono',monospace;font-size:10px">${e.placed_date || '-'}</td>
            <td style="font-family:'Space Mono',monospace;font-size:10px">${e.received_date}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif;font-weight:600">${e.actual_lead_days != null ? e.actual_lead_days : '-'}</td>
            <td class="num" style="font-family:'Rajdhani',sans-serif">${e.qty}</td>
        </tr>`;
    }
    html += '</tbody></table></div>';

    body.innerHTML = html;
    openDrawer('leadtime-drawer');
}

// ══════════════════════════════════════════════════════════════════════
//  CUT ORDER VIEW
// ══════════════════════════════════════════════════════════════════════

let cutOrderData = null;

// ── Cut Order Interactive Calculator ──────────────────────────────
let coData = null;     // raw data from /api/cut_order_interactive
let coCuts = {wk1: {}, wk2: {}};  // user's cut inputs
let coSaveTimer = null;

const CutOrderCalc = {
    /** Resolve raw demand components using current assignments (client-side SUMIF). */
    resolve(rawComponents, assignments) {
        const demand = {};
        const add = (sku, qty) => { demand[sku] = (demand[sku] || 0) + qty; };

        // Direct demand (pickable items already resolved)
        for (const [sku, qty] of Object.entries(rawComponents.direct || {})) {
            if (qty > 0) add(sku, qty);
        }

        // PR-CJAM: each curation's count → assigned cheese SKU
        const prCjam = assignments.pr_cjam || {};
        for (const [cur, count] of Object.entries(rawComponents.prcjam_counts || {})) {
            const cfg = prCjam[cur];
            if (cfg && cfg.cheese) add(cfg.cheese, count);
        }

        // CEX-EC: each curation's count → assigned cheese (with splits)
        const cexEc = assignments.cex_ec || {};
        const splits = assignments.cexec_splits || {};
        for (const [cur, count] of Object.entries(rawComponents.cexec_counts || {})) {
            if (cur === 'BARE') continue;
            const splitCfg = splits[cur];
            if (splitCfg && Object.keys(splitCfg).length > 0) {
                for (const [sku, ratio] of Object.entries(splitCfg)) {
                    if (ratio > 0) add(sku, Math.round(count * ratio));
                }
            } else {
                const cheese = cexEc[cur];
                if (cheese) add(cheese, count);
            }
        }

        return demand;
    },

    /** Calculate all rows for the interactive table. */
    calculate(data, cuts, assignments) {
        const wk1Demand = this.resolve(data.raw_components.wk1, assignments);
        const wk2Demand = data.wk2_demand || {};
        const rows = [];

        const catOrder = {'CH-': 0, 'MT-': 1, 'AC-': 2};
        const skus = Object.keys(data.skus).sort((a, b) => {
            const ca = catOrder[a.substring(0, 3)] ?? 9;
            const cb = catOrder[b.substring(0, 3)] ?? 9;
            return ca !== cb ? ca - cb : a.localeCompare(b);
        });

        for (const sku of skus) {
            const info = data.skus[sku];
            const sliced = info.sliced || 0;
            const supply = (info.wheel_potential || 0) + (info.bulk_potential || 0);
            const avail = sliced;
            const dmdW1 = Math.round(wk1Demand[sku] || 0);
            const dmdW2 = Math.round(wk2Demand[sku] || 0);
            const cutW1 = parseInt(cuts.wk1[sku]) || 0;
            const cutW2 = parseInt(cuts.wk2[sku]) || 0;

            const afterW1 = avail - dmdW1;
            const goodW1 = (afterW1 + cutW1) >= 0;
            const needW1 = goodW1 ? 0 : Math.abs(afterW1 + cutW1);

            const availW2 = afterW1 + cutW1;
            const afterW2 = availW2 - dmdW2;
            const goodW2 = (afterW2 + cutW2) >= 0;
            const needW2 = goodW2 ? 0 : Math.abs(afterW2 + cutW2);

            rows.push({
                sku, name: info.name || '', prefix: sku.substring(0, 3),
                sliced, supply, avail,
                dmdW1, afterW1, cutW1, goodW1, needW1,
                dmdW2, afterW2, cutW2, goodW2, needW2,
            });
        }
        return rows;
    },
};

async function loadCutOrder() { return loadCutOrderInteractive(); }

async function loadCutOrderInteractive() {
    log('Loading interactive cut order...', 'cyan');
    const data = await api('/api/cut_order_interactive', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({}),
    });
    if (data.error) {
        log('Cut order error: ' + data.error, 'red');
        return;
    }
    coData = data;
    coCuts = data.saved_cuts || {wk1: {}, wk2: {}};
    if (!coCuts.wk1) coCuts.wk1 = {};
    if (!coCuts.wk2) coCuts.wk2 = {};
    // Update column headers with actual ship dates
    if (data.ship_dates) {
        const sd = data.ship_dates;
        const hdrDmdW1 = document.getElementById('co-hdr-dmd-w1');
        const hdrAfterW1 = document.getElementById('co-hdr-after-w1');
        const hdrDmdW2 = document.getElementById('co-hdr-dmd-w2');
        const hdrAfterW2 = document.getElementById('co-hdr-after-w2');
        if (hdrDmdW1) hdrDmdW1.textContent = 'Dmd ' + sd.label_wk1;
        if (hdrAfterW1) hdrAfterW1.textContent = 'After W1';
        if (hdrDmdW2) hdrDmdW2.textContent = 'Dmd ' + sd.label_wk2;
        if (hdrAfterW2) hdrAfterW2.textContent = 'After W2';
    }
    renderCutOrderInteractive();
    renderCoAssignPanel();
    // Show demand source indicator
    const srcEl = document.getElementById('co-demand-source');
    if (srcEl && data.demand_source) {
        const src = data.demand_source;
        srcEl.textContent = src === 'api' ? 'RC+SH API' : src === 'rmfg' ? 'RMFG Folder' : 'No data';
        srcEl.style.color = src === 'api' ? 'var(--green, #00e676)' : src === 'none' ? 'var(--red, #ff3b5c)' : 'var(--orange, #ff8800)';
    }
    log(`Cut order: ${Object.keys(data.skus).length} SKUs loaded`, 'green');
}

function renderCutOrderInteractive() {
    if (!coData) return;
    const assignments = coData.assignments;
    const rows = CutOrderCalc.calculate(coData, coCuts, assignments);
    const hideZero = document.getElementById('co-hide-zero')?.checked;

    const tbody = document.getElementById('co-body');
    tbody.innerHTML = '';

    let currentCat = '';
    const catLabels = {'CH-': 'CHEESE', 'MT-': 'MEAT', 'AC-': 'ACCOMPANIMENTS'};
    let totalDmd = 0, wk1Needs = 0, wk2Needs = 0, skuCount = 0;

    for (const r of rows) {
        // Skip meat — not part of cut order
        if (r.prefix === 'MT-') continue;
        // Filter zero rows
        if (hideZero && r.dmdW1 === 0 && r.dmdW2 === 0 && r.sliced === 0 && r.supply === 0) continue;

        // Category header
        if (r.prefix !== currentCat) {
            currentCat = r.prefix;
            const catTr = document.createElement('tr');
            catTr.className = 'co-cat-row';
            catTr.innerHTML = `<td colspan="14">${catLabels[r.prefix] || r.prefix}</td>`;
            tbody.appendChild(catTr);
        }

        skuCount++;
        totalDmd += r.dmdW1 + r.dmdW2;
        if (!r.goodW1) wk1Needs++;
        if (!r.goodW2 && r.dmdW2 > 0) wk2Needs++;

        const tr = document.createElement('tr');
        tr.dataset.sku = r.sku;
        // Row-level color affordance
        const hasShortage = !r.goodW1 || (!r.goodW2 && r.dmdW2 > 0);
        const allOk = r.goodW1 && (r.goodW2 || r.dmdW2 === 0) && (r.dmdW1 > 0 || r.dmdW2 > 0);
        if (hasShortage) tr.className = 'co-row-need';
        else if (allOk) tr.className = 'co-row-ok';

        const supplyBadge = r.supply > 0
            ? `<span class="co-supply-badge">+${r.supply}</span>` : '';
        const afterW1Class = r.afterW1 >= 0 ? 'co-after-ok' : 'co-after-short';
        const afterW2Class = r.afterW2 >= 0 ? 'co-after-ok' : 'co-after-short';
        const goodW1Html = r.dmdW1 === 0 ? '' : r.goodW1
            ? '<span class="co-good-ok">OK</span>'
            : `<span class="co-good-need">NEED ${r.needW1}</span>`;
        const goodW2Html = r.dmdW2 === 0 ? '' : r.goodW2
            ? '<span class="co-good-ok">OK</span>'
            : `<span class="co-good-need">NEED ${r.needW2}</span>`;

        tr.innerHTML = `
            <td class="co-sku-cell" onclick="showAttribution('${r.sku}')">${r.sku}</td>
            <td class="co-name-cell">${r.name}</td>
            <td class="num co-num">${r.sliced || ''}</td>
            <td class="num co-num">${supplyBadge}</td>
            <td class="co-wk-sep"></td>
            <td class="num" data-field="dmdW1">${r.dmdW1 || ''}</td>
            <td class="num ${afterW1Class}" data-field="afterW1">${r.dmdW1 ? r.afterW1 : ''}</td>
            <td class="num"><input type="number" class="co-cut-input" min="0"
                data-sku="${r.sku}" data-week="wk1"
                value="${r.cutW1 || ''}"
                oninput="onCutInput(this)"></td>
            <td class="num" data-field="goodW1">${goodW1Html}</td>
            <td class="co-wk-sep"></td>
            <td class="num" data-field="dmdW2">${r.dmdW2 || ''}</td>
            <td class="num ${afterW2Class}" data-field="afterW2">${r.dmdW2 ? r.afterW2 : ''}</td>
            <td class="num"><input type="number" class="co-cut-input" min="0"
                data-sku="${r.sku}" data-week="wk2"
                value="${r.cutW2 || ''}"
                oninput="onCutInput(this)"></td>
            <td class="num" data-field="goodW2">${goodW2Html}</td>
        `;
        tbody.appendChild(tr);
    }

    // Summary bar
    const el = id => document.getElementById(id);
    el('co-total-demand').textContent = totalDmd.toLocaleString();
    el('co-sku-count').textContent = skuCount;
    el('co-wk1-needs').textContent = wk1Needs || '0';
    el('co-wk1-needs').className = 'cal-summary-value ' + (wk1Needs > 0 ? 'inv-pending' : '');
    el('co-wk2-needs').textContent = wk2Needs || '0';
    el('co-wk2-needs').className = 'cal-summary-value ' + (wk2Needs > 0 ? 'inv-pending' : '');
}

function onCutInput(input) {
    const sku = input.dataset.sku;
    const week = input.dataset.week;
    const val = parseInt(input.value) || 0;
    coCuts[week][sku] = val > 0 ? val : undefined;
    if (val === 0) delete coCuts[week][sku];

    // Recalc just this row (fast path)
    recalcRow(sku);

    // Debounced save
    clearTimeout(coSaveTimer);
    coSaveTimer = setTimeout(saveCutQuantities, 800);

    // Update summary
    updateCoSummary();
}

function recalcRow(sku) {
    if (!coData) return;
    const info = coData.skus[sku];
    if (!info) return;
    const assignments = coData.assignments;

    const wk1Demand = CutOrderCalc.resolve(coData.raw_components.wk1, assignments);
    const wk2Demand = coData.wk2_demand || {};

    const sliced = info.sliced || 0;
    const dmdW1 = Math.round(wk1Demand[sku] || 0);
    const dmdW2 = Math.round(wk2Demand[sku] || 0);
    const cutW1 = parseInt(coCuts.wk1[sku]) || 0;
    const cutW2 = parseInt(coCuts.wk2[sku]) || 0;
    const afterW1 = sliced - dmdW1;
    const goodW1 = (afterW1 + cutW1) >= 0;
    const needW1 = goodW1 ? 0 : Math.abs(afterW1 + cutW1);
    const afterW2 = (afterW1 + cutW1) - dmdW2;
    const goodW2 = (afterW2 + cutW2) >= 0;
    const needW2 = goodW2 ? 0 : Math.abs(afterW2 + cutW2);

    const tr = document.querySelector(`tr[data-sku="${sku}"]`);
    if (!tr) return;

    const set = (field, val, cls) => {
        const td = tr.querySelector(`[data-field="${field}"]`);
        if (td) {
            td.textContent = val;
            if (cls !== undefined) td.className = 'num ' + cls;
        }
    };
    const setHtml = (field, html) => {
        const td = tr.querySelector(`[data-field="${field}"]`);
        if (td) td.innerHTML = html;
    };

    set('afterW1', dmdW1 ? afterW1 : '', afterW1 >= 0 ? 'co-after-ok' : 'co-after-short');
    setHtml('goodW1', dmdW1 === 0 ? '' : goodW1
        ? '<span class="co-good-ok">OK</span>'
        : `<span class="co-good-need">NEED ${needW1}</span>`);
    set('afterW2', dmdW2 ? afterW2 : '', afterW2 >= 0 ? 'co-after-ok' : 'co-after-short');
    setHtml('goodW2', dmdW2 === 0 ? '' : goodW2
        ? '<span class="co-good-ok">OK</span>'
        : `<span class="co-good-need">NEED ${needW2}</span>`);
}

function recalcCutOrder() {
    renderCutOrderInteractive();
}

function updateCoSummary() {
    if (!coData) return;
    const rows = CutOrderCalc.calculate(coData, coCuts, coData.assignments);
    let wk1Needs = 0, wk2Needs = 0;
    for (const r of rows) {
        if (!r.goodW1) wk1Needs++;
        if (!r.goodW2 && r.dmdW2 > 0) wk2Needs++;
    }
    const el = id => document.getElementById(id);
    el('co-wk1-needs').textContent = wk1Needs || '0';
    el('co-wk1-needs').className = 'cal-summary-value ' + (wk1Needs > 0 ? 'inv-pending' : '');
    el('co-wk2-needs').textContent = wk2Needs || '0';
    el('co-wk2-needs').className = 'cal-summary-value ' + (wk2Needs > 0 ? 'inv-pending' : '');
}

async function saveCutQuantities() {
    await api('/api/cut_quantities', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(coCuts),
    });
}

function toggleCoAssignDrawer() {
    // Assignments panel is always visible — no-op
}

function renderCoAssignPanel() {
    if (!coData) return;
    const body = document.getElementById('co-assign-body');
    const assigns = coData.assignments;
    const prCjam = assigns.pr_cjam || {};
    const cexEc = assigns.cex_ec || {};
    const curations = coData.curations || [];
    const wk1Prcjam = coData.raw_components?.wk1?.prcjam_counts || {};
    const wk1Cexec = coData.raw_components?.wk1?.cexec_counts || {};
    const wk2 = coData.wk2_demand || {};

    // Use actual wk2 raw components from Recharge (not estimates)
    const wk2Prcjam = coData.raw_components?.wk2?.prcjam_counts || {};
    const wk2Cexec = coData.raw_components?.wk2?.cexec_counts || {};

    let html = '<div class="co-assign-section">';
    html += '<div class="co-assign-title">PR-CJAM</div>';
    html += '<div class="co-assign-hdr-row"><span></span><span>W1</span><span>W2</span><span>Cheese</span></div>';
    for (const cur of curations) {
        const cheese = (prCjam[cur] || {}).cheese || '';
        const w1 = wk1Prcjam[cur] || 0;
        const w2 = wk2Prcjam[cur] || 0;
        // Show all curations that have an assignment or demand
        if (w1 === 0 && w2 === 0 && !cheese) continue;
        html += `<div class="co-assign-row">
            <span class="co-assign-suffix">${cur}</span>
            <span class="co-assign-demand">${w1 || '-'}</span>
            <span class="co-assign-demand co-assign-w2">${w2 || '-'}</span>
            <input type="text" class="co-assign-input"
                value="${cheese}" data-assign="prcjam" data-cur="${cur}"
                onchange="onAssignChange(this)">
        </div>`;
    }
    html += '</div>';

    html += '<div class="co-assign-section">';
    html += '<div class="co-assign-title">CEX-EC</div>';
    html += '<div class="co-assign-hdr-row"><span></span><span>W1</span><span>W2</span><span>Cheese</span></div>';
    for (const cur of curations) {
        const cheese = cexEc[cur] || '';
        const w1 = wk1Cexec[cur] || 0;
        const w2 = wk2Cexec[cur] || 0;
        if (w1 === 0 && w2 === 0 && !cheese) continue;
        html += `<div class="co-assign-row">
            <span class="co-assign-suffix">${cur}</span>
            <span class="co-assign-demand">${w1 || '-'}</span>
            <span class="co-assign-demand co-assign-w2">${w2 || '-'}</span>
            <input type="text" class="co-assign-input"
                value="${cheese}" data-assign="cexec" data-cur="${cur}"
                onchange="onAssignChange(this)">
        </div>`;
    }
    html += '</div>';

    body.innerHTML = html;
}

async function onAssignChange(input) {
    const type = input.dataset.assign;
    const cur = input.dataset.cur;
    const sku = input.value.trim();

    // Persist to server
    await api('/api/assign', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({curation: cur, slot: type === 'prcjam' ? 'prcjam' : 'cexec', cheese: sku}),
    });

    // Update local assignments and recalc
    if (type === 'prcjam') {
        if (!coData.assignments.pr_cjam[cur]) coData.assignments.pr_cjam[cur] = {};
        coData.assignments.pr_cjam[cur].cheese = sku;
    } else {
        coData.assignments.cex_ec[cur] = sku;
    }

    renderCutOrderInteractive();
    log(`Assignment: ${type} ${cur} = ${sku}`, 'cyan');
}

function renderCutOrder(data) {
    // Legacy compatibility — redirect to interactive if coData loaded
    if (coData) { renderCutOrderInteractive(); return; }
}

async function showAttribution(sku) {
    const data = await api(`/api/demand_breakdown/${sku}`);
    const title = document.getElementById('co-attr-title');
    const body = document.getElementById('co-attr-body');
    title.textContent = `Demand Attribution — ${sku}`;

    let html = '';
    const total = (data.direct || 0) +
        Object.values(data.prcjam || {}).reduce((a, b) => a + b, 0) +
        Object.values(data.cexec || {}).reduce((a, b) => a + b, 0);

    // Direct
    if (data.direct > 0) {
        html += `<div class="attr-section">
            <div class="attr-section-title attr-direct">Direct (Recipe + Custom)</div>
            <div class="attr-row"><span class="attr-label">Direct picks</span><span class="attr-value attr-direct">${data.direct}</span></div>
        </div>`;
    }

    // PR-CJAM
    const prcjam = data.prcjam || {};
    if (Object.keys(prcjam).length > 0) {
        html += `<div class="attr-section"><div class="attr-section-title attr-prcjam">PR-CJAM</div>`;
        for (const [cur, qty] of Object.entries(prcjam).sort((a, b) => b[1] - a[1])) {
            html += `<div class="attr-row"><span class="attr-label">${cur}</span><span class="attr-value attr-prcjam">${qty}</span></div>`;
        }
        html += '</div>';
    }

    // CEX-EC
    const cexec = data.cexec || {};
    if (Object.keys(cexec).length > 0) {
        html += `<div class="attr-section"><div class="attr-section-title attr-cexec">CEX-EC</div>`;
        for (const [cur, qty] of Object.entries(cexec).sort((a, b) => b[1] - a[1])) {
            html += `<div class="attr-row"><span class="attr-label">${cur}</span><span class="attr-value attr-cexec">${qty}</span></div>`;
        }
        html += '</div>';
    }

    // Total
    html += `<div class="attr-section" style="border-top:1px solid var(--border);padding-top:8px;margin-top:4px">
        <div class="attr-row"><span class="attr-label" style="font-weight:600">Total</span><span class="attr-value" style="font-size:16px">${total}</span></div>
    </div>`;

    body.innerHTML = html;
    openDrawer('co-attr-drawer');
}

async function loadProjectionSettings() {
    const data = await api('/api/projection_settings');
    const curSelect = document.getElementById('co-proj-curation');
    const mulInput = document.getElementById('co-proj-multiplier');
    const enabledCb = document.getElementById('co-proj-enabled');

    // Populate curation dropdown
    const curations = ['MONG', 'MDT', 'OWC', 'SPN', 'ALPT', 'ISUN', 'HHIGH'];
    curSelect.innerHTML = curations.map(c =>
        `<option value="${c}" ${c === data.active_curation ? 'selected' : ''}>${c}</option>`
    ).join('');

    mulInput.value = data.multiplier || 3;
    enabledCb.checked = data.enabled !== false;

    // Load weeks-back setting
    const wbSelect = document.getElementById('co-shopify-weeks-back');
    if (wbSelect && data.shopify_weeks_back) {
        wbSelect.value = data.shopify_weeks_back;
    }

    // Load first-order overrides table
    loadFirstOrderOverrides();
}

async function saveProjectionSettings() {
    const data = {
        enabled: document.getElementById('co-proj-enabled').checked,
        active_curation: document.getElementById('co-proj-curation').value,
        multiplier: parseInt(document.getElementById('co-proj-multiplier').value) || 3,
        recipe_only: true,
    };
    const resp = await api('/api/projection_settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (resp.ok) {
        log(`Projection updated: ${data.active_curation} × ${data.multiplier}`, 'green');
    }
}

async function saveShopifyWeeksBack() {
    const val = document.getElementById('co-shopify-weeks-back').value;
    await api('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shopify_weeks_back: parseInt(val) }),
    });
    log(`Shopify history window: ${val} weeks`, 'green');
}

async function loadFirstOrderOverrides() {
    const data = await api('/api/first_order_overrides');
    if (!data) return;
    const rolling = data.rolling_averages || {};
    const overrides = data.overrides || {};
    const allSkus = [...new Set([...Object.keys(rolling), ...Object.keys(overrides)])].sort();

    const container = document.getElementById('fo-overrides-table');
    if (allSkus.length === 0) {
        container.innerHTML = '<span style="font-size:10px;color:var(--fg3)">No Shopify first-order data. Run Shopify Sync first.</span>';
        return;
    }

    let html = '<table style="width:100%;font-size:11px;border-collapse:collapse">';
    html += '<tr style="color:var(--fg3);font-family:\'Space Mono\',monospace;font-size:9px;text-transform:uppercase">';
    html += '<th style="text-align:left;padding:3px 4px">SKU</th>';
    html += '<th style="text-align:right;padding:3px 4px">Rolling Avg</th>';
    html += '<th style="text-align:right;padding:3px 4px">Override</th>';
    html += '<th style="width:30px"></th></tr>';

    for (const sku of allSkus) {
        const avg = rolling[sku] || 0;
        const ov = overrides[sku];
        const hasOverride = ov !== undefined && ov !== null;
        html += `<tr style="border-bottom:1px solid rgba(42,42,48,0.2)">`;
        html += `<td style="padding:3px 4px;font-family:'DM Sans',sans-serif;color:var(--fg)">${sku}</td>`;
        html += `<td style="padding:3px 4px;text-align:right;font-family:'Rajdhani',sans-serif;color:var(--fg3)">${avg}/wk</td>`;
        html += `<td style="padding:3px 4px;text-align:right">
            <input type="number" class="settings-input" style="width:50px;font-size:11px;text-align:right"
                   id="fo-ov-${sku}" value="${hasOverride ? ov : ''}"
                   placeholder="${avg}" min="0"
                   onchange="setFirstOrderOverride('${sku}', this.value)">
        </td>`;
        html += `<td style="padding:3px 2px;text-align:center">
            ${hasOverride ? `<span style="cursor:pointer;color:var(--fg3);font-size:10px" onclick="clearFirstOrderOverride('${sku}')" title="Clear override">\u00d7</span>` : ''}
        </td>`;
        html += '</tr>';
    }
    html += '</table>';
    container.innerHTML = html;
}

async function setFirstOrderOverride(sku, value) {
    if (value === '' || value === null) return clearFirstOrderOverride(sku);
    const qty = parseInt(value);
    if (isNaN(qty) || qty < 0) return;
    await api('/api/first_order_override', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sku, qty }),
    });
    log(`Override set: ${sku} = ${qty}/wk`, 'green');
}

async function clearFirstOrderOverride(sku) {
    await api('/api/first_order_override', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sku, clear: true }),
    });
    log(`Override cleared: ${sku}`, 'green');
    loadFirstOrderOverrides();
}

function exportCutOrderCSV() {
    window.open('/api/cut_order_csv', '_blank');
}

async function emailCutOrder() {
    if (!cutOrderData || !cutOrderData.cut_lines) {
        log('Load cut order first', 'red');
        return;
    }
    setMascot('loading', 'Sending cut order email...');
    const resp = await api('/api/email_cut_order', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lines: cutOrderData.cut_lines, summary: cutOrderData.summary }),
    });
    if (resp.ok) {
        log(`Cut order emailed to ${resp.sent_to} (${resp.lines} lines)`, 'green');
        setMascot('happy', 'Email sent!');
    } else {
        log('Email failed: ' + (resp.error || 'unknown error'), 'red');
        setMascot('alert', 'Email failed');
    }
}

async function loadJournal() {
    const data = await api('/api/journal');
    const panel = document.getElementById('co-journal-panel');
    const body = document.getElementById('co-journal-body');
    if (!data || !data.entries || data.entries.length === 0) {
        panel.style.display = 'none';
        return;
    }
    panel.style.display = '';
    let html = '<table class="net-table"><thead><tr><th>Time</th><th>Type</th><th>Label</th><th>Changes</th></tr></thead><tbody>';
    for (const e of data.entries) {
        const ts = e.ts ? new Date(e.ts).toLocaleString() : '';
        const deltas = e.sku_deltas ? Object.entries(e.sku_deltas).map(([k,v]) => `${k}: ${v > 0 ? '+' : ''}${v}`).join(', ') : '';
        const typeClass = e.type === 'depletion' ? 'color:var(--red)' : e.type === 'production' ? 'color:var(--green)' : '';
        html += `<tr><td style="font-size:10px">${ts}</td><td style="${typeClass};text-transform:uppercase;font-size:9px;font-weight:600">${e.type}</td><td>${e.label || ''}</td><td style="font-size:10px">${deltas}</td></tr>`;
    }
    html += '</tbody></table>';
    body.innerHTML = html;
}


// ══════════════════════════════════════════════════════════════════════
//  RUNWAY VIEW
// ══════════════════════════════════════════════════════════════════════

let runwayHiddenSkus = new Set(JSON.parse(localStorage.getItem('runwayHiddenSkus') || '[]'));
let runwayShowHidden = false;
let _lastRunwayData = null;

function saveRunwayHidden() {
    localStorage.setItem('runwayHiddenSkus', JSON.stringify([...runwayHiddenSkus]));
}

function toggleRunwayHidden() {
    runwayShowHidden = !runwayShowHidden;
    const btn = document.getElementById('rw-toggle-hidden-btn');
    if (btn) btn.classList.toggle('active', runwayShowHidden);
    if (_lastRunwayData) renderRunway(_lastRunwayData);
}

function hideRunwaySku(sku) {
    runwayHiddenSkus.add(sku);
    saveRunwayHidden();
    if (_lastRunwayData) renderRunway(_lastRunwayData);
}

function unhideRunwaySku(sku) {
    runwayHiddenSkus.delete(sku);
    saveRunwayHidden();
    if (_lastRunwayData) renderRunway(_lastRunwayData);
}

async function loadRunway() {
    log('Loading runway...', 'cyan');
    try {
        const data = await api('/api/runway', {
            method: 'POST',
            body: JSON.stringify({}),
        });
        if (data.error) {
            log(data.error, 'red');
            return;
        }
        _lastRunwayData = data;
        renderRunway(data);
        // Fetch shortage actions and attach to rows
        loadRunwayActions();
    } catch (e) {
        log('Runway load failed: ' + e.message, 'red');
    }
}

let _runwayFixMap = {};

async function loadRunwayActions() {
    try {
        const fixes = await api('/api/suggest_fixes');
        if (!fixes || !Array.isArray(fixes)) return;
        _runwayFixMap = {};
        for (const f of fixes) _runwayFixMap[f.sku] = f;
        // Re-render with actions attached
        if (_lastRunwayData) renderRunway(_lastRunwayData);
    } catch (e) {
        // Non-critical
    }
}

// ── Horizon Toggle ───────────────────────────────────────────────────

let runwayHorizon = 'weekly';
let _lastMonthlyData = null;

function toggleRunwayHorizon() {
    runwayHorizon = runwayHorizon === 'weekly' ? 'monthly' : 'weekly';
    document.getElementById('rw-hz-wk').classList.toggle('rw-hz-active', runwayHorizon === 'weekly');
    document.getElementById('rw-hz-mo').classList.toggle('rw-hz-active', runwayHorizon === 'monthly');
    // Show/hide extended stats
    document.querySelectorAll('.rw-stat-extended').forEach(el => {
        el.style.display = runwayHorizon === 'monthly' ? '' : 'none';
    });
    if (runwayHorizon === 'weekly') {
        if (_lastRunwayData) renderRunway(_lastRunwayData);
        else loadRunway();
    } else {
        loadRunwayMonthly();
    }
}

async function loadRunwayMonthly() {
    log('Loading 4-month runway...', 'cyan');
    try {
        const data = await api('/api/runway_monthly', {
            method: 'POST',
            body: JSON.stringify({}),
        });
        if (data.error) { log(data.error, 'red'); return; }
        _lastMonthlyData = data;
        renderRunwayMonthly(data);
    } catch (e) {
        log('Monthly runway failed: ' + e.message, 'red');
    }
}

function renderRunwayMonthly(data) {
    const allSkus = data.skus || [];
    const labels = data.month_labels || [];
    const stats = data.stats || {};

    // Update stats
    document.getElementById('rw-sku-count').textContent = stats.skus_tracked || '--';
    document.getElementById('rw-avg-forecast').textContent = (stats.avg_runway || 0) + ' mo';
    document.getElementById('rw-at-risk').textContent = stats.at_risk || 0;
    document.getElementById('rw-velocity').textContent = Math.round(stats.velocity || 0) + '/wk';
    document.getElementById('rw-coverage').textContent = (stats.coverage_pct || 0) + '%';
    const trendVal = stats.velocity_trend || 0;
    const trendEl = document.getElementById('rw-vel-trend');
    trendEl.textContent = (trendVal >= 0 ? '+' : '') + trendVal + '/wk';
    trendEl.style.color = trendVal > 0 ? 'var(--red)' : trendVal < 0 ? 'var(--green)' : 'var(--fg3)';
    document.getElementById('rw-overstock').textContent = stats.overstock || 0;
    document.getElementById('rw-wheel-util').textContent = (stats.wheel_util_pct || 0) + '%';

    // Filter hidden
    const visibleSkus = runwayShowHidden ? allSkus :
        allSkus.filter(s => !runwayHiddenSkus.has(s.sku));

    // Render grid
    renderRunwayMonthlyGrid(visibleSkus, labels);
}

function renderRunwayMonthlyGrid(skus, labels) {
    const grid = document.getElementById('runway-grid');
    const NUM_MONTHS = labels.length || 4;

    const tickPositions = [];
    for (let mi = 0; mi < NUM_MONTHS; mi++) {
        tickPositions.push(((mi + 1) / NUM_MONTHS) * 100);
    }

    let html = '<table class="rw-grid-table">';
    html += '<colgroup>';
    html += '<col style="width:18px">';
    html += '<col style="width:95px">';
    html += '<col style="width:62px">';
    html += '<col style="width:60px">';
    html += '<col>';
    html += '<col style="width:55px">';
    html += '</colgroup>';
    html += '<thead><tr>';
    html += '<th></th><th>SKU</th>';
    html += '<th class="rw-th-num">Avail</th>';
    html += '<th class="rw-th-num rw-th-dmd">Dmd/mo</th>';
    html += `<th class="rw-th-runway" style="position:relative">
        <div style="display:flex;justify-content:space-around;font-family:'Space Mono',monospace;font-size:12px;font-weight:600;letter-spacing:0.5px;color:var(--fg2)">`;
    for (const lbl of labels) html += `<span>${lbl}</span>`;
    html += `</div></th>`;
    html += '<th class="rw-th-weeks">Runway</th>';
    html += '</tr></thead><tbody>';

    for (const s of skus) {
        const isHidden = runwayHiddenSkus.has(s.sku);
        const runway = s.runway_months;
        const statusClass = runway < 1 ? 'rw-shortage' :
                            runway < 2 ? 'rw-tight' : 'rw-ok';

        const totalSupply = s.available + s.potential;
        const fillPct = Math.min(100, (runway / NUM_MONTHS) * 100);

        let procPct, potPct;
        if (s.potential > 0 && totalSupply > 0) {
            const procRatio = s.available / totalSupply;
            procPct = fillPct * procRatio;
            potPct = fillPct * (1 - procRatio);
        } else {
            procPct = fillPct;
            potPct = 0;
        }

        const demandMo = s.demand_per_month || 0;
        const tooltip = `${s.sku}: ${s.available} avail` +
            (s.potential > 0 ? ` +${s.potential} potential` : '') +
            `\nDemand: ${demandMo}/mo | Velocity: ${s.velocity}/wk` +
            `\nRunway: ${runway} mo` +
            (s.reorder_week ? `\nReorder by: ${s.reorder_week}` : '');

        const hideBtn = isHidden
            ? `<span class="rw-hide-btn" onclick="unhideRunwaySku('${s.sku}')" title="Unhide" style="opacity:1;color:var(--warm)">+</span>`
            : `<span class="rw-hide-btn" onclick="hideRunwaySku('${s.sku}')" title="Hide">\u00d7</span>`;

        const rowOpacity = isHidden ? ' style="opacity:0.35"' : '';
        const availDisplay = s.potential > 0
            ? `<span class="rw-pot-hint">${s.potential}&nbsp;</span>${s.available}`
            : `${s.available}`;

        // Reorder chip for at-risk SKUs
        let reorderChip = '';
        if (s.reorder_week && runway < 2) {
            reorderChip = `<span class="rw-chip rw-chip-other" title="Reorder by ${s.reorder_week}">${s.reorder_week}</span>`;
        }

        html += `<tr data-sku="${s.sku}"${rowOpacity}>`;
        html += `<td style="width:18px;padding:0 2px">${hideBtn}</td>`;
        html += `<td class="rw-sku-col">
            <span>${s.sku}</span>
            <span class="rw-sku-name">${s.name || ''}</span>
        </td>`;
        html += `<td class="rw-avail-col">${availDisplay}</td>`;
        html += `<td class="rw-demand-col" onclick="toggleMonthlyBreakdown('${s.sku}')">
            <span class="rw-demand-num">${demandMo}</span>
            <span class="rw-expand-icon">&#9662;</span>
        </td>`;

        html += `<td>
            <div class="rw-bar-cell" title="${tooltip}">
                <div class="rw-bar-track">
                    <div class="rw-fill-processed ${statusClass}" style="width:${procPct}%"></div>`;
        if (potPct > 0) {
            html += `<div class="rw-fill-potential ${statusClass}" style="left:${procPct}%;width:${potPct}%"></div>`;
        }
        html += `</div>`;
        for (let ti = 0; ti < NUM_MONTHS - 1; ti++) {
            html += `<div class="rw-week-tick" style="left:${tickPositions[ti]}%"></div>`;
        }
        html += `</div></td>`;

        html += `<td class="rw-runway-col ${statusClass}">${runway} mo${reorderChip}</td>`;
        html += '</tr>';
    }

    html += '</tbody></table>';
    grid.innerHTML = html;
}

function toggleMonthlyBreakdown(sku) {
    const existing = document.getElementById(`rw-mo-breakdown-${sku}`);
    if (existing) { existing.remove(); return; }
    if (!_lastMonthlyData) return;
    const skuData = _lastMonthlyData.skus.find(s => s.sku === sku);
    if (!skuData) return;
    const row = document.querySelector(`tr[data-sku="${sku}"]`);
    if (!row) return;
    const detail = document.createElement('tr');
    detail.id = `rw-mo-breakdown-${sku}`;
    detail.className = 'rw-breakdown-row';
    let cells = '';
    for (const m of skuData.months) {
        const cls = m.status === 'SHORTAGE' ? 'tl-out' : m.status === 'TIGHT' ? '' : 'tl-in';
        cells += `<span class="rw-bd-item">${m.label}: <b>${m.demand}</b> dmd, <b class="${cls}">${m.carry_out}</b> left</span><span class="rw-bd-sep">\u00b7</span>`;
    }
    cells += `<span class="rw-bd-item">Vel: <b>${skuData.velocity}</b>/wk (${skuData.velocity_trend >= 0 ? '+' : ''}${skuData.velocity_trend})</span>`;
    detail.innerHTML = `<td></td><td colspan="5" class="rw-breakdown-cell">${cells}</td>`;
    row.after(detail);
}

function renderRunway(data) {
    const allSkus = data.skus || [];
    const labels = data.week_labels || [];
    const params = data.model_params || {};
    const assignments = data.assignments || [];

    // Filter hidden
    const visibleSkus = runwayShowHidden ? allSkus :
        allSkus.filter(s => !runwayHiddenSkus.has(s.sku));
    const hiddenCount = allSkus.filter(s => runwayHiddenSkus.has(s.sku)).length;

    // Summary bar — compute from visible only
    const withDemand = visibleSkus.filter(s => s.forecast.weeks.some(w => w.demand > 0));
    document.getElementById('rw-sku-count').textContent = withDemand.length;

    const avgF = withDemand.length ? (withDemand.reduce((a, s) => a + s.forecast.runway_weeks, 0) / withDemand.length).toFixed(1) : '--';
    document.getElementById('rw-avg-forecast').textContent = avgF + ' wk';

    const atRisk = visibleSkus.filter(s => s.worst_status === 'SHORTAGE' || s.worst_status === 'TIGHT').length;
    document.getElementById('rw-at-risk').textContent = atRisk;
    document.getElementById('rw-at-risk').style.color = atRisk > 0 ? 'var(--red)' : 'var(--rw-bar-ok)';

    const totalVelocity = withDemand.reduce((a, s) => a + (s.demand_per_wk || 0), 0);
    document.getElementById('rw-velocity').textContent = totalVelocity + '/wk';


    // Update hidden toggle button
    const toggleBtn = document.getElementById('rw-toggle-hidden-btn');
    if (toggleBtn) {
        toggleBtn.textContent = hiddenCount > 0
            ? `${runwayShowHidden ? 'Hide' : 'Show'} ${hiddenCount} hidden`
            : 'No hidden';
        toggleBtn.classList.toggle('active', runwayShowHidden);
        toggleBtn.style.display = hiddenCount > 0 ? '' : 'none';
    }

    // Assignment panel
    renderRunwayAssignments(assignments);

    // Grid — continuous bar
    renderRunwayGrid(visibleSkus, labels);
}

function renderRunwayAssignments(assignments) {
    const panel = document.getElementById('runway-assignments');
    const maxUnits = Math.max(1, ...assignments.map(a => Math.max(a.prcjam_units, a.cexec_units)));

    let html = '<div class="rw-assign-title">Assignments</div>';
    html += '<table class="rw-assign-table"><thead><tr>';
    html += '<th>Cur</th><th>PR-CJAM</th><th>CEX-EC</th>';
    html += '</tr></thead><tbody>';

    for (const a of assignments) {
        const pjW = Math.max(2, (a.prcjam_units / maxUnits) * 60);
        const ceW = Math.max(2, (a.cexec_units / maxUnits) * 60);
        const pjClass = a.prcjam ? '' : ' unassigned';
        const ceClass = a.cexec ? '' : ' unassigned';

        html += `<tr>`;
        html += `<td class="rw-cur-label">${a.curation}</td>`;
        html += `<td class="rw-cheese-cell${pjClass}" onclick="openRunwayPicker('${a.curation}','prcjam')">
            <div>${a.prcjam || '\u2014'}</div>
            <div class="rw-spark-wrap">
                <span class="rw-sparkline" style="width:${pjW}px"></span>
                <span class="rw-spark-units">${a.prcjam_units}</span>
            </div>
        </td>`;
        html += `<td class="rw-cheese-cell${ceClass}" onclick="openRunwayPicker('${a.curation}','cexec')">
            <div>${a.cexec || '\u2014'}</div>
            <div class="rw-spark-wrap">
                <span class="rw-sparkline cexec" style="width:${ceW}px"></span>
                <span class="rw-spark-units">${a.cexec_units}</span>
            </div>
        </td>`;
        html += '</tr>';
    }
    html += '</tbody></table>';
    panel.innerHTML = html;
}

function openRunwayPicker(cur, slot) {
    pickerCallback = (curation, sl, cheese) => {
        assignCheeseRunway(curation, sl, cheese);
    };
    openPicker(cur, slot);
}

async function assignCheeseRunway(curation, slot, cheese) {
    await fetch('/api/assign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ curation, slot, cheese }),
    });
    log(`Runway: assigned ${cheese} \u2192 ${curation} ${slot}`, 'green');
    loadRunway();
    calculateRMFG();
}

function toggleDemandBreakdown(sku) {
    const existing = document.getElementById(`rw-breakdown-${sku}`);
    if (existing) { existing.remove(); return; }
    if (!_lastRunwayData) return;
    const skuData = _lastRunwayData.skus.find(s => s.sku === sku);
    if (!skuData) return;
    const row = document.querySelector(`tr[data-sku="${sku}"]`);
    if (!row) return;
    const detail = document.createElement('tr');
    detail.id = `rw-breakdown-${sku}`;
    detail.className = 'rw-breakdown-row';
    const rec = skuData.recurring_per_wk || 0;
    const fo = skuData.first_order_per_wk || 0;
    const addon = skuData.addon_per_wk || 0;
    detail.innerHTML = `<td></td><td colspan="5" class="rw-breakdown-cell">
        <span class="rw-bd-item">RC Recurring: <b>${rec}</b>/wk</span>
        <span class="rw-bd-sep">\u00b7</span>
        <span class="rw-bd-item">First Orders: <b>${fo}</b>/wk</span>
        <span class="rw-bd-sep">\u00b7</span>
        <span class="rw-bd-item">Add-ons: <b>${addon}</b>/wk</span>
    </td>`;
    row.after(detail);
}

function renderRunwayGrid(skus, labels) {
    const grid = document.getElementById('runway-grid');
    const NUM_WEEKS = labels.length || 4;

    // Week ticks at fixed positions (evenly spaced across bar)
    const tickPositions = [];
    for (let wi = 0; wi < NUM_WEEKS; wi++) {
        tickPositions.push(((wi + 1) / NUM_WEEKS) * 100);
    }

    let html = '<table class="rw-grid-table">';
    html += '<colgroup>';
    html += '<col style="width:18px">';      // hide btn
    html += '<col style="width:95px">';      // SKU (+ gap before Avail)
    html += '<col style="width:62px">';      // Avail (wider for potential prefix)
    html += '<col style="width:56px">';      // Dmd/wk
    html += '<col>';                          // Bar (fills remaining)
    html += '<col style="width:50px">';      // Runway
    html += '</colgroup>';
    html += '<thead><tr>';
    html += '<th></th><th>SKU</th>';
    html += '<th class="rw-th-num">Avail</th>';
    html += '<th class="rw-th-num rw-th-dmd">Dmd/wk</th>';
    // Header with date labels at fixed positions
    html += `<th class="rw-th-runway" style="position:relative">
        <div style="display:flex;justify-content:space-around;font-family:'Space Mono',monospace;font-size:12px;font-weight:600;letter-spacing:0.5px;color:var(--fg2)">`;
    for (let i = 0; i < labels.length; i++) {
        html += `<span>${labels[i]}</span>`;
    }
    html += `</div></th>`;
    html += '<th class="rw-th-weeks">Runway</th>';
    html += '</tr></thead><tbody>';

    for (const s of skus) {
        const isHidden = runwayHiddenSkus.has(s.sku);
        // Color based on runway weeks: red < 1wk, tight 1-2wk, ok 2+wk
        const forecastRunway = s.forecast.runway_weeks;
        const statusClass = forecastRunway < 1 ? 'rw-shortage' :
                            forecastRunway < 2 ? 'rw-tight' : 'rw-ok';

        const totalSupply = s.available + s.potential;

        // Bar fill = runway weeks / NUM_WEEKS (relative to time axis)
        // A SKU lasting all 4 weeks fills 100%; 2 weeks fills 50%
        const fillPct = Math.min(100, (forecastRunway / NUM_WEEKS) * 100);

        // Split fill between processed and potential (wheel yield)
        let procPct, potPct;
        if (s.potential > 0 && totalSupply > 0) {
            const procRatio = s.available / totalSupply;
            procPct = fillPct * procRatio;
            potPct = fillPct * (1 - procRatio);
        } else {
            procPct = fillPct;
            potPct = 0;
        }

        // Demand per week (combined)
        const demandWk = s.demand_per_wk || 0;

        // Tooltip with full breakdown
        const recWk = s.recurring_per_wk || 0;
        const foWk = s.first_order_per_wk || 0;
        const addonWk = s.addon_per_wk || 0;
        const ttParts = [`${s.sku}: ${s.available} avail`];
        if (s.potential > 0) ttParts.push(`+${s.potential} wheel potential`);
        ttParts.push(`Demand: ${demandWk}/wk (RC: ${recWk}, FO: ${foWk}, Add: ${addonWk})`);
        ttParts.push(`Runway: ${forecastRunway} wk`);
        const tooltip = ttParts.join('\n');

        // Hide/unhide button
        const hideBtn = isHidden
            ? `<span class="rw-hide-btn" onclick="unhideRunwaySku('${s.sku}')" title="Unhide" style="opacity:1;color:var(--warm)">+</span>`
            : `<span class="rw-hide-btn" onclick="hideRunwaySku('${s.sku}')" title="Hide">\u00d7</span>`;

        const rowOpacity = isHidden ? ' style="opacity:0.35"' : '';

        // Avail display: show potential in parens if > 0
        const availDisplay = s.potential > 0
            ? `<span class="rw-pot-hint">${s.potential}&nbsp;</span>${s.available}`
            : `${s.available}`;

        html += `<tr data-sku="${s.sku}"${rowOpacity}>`;
        html += `<td style="width:18px;padding:0 2px">${hideBtn}</td>`;
        html += `<td class="rw-sku-col">
            <span>${s.sku}</span>
            <span class="rw-sku-name">${s.name || ''}</span>
        </td>`;
        html += `<td class="rw-avail-col">${availDisplay}</td>`;
        html += `<td class="rw-demand-col" onclick="toggleDemandBreakdown('${s.sku}')">
            <span class="rw-demand-num">${demandWk}</span>
            <span class="rw-expand-icon">&#9662;</span>
        </td>`;

        // Runway bar — fixed week positions, fill = runway proportion
        html += `<td>
            <div class="rw-bar-cell" title="${tooltip}">
                <div class="rw-bar-track">
                    <div class="rw-fill-processed ${statusClass}" style="width:${procPct}%"></div>`;
        if (potPct > 0) {
            html += `<div class="rw-fill-potential ${statusClass}" style="left:${procPct}%;width:${potPct}%"></div>`;
        }
        html += `</div>`;  // close rw-bar-track
        // Week ticks at fixed positions
        for (let ti = 0; ti < NUM_WEEKS - 1; ti++) {
            html += `<div class="rw-week-tick" style="left:${tickPositions[ti]}%"></div>`;
        }
        html += `</div></td>`;  // close rw-bar-cell

        // Runway weeks
        // Runway weeks + inline action chip for shortages
        let actionHtml = '';
        const fix = _runwayFixMap[s.sku];
        if (fix && forecastRunway < 2) {
            const firstFix = fix.fixes[0] || '';
            let chipClass = 'rw-chip';
            if (firstFix.startsWith('MFG:')) chipClass += ' rw-chip-mfg';
            else if (firstFix.startsWith('PO:')) chipClass += ' rw-chip-po';
            else chipClass += ' rw-chip-other';
            const chipText = firstFix.replace(/^(MFG|PO): /, '');
            const allFixes = fix.fixes.map(f => f.replace(/"/g, '&quot;')).join('&#10;');
            actionHtml = `<span class="${chipClass}" title="${allFixes}">${chipText}</span>`;
        }
        html += `<td class="rw-runway-col ${statusClass}">${forecastRunway} wk${actionHtml}</td>`;
        html += '</tr>';
    }

    html += '</tbody></table>';
    grid.innerHTML = html;
}
