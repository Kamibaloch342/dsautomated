/**
 * server.js — Self-Hosted WhatsApp Web Gateway for ZUHRA Software
 * Powered by @whiskeysockets/baileys.
 *
 * Runs a lightweight local HTTP server on port 3005 that bridges ZUHRA's order pipeline to WhatsApp.
 * Two numbers are linked side by side (9 Nov 2026). Every alert belongs to a lane (store + job) and
 * goes out from the number chosen for that lane on the page at http://127.0.0.1:3005.
 */

const express = require('express');
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion,
        normalizeMessageContent, getContentType, jidDecode, generateMessageIDV2, toNumber } = require('@whiskeysockets/baileys');
const pino = require('pino');
const qrcodeTerminal = require('qrcode-terminal');
const QRCode = require('qrcode');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { execFile, execFileSync } = require('child_process');

const PORT = process.env.PORT || 3005;
const PANEL_FILE = path.join(__dirname, 'panel.html');
const ROUTES_FILE = path.join(__dirname, 'routes.json');
const QUEUE_FILE = path.join(__dirname, 'queue.json');
const MEDIA_DIR = path.join(__dirname, 'queue_media');
const BOT_CONFIG = path.join(__dirname, '..', 'bot_config.json');
const PID_FILE = path.join(__dirname, 'gateway.pid');
const INBOX_FILE = path.join(__dirname, 'inbox.json');

// A library rejection nobody caught must not take both WhatsApp logins down with it.
process.on('unhandledRejection', (e) => console.error('  [Gateway] ⚠️ unhandled rejection:', e && e.message));

// ── The linked numbers ────────────────────────────────────────────────────────────────────────
// Kamran keeps auth_session/, the folder the gateway has always used.
const ACCOUNTS = [
    { id: 'kamran', label: 'Kamran', authDir: path.join(__dirname, 'auth_session') },
    { id: 'hifza',  label: 'Hifza',  authDir: path.join(__dirname, 'auth_session_hifza') },
].map(a => Object.assign(a, {
    sock: null, generation: 0, startTimer: null,
    qr: null, qrDataURL: null, connected: false, userJid: null, phone: null,
    ids: new Set(),                                    // this number's phone + LID; kept while it reconnects
    groups: null, groupsAt: 0, groupsBusy: false,      // groups: Map jid → name; null until fetched
    sending: false, nextSendAt: 0,
}));
const accountById = id => ACCOUNTS.find(a => a.id === id);
for (const a of ACCOUNTS) fs.mkdirSync(a.authDir, { recursive: true });
try { fs.mkdirSync(MEDIA_DIR, { recursive: true }); } catch (e) { /* exists */ }

// ── Lanes: which number sends what ────────────────────────────────────────────────────────────
// A lane is found from the group an alert goes to, so callers do not have to know about senders.
// Only the placers' notices share one mixed group (bot_config recipient_phone); a DM placer names
// its lane ('hathoro_notices') and everything else in that group is 'general'.
// `normal` is the owner's setup of 9 Nov 2026: all of HS and DM closing from Kamran, the rest of DM
// from Hifza. Same ids as DEFAULT_GROUPS in utils/whatsapp.py; bot_config whatsapp.groups overrides.
const DEFAULT_GROUPS = {
    hoank_orders: '120363410664445868@g.us',
    hoank_insights: '120363429340767368@g.us',
    hoank_messages: '120363431670792427@g.us',
    hoank_tracking: '120363436018692282@g.us',
    hathoro_orders: '120363412233188303@g.us',
    hathoro_insights: '120363413450875187@g.us',
    hathoro_messages: '120363430751417464@g.us',
    hathoro_tracking: '120363431994810056@g.us',
};
const LANES = [
    { id: 'hathoro_orders',   store: 'DM', label: 'Order alerts (DM Coupon + DM Reseller)', normal: 'hifza' },
    { id: 'hathoro_tracking', store: 'DM', label: 'Tracking ID alerts', normal: 'hifza' },
    { id: 'hathoro_insights', store: 'DM', label: 'Insights', normal: 'hifza' },
    { id: 'hathoro_notices',  store: 'DM', label: 'Placer notices (cancel requests, delivery time, Manual-C)', normal: 'hifza', mixedGroup: true },
    { id: 'hathoro_messages', store: 'DM', label: 'Closing (delivery messages + review requests)', normal: 'kamran' },
    { id: 'hoank_orders',     store: 'HS', label: 'Order alerts (HS Coupon + HS Reseller)', normal: 'kamran' },
    { id: 'hoank_tracking',   store: 'HS', label: 'Tracking ID alerts', normal: 'kamran' },
    { id: 'hoank_insights',   store: 'HS', label: 'Insights', normal: 'kamran' },
    { id: 'hoank_messages',   store: 'HS', label: 'Closing (delivery messages + review requests)', normal: 'kamran' },
    { id: 'wrenchly_orders',   store: 'WR', label: 'Order alerts (WR Reseller)', normal: 'kamran' },
    { id: 'wrenchly_tracking', store: 'WR', label: 'Tracking ID alerts', normal: 'kamran' },
    { id: 'wrenchly_messages', store: 'WR', label: 'Closing (delivery messages + review requests)', normal: 'kamran' },
    { id: 'general',          store: 'Other', label: 'HS and WR placer notices, WR insights and anything else', normal: 'kamran', mixedGroup: true },
];
const laneById = id => LANES.find(l => l.id === id);

let routes = Object.fromEntries(LANES.map(l => [l.id, l.normal]));
try {
    const saved = JSON.parse(fs.readFileSync(ROUTES_FILE, 'utf8')) || {};
    for (const [lane, who] of Object.entries(saved)) if (laneById(lane) && accountById(who)) routes[lane] = who;
} catch (e) { /* first run: the normal setup */ }

function saveRoutes() {
    fs.writeFileSync(ROUTES_FILE + '.tmp', JSON.stringify(routes, null, 1));
    fs.renameSync(ROUTES_FILE + '.tmp', ROUTES_FILE);
}

let groupsCfg = DEFAULT_GROUPS;
let mixedGroupJid = '';
const laneJid = l => (l.mixedGroup ? mixedGroupJid : groupsCfg[l.id]) || '';
function laneOf(m) {
    if (laneById(m.lane)) return m.lane;
    const hit = LANES.find(l => !l.mixedGroup && laneJid(l) === m.to);
    return hit ? hit.id : 'general';
}
// Decided when the alert goes out, not when it was queued, so a switch moves what is already waiting.
const senderOf = m => accountById(routes[laneOf(m)]);

/** Why `acc` cannot send to `jid` right now; null when it can. */
function holdReason(acc, jid) {
    if (!acc.connected || !acc.sock) return 'not_linked';
    if (jid.endsWith('@g.us') && acc.groups && !acc.groups.has(jid)) return 'not_in_group';
    return null;
}

function requireJson(req, res) {
    // A cross-site page cannot send application/json here without a preflight, which is never answered.
    if (req.is('application/json') && req.body) return true;
    res.status(415).json({ error: 'application/json required' });
    return false;
}

const app = express();
app.use(express.json({ limit: '25mb' }));

// ── Who wrote in the Orders Alerts groups (9 Nov 2026) ────────────────────────────────────────
// The launcher reads /inbox and tells the owner. Left out: the gateway's own alerts (their message
// ids are noted before sending, because the other linked number can receive one before the send
// returns) and the owner's own messages. Everyone else counts, Hifza typing by hand included.
// A group both numbers are in delivers each message twice, so the message id decides.
const INBOX_LANES = ['hoank_orders', 'hathoro_orders'];
const INBOX_KEEP = 200;
const INBOX_MAX_AGE_MS = 48 * 3600 * 1000;
let inbox = { seq: 0, seen: 0, items: [], botIds: {} };
try { inbox = Object.assign(inbox, JSON.parse(fs.readFileSync(INBOX_FILE, 'utf8'))); } catch (e) { /* first run */ }

let inboxTimer = null;
function saveInboxNow() {
    inboxTimer = null;
    try {
        fs.writeFileSync(INBOX_FILE + '.tmp', JSON.stringify(inbox));
        fs.renameSync(INBOX_FILE + '.tmp', INBOX_FILE);
    } catch (e) { console.error('  [Inbox] ⚠️ could not save:', e.message); }
}
function saveInbox() {
    if (!inboxTimer) inboxTimer = setTimeout(saveInboxNow, 2000);
}

const jidUser = jid => (jid && jidDecode(jid)?.user) || '';

function messagePreview(message) {
    const content = normalizeMessageContent(message);
    const type = getContentType(content);
    const body = type ? content[type] : null;
    switch (type) {
        case 'conversation': return body;
        case 'extendedTextMessage': return body.text;
        case 'imageMessage': return body.caption || '📷 Photo';
        case 'videoMessage': return body.caption || '🎥 Video';
        case 'documentMessage': return body.caption || body.fileName || '📄 Document';
        case 'audioMessage': return '🎤 Voice note';
        case 'stickerMessage': return 'Sticker';
        case 'contactMessage': return `👤 Contact ${body.displayName || ''}`.trim();
        case 'locationMessage': return '📍 Location';
        default: return null;      // reactions, edits, deletions and the like are not new messages
    }
}

function noteGroupMessage(acc, msg) {
    const jid = msg.key?.remoteJid;
    const lane = jid && INBOX_LANES.map(laneById).find(l => laneJid(l) === jid);
    if (!lane || !msg.message) return;
    const id = msg.key.id;
    if (inbox.botIds[id] || inbox.items.some(i => i.id === id)) return;
    const at = (toNumber(msg.messageTimestamp) * 1000) || Date.now();
    if (Date.now() - at > INBOX_MAX_AGE_MS) return;      // a replay of old messages is not news

    const jids = [msg.key.participant, msg.key.participantAlt].filter(Boolean);
    const users = jids.map(jidUser);
    const author = msg.key.fromMe ? acc : ACCOUNTS.find(a => users.some(u => a.ids.has(u)));
    if (author && author.id === 'kamran') return;
    const text = messagePreview(msg.message);
    if (!text) return;

    const pn = jids.find(j => j.endsWith('@s.whatsapp.net'));
    const group = ACCOUNTS.map(a => a.groups && a.groups.get(jid)).find(Boolean)
               || `${lane.store} Orders Alerts`;
    inbox.seq += 1;
    inbox.items.push({
        seq: inbox.seq, id, at, group,
        from: msg.pushName || (author ? author.label : ''),
        phone: pn ? jidUser(pn) : (author && author.phone) || '',
        text: String(text).slice(0, 500),
    });
    inbox.items = inbox.items.slice(-INBOX_KEEP);
    console.log(`  [Inbox] ${group}: new message from ${msg.pushName || 'unknown'}`);
    saveInbox();
}

app.get('/inbox', (req, res) => {
    const since = req.query.since === undefined ? NaN : Number(req.query.since);
    res.json({
        seq: inbox.seq,
        seen: inbox.seen,
        unread: inbox.items.filter(i => i.seq > inbox.seen).length,
        items: Number.isFinite(since) ? inbox.items.filter(i => i.seq > since) : inbox.items.slice(-50),
    });
});

app.post('/inbox/seen', (req, res) => {
    if (!requireJson(req, res)) return;
    const seq = Number(req.body.seq);
    if (!Number.isFinite(seq)) return res.status(400).json({ error: 'seq must be a number' });
    inbox.seen = Math.max(inbox.seen, Math.min(seq, inbox.seq));
    saveInbox();
    return res.json({ success: true, seen: inbox.seen });
});

// ── WhatsApp sessions ─────────────────────────────────────────────────────────────────────────
function scheduleStart(acc, ms) {
    clearTimeout(acc.startTimer);          // one pending start per number, or two sockets fight
    acc.startTimer = setTimeout(() => {
        startWhatsApp(acc).catch((e) => {
            console.error(`  [${acc.label}] ⚠️ could not start:`, e.message);
            scheduleStart(acc, 5000);
        });
    }, ms);
}

function purgeSession(acc) {
    try {
        for (const f of fs.readdirSync(acc.authDir)) fs.unlinkSync(path.join(acc.authDir, f));
    } catch (e) { console.error(`  [${acc.label}] ⚠️ could not clear the session:`, e.message); }
}

function markOffline(acc) {
    acc.connected = false;
    acc.userJid = null;
    acc.phone = null;
    acc.groups = null;
    acc.groupsAt = 0;
}

async function refreshGroups(acc) {
    if (!acc.connected || !acc.sock || acc.groupsBusy) return;
    acc.groupsBusy = true;
    try {
        const all = await acc.sock.groupFetchAllParticipating();
        acc.groups = new Map(Object.entries(all).map(([id, g]) => [id, g.subject || '']));
        acc.groupsAt = Date.now();
    } catch (e) {
        console.error(`  [${acc.label}] ⚠️ group list failed:`, e.message);
    }
    acc.groupsBusy = false;
}

async function startWhatsApp(acc) {
    const generation = ++acc.generation;
    const isCurrent = () => generation === acc.generation;   // events of a replaced socket are ignored
    const { state, saveCreds } = await useMultiFileAuthState(acc.authDir);
    const { version } = await fetchLatestBaileysVersion();
    if (!isCurrent()) return;

    const sock = makeWASocket({
        version,
        logger: pino({ level: 'silent' }),
        auth: state,
        printQRInTerminal: false, // We'll handle custom terminal & web QR
        browser: ['ZUHRA Software', 'Chrome', '120.0.0'],
    });
    acc.sock = sock;

    sock.ev.on('creds.update', (...a) => { if (isCurrent()) saveCreds(...a); });
    sock.ev.on('groups.upsert', () => { if (isCurrent()) refreshGroups(acc); });
    sock.ev.on('group-participants.update', () => { if (isCurrent()) refreshGroups(acc); });
    sock.ev.on('messages.upsert', ({ messages }) => {
        if (!isCurrent()) return;
        for (const msg of messages || []) {
            try { noteGroupMessage(acc, msg); } catch (e) { console.error('  [Inbox] ⚠️', e.message); }
        }
    });

    sock.ev.on('connection.update', async (update) => {
        if (!isCurrent()) return;
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            acc.qr = qr;
            try {
                acc.qrDataURL = await QRCode.toDataURL(qr, { margin: 2, width: 280 });
                if (acc.id === 'kamran') {
                    const qrBuffer = await QRCode.toBuffer(qr);
                    fs.writeFileSync(path.join(__dirname, 'latest_qr.png'), qrBuffer);
                }
            } catch (e) {
                acc.qrDataURL = null;
            }
            console.log(`\n📱 Scan to link ${acc.label}'s WhatsApp: http://localhost:${PORT}\n`);
            qrcodeTerminal.generate(qr, { small: true });
        }

        if (connection === 'close') {
            markOffline(acc);
            const statusCode = lastDisconnect?.error?.output?.statusCode;
            const loggedOut = statusCode === DisconnectReason.loggedOut;
            console.log(`\n⚠️  [${acc.label}] connection closed (code ${statusCode}). Logged out: ${loggedOut}`);
            if (loggedOut) purgeSession(acc);
            scheduleStart(acc, loggedOut ? 2000 : 3000);
        } else if (connection === 'open') {
            acc.connected = true;
            acc.qr = null;
            acc.qrDataURL = null;
            acc.phone = sock.user.id.split(':')[0].split('@')[0];
            acc.userJid = `${acc.phone}@s.whatsapp.net`;
            acc.ids = new Set([acc.phone, jidUser(sock.user.lid)].filter(Boolean));
            console.log(`\n✅ [${acc.label}] WhatsApp linked: +${acc.phone}\n`);
            setTimeout(() => { if (isCurrent()) refreshGroups(acc); }, 3000);
        }
    });
}

async function unlink(acc) {
    acc.generation += 1;               // from here on the old socket can neither save nor reconnect
    const old = acc.sock;
    acc.sock = null;
    markOffline(acc);
    acc.ids = new Set();
    acc.qr = null;
    acc.qrDataURL = null;
    if (old) {
        try { await Promise.race([old.logout(), new Promise(r => setTimeout(r, 5000))]); } catch (e) { /* offline */ }
        try { await old.end(undefined); } catch (e) { /* already closed */ }
    }
    purgeSession(acc);
    scheduleStart(acc, 1000);
}

setInterval(() => ACCOUNTS.forEach(refreshGroups), 5 * 60 * 1000);

// Format phone number to WhatsApp JID
function formatJid(phone) {
    if (!phone || String(phone).trim() === '') {
        return ACCOUNTS[0].userJid; // Default: Send to self ("Message Yourself")
    }
    const str = String(phone).trim();
    if (str.endsWith('@g.us') || str.endsWith('@s.whatsapp.net')) {
        return str;
    }
    const digitsOnly = str.replace(/\D/g, '');
    return `${digitsOnly}@s.whatsapp.net`;
}

// ── The page: link both numbers, choose who sends what ────────────────────────────────────────
app.get('/', (req, res) => {
    res.type('html').send(fs.readFileSync(PANEL_FILE, 'utf8'));
});

app.get('/state', (req, res) => {
    for (const a of ACCOUNTS) {
        if (a.connected && Date.now() - a.groupsAt > 60000) refreshGroups(a);   // while the page is open
    }
    res.json({
        launcher_running: launcherUp,
        accounts: ACCOUNTS.map(a => ({
            id: a.id, label: a.label, connected: a.connected, phone: a.phone,
            qr: a.connected ? null : a.qrDataURL,
            waiting: queue.filter(m => senderOf(m) === a).length,
        })),
        lanes: LANES.map(l => {
            const jid = laneJid(l);
            return {
                id: l.id, store: l.store, label: l.label, normal: l.normal, account: routes[l.id],
                waiting: queue.filter(m => laneOf(m) === l.id).length,
                hold: jid ? holdReason(accountById(routes[l.id]), jid) : null,
                group: ACCOUNTS.map(a => a.groups && a.groups.get(jid)).find(Boolean) || '',
            };
        }),
    });
});

app.post('/route', (req, res) => {
    if (!requireJson(req, res)) return;
    const changes = req.body.changes;
    const pairs = changes && typeof changes === 'object' ? Object.entries(changes) : [];
    if (!pairs.length || pairs.some(([lane, who]) => !laneById(lane) || !accountById(who))) {
        return res.status(400).json({ error: 'changes must map lane ids to account ids' });
    }
    for (const [lane, who] of pairs) {
        if (routes[lane] !== who) console.log(`  [Routes] ${lane} → ${who}`);
        routes[lane] = who;
    }
    saveRoutes();
    return res.json({ success: true, routes });
});

// Logs one number out and shows a fresh QR for it; the other number is not touched.
app.post('/relink', async (req, res) => {
    if (!requireJson(req, res)) return;
    const acc = accountById(req.body.account);
    if (!acc) return res.status(400).json({ error: 'unknown account' });
    try {
        await unlink(acc);
        return res.json({ success: true, message: `${acc.label} unlinked. Generating a fresh QR code now.` });
    } catch (err) {
        return res.status(500).json({ error: err.message });
    }
});

// Direct QR PNG image
app.get('/qr.png', async (req, res) => {
    const acc = accountById(req.query.account) || ACCOUNTS[0];
    if (!acc.qr) {
        return res.status(404).send('No QR available. Already connected or initializing.');
    }
    try {
        const buf = await QRCode.toBuffer(acc.qr);
        res.type('png').send(buf);
    } catch (e) {
        res.status(500).send('Error generating QR image.');
    }
});

// JSON Status API: the top level is the first number, as before
app.get('/status', (req, res) => {
    const main = ACCOUNTS[0];
    res.json({
        connected: main.connected,
        phone: main.phone,
        user_jid: main.userJid,
        has_qr: !!main.qr,
        qr: main.qr,
        qr_data_url: main.qrDataURL,
        accounts: ACCOUNTS.map(a => ({ id: a.id, label: a.label, connected: a.connected, phone: a.phone, has_qr: !!a.qr })),
    });
});

// List WhatsApp Groups (?account=hifza for the second number)
app.get('/groups', async (req, res) => {
    const acc = accountById(req.query.account) || ACCOUNTS[0];
    try {
        if (!acc.connected || !acc.sock) {
            return res.status(503).json({ error: `${acc.label}'s WhatsApp is not connected` });
        }
        const groups = await acc.sock.groupFetchAllParticipating();
        const list = Object.entries(groups).map(([id, g]) => ({
            id: id,
            name: g.subject,
            participants_count: g.participants ? g.participants.length : 0
        }));
        return res.json({ groups: list });
    } catch (err) {
        console.error('  [WhatsApp] ⚠️ Group fetch error:', err);
        return res.status(500).json({ error: err.message });
    }
});

// Send Message / Photo API
// ── One queue for every alert ─────────────────────────────────────────────────────────────────
// Placers, the tracker and closing/messaging are separate processes, and they all POST here; each
// number sends one message at a time. Pacing (owner): 15 Oct 2026 one every ~20 s
// so alerts looked manual — but at 180 an hour it could not keep up (20 Oct: 971 waiting, the oldest
// 8 h, 321 of them order alerts), so on 20 Oct the owner asked for instant alerts: the gap is now
// `bot_config.json → whatsapp.alert_gap_s` (default 1 s, re-read every 30 s, no restart needed).
// Screenshots are kept as files in queue_media/ — they used to sit base64 inside queue.json, which
// reached 139 MB and was rewritten on every alert, stalling the WhatsApp connection.
//   priority  1 = order alerts (placers), 5 = everything else — order alerts are never stuck
//             behind a backlog of review-request lines
//   dedupe    the same text to the same group within DEDUPE_HOURS is dropped (retry loops)
//   persist   queue.json survives a gateway restart
//   hold      a number that is not linked or not in the group keeps its alerts waiting (the page
//             says why); only the owner moves a lane to the other number
let ALERT_GAP_S = Number(process.env.ALERT_GAP_S || 1);
let ALERT_JITTER_S = Number(process.env.ALERT_JITTER_S || 0);
const DEDUPE_HOURS = Number(process.env.ALERT_DEDUPE_HOURS || 6);

function readConfig() {
    try {
        const wa = (JSON.parse(fs.readFileSync(BOT_CONFIG, 'utf8')) || {}).whatsapp || {};
        if (wa.alert_gap_s !== undefined && !isNaN(Number(wa.alert_gap_s))) ALERT_GAP_S = Math.max(0, Number(wa.alert_gap_s));
        if (wa.alert_jitter_s !== undefined && !isNaN(Number(wa.alert_jitter_s))) ALERT_JITTER_S = Math.max(0, Number(wa.alert_jitter_s));
        groupsCfg = Object.assign({}, DEFAULT_GROUPS, wa.groups || {});
        mixedGroupJid = formatJid(wa.recipient_phone) || '';
    } catch (e) { /* keep the last values */ }
}
readConfig();
setInterval(readConfig, 30000);

let queue = [];            // [{id, to, lane, text, caption, image_file, priority, at}]
let sentKeys = {};         // dedupe key -> epoch ms
const inFlight = new Set();   // ids being sent right now: a switch must not make the other number send it too

// ── Owner's hard rule (16 Oct 2026): launcher closed → NOTHING is sent. ─────────────────────
// Alerts keep queueing (saved in queue.json) and go out again once the launcher is back.
// Checked every 5 s with pgrep -a (macOS pgrep skips its own ancestors by default, and the launcher
// is the grandparent of the placer that starts this gateway); any error counts as "closed". The pattern's
// "[Pp]ython" cannot match its own text, so a shell running this check never counts.
let launcherUp = false;
let launcherCheckedAt = 0;
let launcherErr = '';
function checkLauncher() {
    execFile('pgrep', ['-a', '-f', '[Pp]ython.*gui/launcher\\.py'], (err, out, serr) => {
        const up = !err;
        launcherErr = err ? `${err.code} ${String(serr || err.message).trim().slice(0, 200)}` : '';
        if (up !== launcherUp) {
            console.log(up ? '  [Queue] ▶️  launcher is running — alerts resume'
                           : '  [Queue] ⏸️  launcher is closed — alerts held');
        }
        launcherUp = up;
        launcherCheckedAt = Date.now();
    });
}
checkLauncher();
setInterval(checkLauncher, 5000);
let stats = { sent: 0, failed: 0, deduped: 0 };

function mediaPath(id) { return path.join(MEDIA_DIR, `${id}.img`); }

function stashImage(m) {
    // keep the picture on disk, not in queue.json
    if (m.image_base64) {
        try {
            fs.writeFileSync(mediaPath(m.id), Buffer.from(m.image_base64, 'base64'));
            m.image_file = `${m.id}.img`;
            delete m.image_base64;
        } catch (e) { console.error('  [Queue] ⚠️ could not store a screenshot:', e.message); }
    }
    return m;
}

function dropImage(m) {
    if (m && m.image_file) { try { fs.unlinkSync(path.join(MEDIA_DIR, m.image_file)); } catch (e) { /* gone */ } }
}

try {
    const saved = JSON.parse(fs.readFileSync(QUEUE_FILE, 'utf8'));
    queue = (saved.queue || []).map(m => stashImage(Object.assign({ id: m.id || crypto.randomUUID() }, m)));
    sentKeys = saved.sentKeys || {};
} catch (e) { /* first run */ }

let persistTimer = null;
function persistNow() {
    persistTimer = null;
    try {
        fs.writeFileSync(QUEUE_FILE + '.tmp', JSON.stringify({ queue, sentKeys }));
        fs.renameSync(QUEUE_FILE + '.tmp', QUEUE_FILE);
    } catch (e) { console.error('  [Queue] ⚠️ could not save queue:', e.message); }
}
function persist() {
    // at most one (small) write every 2 s
    if (!persistTimer) persistTimer = setTimeout(persistNow, 2000);
}
persistNow();

function dedupeKey(to, text, caption) {
    return crypto.createHash('sha1').update(`${to}\u0000${text || ''}\u0000${caption || ''}`).digest('hex');
}

function gapMs() {
    return Math.max(0.5, ALERT_GAP_S + (Math.random() * 2 - 1) * ALERT_JITTER_S) * 1000;
}

function removeFromQueue(m) {
    queue = queue.filter(q => q.id !== m.id);
    dropImage(m);
}

async function drain(acc) {
    if (acc.sending || !acc.connected || !acc.sock || Date.now() < acc.nextSendAt) return;
    if (!launcherUp || Date.now() - launcherCheckedAt > 15000) return;   // launcher closed (or unknown) → hold
    const m = queue
        .filter(q => !inFlight.has(q.id) && senderOf(q) === acc && !holdReason(acc, q.to))
        .sort((x, y) => (x.priority - y.priority) || (x.at - y.at))[0];
    if (!m) return;
    acc.sending = true;
    inFlight.add(m.id);
    try {
        const img = m.image_file ? fs.readFileSync(path.join(MEDIA_DIR, m.image_file))
                  : (m.image_base64 ? Buffer.from(m.image_base64, 'base64') : null);
        const messageId = generateMessageIDV2(acc.sock.user?.id);
        inbox.botIds[messageId] = Date.now();
        if (img) {
            await acc.sock.sendMessage(m.to, { image: img, caption: m.caption || m.text || '' }, { messageId });
        } else {
            await acc.sock.sendMessage(m.to, { text: m.text }, { messageId });
        }
        stats.sent += 1;
        sentKeys[dedupeKey(m.to, m.text, m.caption)] = Date.now();
        removeFromQueue(m);
    } catch (err) {
        stats.failed += 1;
        console.error(`  [Queue] ⚠️ ${acc.label} send failed (try ${(m.tries || 0) + 1}):`, err.message);
        await refreshGroups(acc);
        if (!holdReason(acc, m.to)) {          // a number just removed from the group waits instead
            m.tries = (m.tries || 0) + 1;
            if (m.tries >= 5) removeFromQueue(m);   // give up on a message WhatsApp keeps refusing
        }
    }
    const cutoff = Date.now() - DEDUPE_HOURS * 3600 * 1000;
    for (const k of Object.keys(sentKeys)) if (sentKeys[k] < cutoff) delete sentKeys[k];
    const botCutoff = Date.now() - INBOX_MAX_AGE_MS;
    for (const k of Object.keys(inbox.botIds)) if (inbox.botIds[k] < botCutoff) delete inbox.botIds[k];
    acc.nextSendAt = Date.now() + gapMs();
    persist();
    saveInbox();
    inFlight.delete(m.id);
    acc.sending = false;
}
setInterval(() => ACCOUNTS.forEach(drain), 1000);

app.post('/send', async (req, res) => {
    try {
        const { to, text, image_base64, caption } = req.body;
        const priority = Number(req.body.priority || 5);
        const lane = laneById(req.body.lane) ? req.body.lane : undefined;
        const targetJid = formatJid(to);
        if (!targetJid) {
            return res.status(400).json({ error: 'Recipient JID or phone number is required when not connected to self' });
        }
        if (!image_base64 && !text) {
            return res.status(400).json({ error: 'Either image_base64 or text is required' });
        }
        const key = dedupeKey(targetJid, text, caption);
        const recent = sentKeys[key] && sentKeys[key] > Date.now() - DEDUPE_HOURS * 3600 * 1000;
        if (recent || queue.some(q => dedupeKey(q.to, q.text, q.caption) === key)) {
            stats.deduped += 1;
            return res.json({ success: true, queued: false, deduped: true });
        }
        queue.push(stashImage({ id: crypto.randomUUID(), to: targetJid, lane, text: text || '', caption: caption || '',
                                image_base64: image_base64 || null, priority, at: Date.now() }));
        persist();
        return res.json({ success: true, queued: true, position: queue.length });
    } catch (err) {
        console.error('  [WhatsApp] ⚠️ Queue error:', err);
        return res.status(500).json({ error: err.message });
    }
});

app.get('/queue', (req, res) => {
    const waitS = Math.max(0, Math.round((Math.min(...ACCOUNTS.map(a => a.nextSendAt)) - Date.now()) / 1000));
    res.json({ waiting: queue.length, order_alerts_waiting: queue.filter(q => q.priority <= 1).length,
               next_send_in_s: waitS, gap_s: ALERT_GAP_S, jitter_s: ALERT_JITTER_S,
               connected: ACCOUNTS.some(a => a.connected),
               accounts: Object.fromEntries(ACCOUNTS.map(a => [a.id, { connected: a.connected,
                                                                       waiting: queue.filter(m => senderOf(m) === a).length }])),
               launcher_running: launcherUp, paused: !launcherUp, launcher_check: launcherErr, ...stats });
});

// ── Exactly ONE gateway (20 Oct 2026) ─────────────────────────────────────────────────────────
// Two placers can start a gateway at the same moment. A second copy that did not get the port
// still logged in to WhatsApp with the same session: from 19 Oct 13:53 two clients kept knocking
// each other offline ("connected: false", failed sends) and the stray one sent from its own stale
// queue. Now a copy that does not own the port, or finds another live gateway, exits before it
// touches WhatsApp.
//
// "Alive" is not enough: after a reboot the old pid belongs to some other process. On 27 Oct 2026
// pid 1467 (the gateway of 26 Oct) was macOS's SetStoreUpdateService, so every gateway the placers
// started exited at once and no WhatsApp alert left from 23:45. The pid must be a gateway — a
// process whose command line runs this server.js.
function isGateway(pid) {
    try {
        const cmd = execFileSync('ps', ['-p', String(pid), '-o', 'command='],
                                 { encoding: 'utf8', timeout: 3000, stdio: ['ignore', 'pipe', 'ignore'] });
        return /server\.js/.test(cmd) && /node/i.test(cmd);
    } catch (e) {
        return false;                                  // no such process
    }
}
try {
    const other = parseInt(fs.readFileSync(PID_FILE, 'utf8'), 10);
    if (other && other !== process.pid && isGateway(other)) {
        console.log(`  [Gateway] another gateway (pid ${other}) is running — exiting`);
        process.exit(0);
    }
} catch (e) { /* no pid file */ }

const server = app.listen(PORT, '127.0.0.1', () => {
    fs.writeFileSync(PID_FILE, String(process.pid));
    console.log(`\n🚀 ZUHRA WhatsApp Gateway listening on http://127.0.0.1:${PORT}`);
    readConfig();
    ACCOUNTS.forEach(a => scheduleStart(a, 0));
});
server.on('error', (e) => {
    console.error(`  [Gateway] cannot listen on ${PORT} (${e.code}) — another gateway owns it; exiting`);
    process.exit(0);
});
process.on('exit', () => {
    try { if (parseInt(fs.readFileSync(PID_FILE, 'utf8'), 10) === process.pid) fs.unlinkSync(PID_FILE); } catch (e) { /* gone */ }
});
process.on('SIGTERM', () => {      // a restart must not lose the last 2 s of queue and inbox changes
    persistNow();
    saveInboxNow();
    process.exit(0);
});
