/**
 * server.js — Self-Hosted WhatsApp Web Gateway for ZUHRA Software
 * Powered by @whiskeysockets/baileys.
 * 
 * Runs a lightweight local HTTP server on port 3005 that bridges ZUHRA's order pipeline
 * to your personal or business WhatsApp.
 */

const express = require('express');
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const pino = require('pino');
const qrcodeTerminal = require('qrcode-terminal');
const QRCode = require('qrcode');
const path = require('path');
const fs = require('fs');

const PORT = process.env.PORT || 3005;
const AUTH_DIR = path.join(__dirname, 'auth_session');

if (!fs.existsSync(AUTH_DIR)) {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
}

let sock = null;
let currentQR = null;
let currentQRDataURL = null;
let isConnected = false;
let userJid = null;

const app = express();
app.use(express.json({ limit: '25mb' }));

async function startWhatsApp() {
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    const { version } = await fetchLatestBaileysVersion();

    sock = makeWASocket({
        version,
        logger: pino({ level: 'silent' }),
        auth: state,
        printQRInTerminal: false, // We'll handle custom terminal & web QR
        browser: ['ZUHRA Software', 'Chrome', '120.0.0'],
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            currentQR = qr;
            try {
                currentQRDataURL = await QRCode.toDataURL(qr);
                const qrBuffer = await QRCode.toBuffer(qr);
                fs.writeFileSync(path.join(__dirname, 'latest_qr.png'), qrBuffer);
                const artifactDir = '/Users/kamibaloch342/.gemini/antigravity-ide/brain/75658f8e-7a00-4255-968c-76f07b8dd58e';
                if (fs.existsSync(artifactDir)) {
                    fs.writeFileSync(path.join(artifactDir, 'whatsapp_qr.png'), qrBuffer);
                }
            } catch (e) {
                currentQRDataURL = null;
            }
            console.log('\n======================================================');
            console.log('📱 SCAN THIS QR CODE IN WHATSAPP TO LINK ZUHRA:');
            console.log('   WhatsApp -> Settings/Menu -> Linked Devices -> Link a Device');
            console.log(`   (Or view in your browser at: http://localhost:${PORT})`);
            console.log('======================================================\n');
            qrcodeTerminal.generate(qr, { small: true });
        }

        if (connection === 'close') {
            isConnected = false;
            userJid = null;
            const statusCode = lastDisconnect?.error?.output?.statusCode;
            const shouldReconnect = statusCode !== DisconnectReason.loggedOut;

            console.log(`\n⚠️  WhatsApp Gateway connection closed (code ${statusCode}). Reconnecting: ${shouldReconnect}`);
            if (shouldReconnect) {
                setTimeout(startWhatsApp, 3000);
            } else {
                console.log('❌ WhatsApp session logged out. Purging auth_session/ and generating fresh QR...');
                try {
                    const files = fs.readdirSync(AUTH_DIR);
                    for (const f of files) {
                        fs.unlinkSync(path.join(AUTH_DIR, f));
                    }
                } catch (e) {}
                setTimeout(startWhatsApp, 2000);
            }
        } else if (connection === 'open') {
            isConnected = true;
            currentQR = null;
            currentQRDataURL = null;
            userJid = sock.user.id.split(':')[0] + '@s.whatsapp.net';
            const phone = sock.user.id.split(':')[0];
            console.log('\n======================================================');
            console.log(`✅ WHATSAPP GATEWAY CONNECTED SUCCESSFULLY!`);
            console.log(`   Linked Account Phone: +${phone}`);
            console.log(`   Self JID: ${userJid}`);
            console.log(`   Ready to dispatch order screenshots & insights.`);
            console.log('======================================================\n');
        }
    });
}

// Format phone number to WhatsApp JID
function formatJid(phone) {
    if (!phone || String(phone).trim() === '') {
        return userJid; // Default: Send to self ("Message Yourself")
    }
    const str = String(phone).trim();
    if (str.endsWith('@g.us') || str.endsWith('@s.whatsapp.net')) {
        return str;
    }
    const digitsOnly = str.replace(/\D/g, '');
    return `${digitsOnly}@s.whatsapp.net`;
}

// Web UI for QR code viewing and connection status
app.get('/', (req, res) => {
    if (isConnected) {
        return res.send(`
            <!DOCTYPE html>
            <html>
            <head>
                <title>ZUHRA WhatsApp Gateway</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                    .card { background: #1e293b; padding: 2.5rem; border-radius: 1rem; text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid #334155; max-width: 420px; }
                    .badge { display: inline-block; background: #059669; color: #fff; padding: 0.4rem 1rem; border-radius: 2rem; font-weight: bold; font-size: 0.9rem; margin-bottom: 1rem; }
                    h2 { margin: 0 0 0.5rem 0; color: #38bdf8; }
                    p { color: #94a3b8; font-size: 0.95rem; line-height: 1.5; }
                    .phone { color: #f1f5f9; font-family: monospace; font-size: 1.1rem; background: #0f172a; padding: 0.5rem; border-radius: 0.5rem; margin-top: 1rem; }
                </style>
            </head>
            <body>
                <div class="card">
                    <div class="badge">● CONNECTED & READY</div>
                    <h2>ZUHRA WhatsApp Gateway</h2>
                    <p>Your WhatsApp is linked and active. Order placement screenshots and insights will be delivered in real-time.</p>
                    <div class="phone">Linked: +${sock.user.id.split(':')[0]}</div>
                </div>
            </body>
            </html>
        `);
    }

    if (currentQRDataURL) {
        return res.send(`
            <!DOCTYPE html>
            <html>
            <head>
                <title>Link ZUHRA WhatsApp</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <meta http-equiv="refresh" content="20">
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                    .card { background: #1e293b; padding: 2rem; border-radius: 1rem; text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid #334155; max-width: 400px; }
                    h2 { margin: 0 0 0.5rem 0; color: #38bdf8; }
                    p { color: #94a3b8; font-size: 0.9rem; line-height: 1.4; margin-bottom: 1.5rem; }
                    .qr-box { background: white; padding: 1rem; border-radius: 0.75rem; display: inline-block; }
                    .qr-box img { display: block; width: 240px; height: 240px; }
                    .steps { text-align: left; margin-top: 1.5rem; background: #0f172a; padding: 1rem; border-radius: 0.5rem; font-size: 0.85rem; color: #cbd5e1; }
                    .steps ol { margin: 0; padding-left: 1.2rem; }
                    .steps li { margin-bottom: 0.3rem; }
                </style>
            </head>
            <body>
                <div class="card">
                    <h2>Link WhatsApp</h2>
                    <p>Scan this QR code with WhatsApp to connect ZUHRA Software alerts.</p>
                    <div class="qr-box">
                        <img src="${currentQRDataURL}" alt="Scan QR Code" />
                    </div>
                    <div class="steps">
                        <ol>
                            <li>Open WhatsApp on your phone</li>
                            <li>Tap <b>Settings</b> or <b>Menu (⋮)</b></li>
                            <li>Tap <b>Linked Devices</b> &rarr; <b>Link a Device</b></li>
                            <li>Point your phone camera at this screen</li>
                        </ol>
                    </div>
                </div>
            </body>
            </html>
        `);
    }

    return res.send(`
        <!DOCTYPE html>
        <html>
        <head>
            <title>Starting WhatsApp Gateway...</title>
            <meta http-equiv="refresh" content="3">
            <style>
                body { font-family: sans-serif; background: #0f172a; color: #94a3b8; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
            </style>
        </head>
        <body>
            <h3>Initializing WhatsApp Gateway, please wait...</h3>
        </body>
        </html>
    `);
});

// Relink endpoint to force clearing session and generating new QR
app.all('/relink', async (req, res) => {
    try {
        if (sock) {
            try { sock.logout(); } catch (e) {}
            try { sock.end(); } catch (e) {}
        }
        const files = fs.readdirSync(AUTH_DIR);
        for (const f of files) {
            fs.unlinkSync(path.join(AUTH_DIR, f));
        }
        isConnected = false;
        currentQR = null;
        currentQRDataURL = null;
        userJid = null;
        setTimeout(startWhatsApp, 1000);
        return res.json({ success: true, message: 'Session reset. Generating fresh QR code now.' });
    } catch (err) {
        return res.status(500).json({ error: err.message });
    }
});

// Direct QR PNG image
app.get('/qr.png', async (req, res) => {
    if (!currentQR) {
        return res.status(404).send('No QR available. Already connected or initializing.');
    }
    try {
        const buf = await QRCode.toBuffer(currentQR);
        res.type('png').send(buf);
    } catch (e) {
        res.status(500).send('Error generating QR image.');
    }
});

// JSON Status API
app.get('/status', (req, res) => {
    res.json({
        connected: isConnected,
        phone: isConnected && sock?.user?.id ? sock.user.id.split(':')[0] : null,
        user_jid: userJid,
        has_qr: !!currentQR,
        qr: currentQR,
        qr_data_url: currentQRDataURL,
    });
});

// List WhatsApp Groups
app.get('/groups', async (req, res) => {
    try {
        if (!isConnected || !sock) {
            return res.status(503).json({ error: 'WhatsApp Gateway is not connected' });
        }
        const groups = await sock.groupFetchAllParticipating();
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
// ── One paced queue for every alert (owner, 15 Oct 2026) ─────────────────────────────────────
// Placers, the tracker and closing/messaging are separate processes, and they all POST here.
// Sending each the moment it arrives produced bursts that look automated in groups the owner's
// boss reads. So /send only QUEUES; one message leaves every ALERT_GAP_S seconds (± jitter) across
// ALL groups and both stores — a Hathoro alert and a HOANK alert never go out together.
//   priority  1 = order alerts (placers), 5 = everything else — order alerts are never stuck
//             behind a backlog of review-request lines
//   dedupe    the same text to the same group within DEDUPE_HOURS is dropped (retry loops)
//   persist   queue.json survives a gateway restart
const ALERT_GAP_S = Number(process.env.ALERT_GAP_S || 20);
const ALERT_JITTER_S = Number(process.env.ALERT_JITTER_S || 4);
const DEDUPE_HOURS = Number(process.env.ALERT_DEDUPE_HOURS || 6);
const QUEUE_FILE = path.join(__dirname, 'queue.json');
const crypto = require('crypto');

let queue = [];            // [{id, to, text, caption, image_base64, priority, at}]
let sentKeys = {};         // dedupe key -> epoch ms
let nextSendAt = 0;

// ── Owner's hard rule (16 Oct 2026): launcher closed → NOTHING is sent. ─────────────────────
// Alerts keep queueing (saved in queue.json) and go out again once the launcher is back.
// Checked every 5 s with pgrep -a (macOS pgrep skips its own ancestors by default, and the launcher
// is the grandparent of the placer that starts this gateway); any error counts as "closed". The pattern's
// "[Pp]ython" cannot match its own text, so a shell running this check never counts.
const { execFile } = require('child_process');
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
let sending = false;
let stats = { sent: 0, failed: 0, deduped: 0 };

try {
    const saved = JSON.parse(fs.readFileSync(QUEUE_FILE, 'utf8'));
    queue = saved.queue || [];
    sentKeys = saved.sentKeys || {};
} catch (e) { /* first run */ }

function persist() {
    try {
        fs.writeFileSync(QUEUE_FILE + '.tmp', JSON.stringify({ queue, sentKeys }));
        fs.renameSync(QUEUE_FILE + '.tmp', QUEUE_FILE);
    } catch (e) { console.error('  [Queue] ⚠️ could not save queue:', e.message); }
}

function dedupeKey(to, text, caption) {
    return crypto.createHash('sha1').update(`${to}\u0000${text || ''}\u0000${caption || ''}`).digest('hex');
}

function gapMs() {
    return Math.max(5, ALERT_GAP_S + (Math.random() * 2 - 1) * ALERT_JITTER_S) * 1000;
}

async function drain() {
    if (sending || !queue.length || !isConnected || !sock || Date.now() < nextSendAt) return;
    if (!launcherUp || Date.now() - launcherCheckedAt > 15000) return;   // launcher closed (or unknown) → hold
    sending = true;
    queue.sort((x, y) => (x.priority - y.priority) || (x.at - y.at));
    const m = queue[0];
    try {
        if (m.image_base64) {
            await sock.sendMessage(m.to, { image: Buffer.from(m.image_base64, 'base64'), caption: m.caption || m.text || '' });
        } else {
            await sock.sendMessage(m.to, { text: m.text });
        }
        stats.sent += 1;
        sentKeys[dedupeKey(m.to, m.text, m.caption)] = Date.now();
        queue.shift();
    } catch (err) {
        stats.failed += 1;
        m.tries = (m.tries || 0) + 1;
        console.error('  [Queue] ⚠️ send failed (try ' + m.tries + '):', err.message);
        if (m.tries >= 5) queue.shift();     // give up on a message WhatsApp keeps refusing
    }
    const cutoff = Date.now() - DEDUPE_HOURS * 3600 * 1000;
    for (const k of Object.keys(sentKeys)) if (sentKeys[k] < cutoff) delete sentKeys[k];
    nextSendAt = Date.now() + gapMs();
    persist();
    sending = false;
}
setInterval(drain, 1000);

app.post('/send', async (req, res) => {
    try {
        const { to, text, image_base64, caption } = req.body;
        const priority = Number(req.body.priority || 5);
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
        queue.push({ id: crypto.randomUUID(), to: targetJid, text: text || '', caption: caption || '',
                     image_base64: image_base64 || null, priority, at: Date.now() });
        persist();
        return res.json({ success: true, queued: true, position: queue.length });
    } catch (err) {
        console.error('  [WhatsApp] ⚠️ Queue error:', err);
        return res.status(500).json({ error: err.message });
    }
});

app.get('/queue', (req, res) => {
    const waitS = Math.max(0, Math.round((nextSendAt - Date.now()) / 1000));
    res.json({ waiting: queue.length, order_alerts_waiting: queue.filter(q => q.priority <= 1).length,
               next_send_in_s: waitS, gap_s: ALERT_GAP_S, jitter_s: ALERT_JITTER_S, connected: isConnected,
               launcher_running: launcherUp, paused: !launcherUp, launcher_check: launcherErr, ...stats });
});

app.listen(PORT, '127.0.0.1', () => {
    console.log(`\n🚀 ZUHRA WhatsApp Gateway listening on http://127.0.0.1:${PORT}`);
    startWhatsApp();
});
