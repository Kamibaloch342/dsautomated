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
app.post('/send', async (req, res) => {
    try {
        if (!isConnected || !sock) {
            return res.status(503).json({ error: 'WhatsApp Gateway is not connected' });
        }

        const { to, text, image_base64, caption } = req.body;
        const targetJid = formatJid(to);

        if (!targetJid) {
            return res.status(400).json({ error: 'Recipient JID or phone number is required when not connected to self' });
        }

        if (image_base64) {
            const buffer = Buffer.from(image_base64, 'base64');
            const result = await sock.sendMessage(targetJid, {
                image: buffer,
                caption: caption || text || '',
            });
            return res.json({ success: true, messageId: result.key.id });
        } else if (text) {
            const result = await sock.sendMessage(targetJid, {
                text: text,
            });
            return res.json({ success: true, messageId: result.key.id });
        } else {
            return res.status(400).json({ error: 'Either image_base64 or text is required' });
        }
    } catch (err) {
        console.error('  [WhatsApp] ⚠️ Send error:', err);
        return res.status(500).json({ error: err.message });
    }
});

app.listen(PORT, '127.0.0.1', () => {
    console.log(`\n🚀 ZUHRA WhatsApp Gateway listening on http://127.0.0.1:${PORT}`);
    startWhatsApp();
});
