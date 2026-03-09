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

    // Auto-refresh every hour (Dropbox + Recharge + recalculate)
    setInterval(() => {
        log('Auto-refresh triggered (hourly)', 'cyan');
        runAll();
    }, 60 * 60 * 1000);

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
                setMascot('alert', `Blocked! ${c.sku} violates +/-2`);
                log(`Blocked: ${c.sku} for ${curation} (${c.constraint})`, 'red');
                return;
            }
            assignCheese(curation, slot, c.sku);
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
        if (filter === 'Shortages' && r.status !== 'SHORTAGE') return false;
        if (filter === 'Tight' && !['SHORTAGE','TIGHT'].includes(r.status)) return false;
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
            tr.innerHTML = `
                <td class="sku-cell">${r.sku}</td>
                <td class="num">${r.available}</td>
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
                <td class="sku-cell">${r.sku}</td>
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

    const data = await api('/api/calculate_rmfg', { method: 'POST' });

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
    rmfgLoaded = true;
    setMascot('happy', `Loaded ${data.cheese_count} cheeses from Dropbox`);
    return true;
}

async function syncRecharge() {
    setMascot('loading', 'Pulling from Recharge...');
    log('Fetching queued charges from Recharge API...', 'cyan');

    const data = await api('/api/recharge_sync', { method: 'POST' });
    if (data.error) {
        setMascot('alert', 'Recharge sync failed');
        log(`Recharge error: ${data.error}`, 'red');
        return false;
    }

    log(`Recharge: ${data.total_charges} charges across ${data.months.join(', ')}`, 'green');
    if (data.weeks) {
        data.weeks.forEach(w => {
            log(`  ${w.label} (${w.date}): ${w.skus} SKUs, ${w.units} units`, 'green');
        });
    }
    log(`  Total cheese demand: ${data.cheese_demand_units} units`, 'green');
    setMascot('happy', `Loaded ${data.total_charges} charges`);
    return true;
}

async function syncShopify() {
    setMascot('loading', 'Pulling Shopify orders...');
    log('Fetching unfulfilled Shopify orders...', 'cyan');

    const data = await api('/api/shopify_sync', { method: 'POST' });
    if (data.error) {
        setMascot('alert', 'Shopify sync failed');
        log(`Shopify error: ${data.error}`, 'red');
        return false;
    }

    log(`Shopify: ${data.orders} orders, ${data.skus} SKUs, ${data.units} units`, 'green');
    setMascot('happy', `${data.orders} Shopify orders loaded`);
    return true;
}

async function runAll() {
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
        demandLoaded = await syncRecharge();
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

    // 3. Calculate
    await calculateRMFG();

    // 3. Show substitutions if there are shortages
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
        let inner = `
            <div class="sub-shortage-header">
                <span class="sub-shortage-sku">${s.sku}</span>
                <span class="sub-shortage-info">SHORT ${s.deficit} (avail ${s.available}, demand ${s.demand})</span>
            </div>
        `;
        if (s.substitutes.length === 0) {
            inner += '<div class="sub-none">No good substitutes found</div>';
        } else {
            s.substitutes.forEach(sub => {
                const tagClass = sub.covers_all ? 'sub-full' : 'sub-partial';
                const tagText = sub.covers_all ? 'FULL' : 'PARTIAL';
                const noDemand = sub.no_demand ? ' (unused)' : '';
                inner += `
                    <div class="sub-item">
                        <span class="sub-item-sku">${sub.sku}</span>
                        <span class="sub-item-info">headroom ${sub.headroom}, covers ${sub.can_cover}${noDemand}</span>
                        <span class="sub-item-tag ${tagClass}">${tagText}</span>
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

function switchView(view) {
    currentView = view;
    document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
    document.getElementById(`view-${view}`).classList.add('active');

    const content = document.getElementById('content');
    const calView = document.getElementById('calendar-view');

    if (view === 'dashboard') {
        content.style.display = '';
        calView.style.display = 'none';
    } else if (view === 'calendar') {
        content.style.display = 'none';
        calView.style.display = '';
        if (!calendarData) {
            loadCalendar();
        }
    }
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
