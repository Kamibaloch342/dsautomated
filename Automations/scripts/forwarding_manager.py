#!/usr/bin/env python3
"""
forwarding_manager.py — Web portal for managing Mega-Access authorization and
forwarding across all store and Zoro inboxes to mkamrankhan342@gmail.com.
"""
import os
import sys
import re
import json
import base64
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)

from utils import accounts_config
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

PORT = 8899
TARGET_EMAIL = "mkamrankhan342@gmail.com"
TOKEN_DIR = os.path.join(_AUTO, "data", "All_Tokens")
CLIENT_FILE = os.path.join(TOKEN_DIR, "AB ECOM Credentials.json")

MEGA_SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/gmail.settings.sharing",
]

active_flows = {}

def get_service(acct):
    token_path = os.path.join(TOKEN_DIR, f"{acct}_token.json")
    if not os.path.exists(token_path):
        return None
    try:
        creds = Credentials.from_authorized_user_file(token_path)
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"Error loading {acct}: {e}")
        return None

def check_account_status(acct):
    token_path = os.path.join(TOKEN_DIR, f"{acct}_token.json")
    if not os.path.exists(token_path):
        return {"status": "no_token", "email": None, "forwarding": False, "target": None, "verified": False, "mega": False}
    
    # Check token scopes
    has_mega = False
    try:
        with open(token_path) as f:
            d = json.load(f)
            scopes = d.get("scopes", [])
            has_mega = "https://mail.google.com/" in scopes or (
                "https://www.googleapis.com/auth/gmail.modify" in scopes and
                "https://www.googleapis.com/auth/gmail.send" in scopes
            )
    except Exception:
        pass

    svc = get_service(acct)
    if not svc:
        return {"status": "error", "email": None, "forwarding": False, "target": None, "verified": False, "mega": has_mega}
    try:
        email = svc.users().getProfile(userId="me").execute().get("emailAddress", "")
        fwd = svc.users().settings().getAutoForwarding(userId="me").execute()
        enabled = fwd.get("enabled", False)
        target = fwd.get("emailAddress", "")
        
        fwd_addrs = svc.users().settings().forwardingAddresses().list(userId="me").execute().get("forwardingAddresses", [])
        verified = any(a.get("forwardingEmail", "").lower() == TARGET_EMAIL.lower() and a.get("verificationStatus") == "accepted" for a in fwd_addrs)
        added = any(a.get("forwardingEmail", "").lower() == TARGET_EMAIL.lower() for a in fwd_addrs)
        
        return {
            "status": "ok",
            "email": email,
            "forwarding": enabled and (target.lower() == TARGET_EMAIL.lower()),
            "target": target,
            "added": added,
            "verified": verified,
            "mega": has_mega
        }
    except Exception as e:
        return {"status": "error", "email": None, "forwarding": False, "target": None, "verified": False, "mega": has_mega, "error": str(e)}

def get_latest_verification_for(acct_email):
    hub_svc = get_service("mkamrankhan342")
    if not hub_svc:
        return None, None
    query = "from:forwarding-noreply@google.com"
    try:
        res = hub_svc.users().messages().list(userId="me", q=query, maxResults=8).execute()
        msgs = res.get("messages", [])
        for m in msgs:
            msg = hub_svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
            
            def extract_text(payload):
                text = ""
                if "parts" in payload:
                    for p in payload["parts"]:
                        text += extract_text(p)
                elif "data" in payload.get("body", {}):
                    text += base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
                return text
            
            body = extract_text(msg.get("payload", {}))
            if acct_email.lower() in body.lower():
                link_match = re.search(r'(https://mail-settings\.google\.com/mail/vf-[^\s\<\>"\']+)', body)
                vf_link = link_match.group(1) if link_match else None
                code_match = re.search(r'Confirmation code:\s*([0-9]+)', body)
                code = code_match.group(1) if code_match else None
                return vf_link, code
    except Exception as e:
        print(f"Error checking verification: {e}")
    return None, None

class ForwardingHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[portal] {args[0]}", flush=True)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.render_index()
        elif path == "/auth":
            acct = params.get("account", [None])[0]
            if not acct:
                self.send_error(400, "Missing account parameter")
                return
            self.initiate_auth(acct)
        elif path == "/oauth2callback":
            self.handle_callback(params)
        elif path == "/guide":
            acct = params.get("account", [None])[0]
            self.render_guide(acct)
        elif path == "/activate":
            acct = params.get("account", [None])[0]
            self.handle_activation(acct)
        else:
            self.send_error(404, "Not Found")

    def initiate_auth(self, acct):
        email_hint = None
        svc = get_service(acct)
        if svc:
            try:
                email_hint = svc.users().getProfile(userId="me").execute().get("emailAddress")
            except Exception:
                pass
        
        flow = InstalledAppFlow.from_client_secrets_file(
            CLIENT_FILE,
            scopes=MEGA_SCOPES,
            redirect_uri=f"http://localhost:{PORT}/oauth2callback"
        )
        auth_url, state = flow.authorization_url(
            prompt="consent",
            access_type="offline",
            state=acct,
            login_hint=email_hint
        )
        active_flows[acct] = flow

        self.send_response(302)
        self.send_header("Location", auth_url)
        self.end_headers()

    def handle_callback(self, params):
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        
        if not code or not state:
            self.render_error("Authorization failed: Missing code or state.")
            return

        flow = active_flows.get(state)
        if not flow:
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_FILE,
                scopes=MEGA_SCOPES,
                redirect_uri=f"http://localhost:{PORT}/oauth2callback"
            )

        try:
            flow.fetch_token(code=code)
            creds = flow.credentials
            
            token_path = os.path.join(TOKEN_DIR, f"{state}_token.json")
            modify_path = os.path.join(TOKEN_DIR, f"{state}_modify_token.json")
            
            with open(token_path + ".tmp", "w") as f:
                f.write(creds.to_json())
            os.replace(token_path + ".tmp", token_path)

            with open(modify_path + ".tmp", "w") as f:
                f.write(creds.to_json())
            os.replace(modify_path + ".tmp", modify_path)

            svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
            acct_email = svc.users().getProfile(userId="me").execute().get("emailAddress", state)

            self.render_success(state, acct_email, "Mega Access Granted (Full Mail Control, Send, Read, Modify, Settings)!")

        except Exception as e:
            self.render_error(f"Error saving token for {state}: {e}")

    def render_guide(self, acct):
        svc = get_service(acct)
        acct_email = svc.users().getProfile(userId="me").execute().get("emailAddress", acct) if svc else acct
        
        status = check_account_status(acct)
        vf_link, code = get_latest_verification_for(acct_email)
        direct_settings_url = f"https://mail.google.com/mail/u/?authuser={acct_email}#settings/fwdandpop"

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Forwarding Setup — {acct}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8f9fa; margin: 0; padding: 40px 20px; }}
        .card {{ background: white; max-width: 680px; margin: 0 auto; padding: 36px 40px; border-radius: 12px; box-shadow: 0 4px 16px rgba(0,0,0,0.08); }}
        h2 {{ margin-top: 0; color: #202124; }}
        .step {{ margin: 24px 0; padding: 20px; border-radius: 8px; border-left: 4px solid #1a73e8; background: #f8fafd; }}
        .step h3 {{ margin: 0 0 10px 0; font-size: 16px; color: #1a73e8; }}
        .step p {{ margin: 0 0 12px 0; color: #3c4043; line-height: 1.5; }}
        .btn {{ display: inline-block; padding: 10px 20px; background: #1a73e8; color: white; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 14px; }}
        .btn-green {{ background: #1e8e3e; }}
        .code-box {{ display: inline-block; background: #e8f0fe; color: #174ea6; font-size: 20px; font-weight: bold; padding: 6px 14px; border-radius: 6px; letter-spacing: 2px; }}
    </style>
</head>
<body>
    <div class="card">
        <h2>Forwarding & Verification for <code>{acct}</code> ({acct_email})</h2>
        <p>Google requires consumer (@gmail.com) forwarding to be initiated once from the Gmail interface:</p>

        <div class="step">
            <h3>Step 1: Add Forwarding Address in Gmail</h3>
            <p>Click below to open the forwarding settings page for <strong>{acct_email}</strong>:</p>
            <a class="btn" href="{direct_settings_url}" target="_blank">Open Gmail Settings Tab &rarr;</a>
            <p style="margin-top:12px; font-size:13px; color:#5f6368;">
                Click <strong>"Add a forwarding address"</strong>, enter <code>{TARGET_EMAIL}</code>, and click <strong>Next &rarr; Proceed &rarr; OK</strong>.
            </p>
        </div>

        <div class="step" style="border-left-color: #34a853; background: #f6fbf7;">
            <h3>Step 2: Instant Verification Code</h3>
            <p>Our automation checks <strong>{TARGET_EMAIL}</strong> for the verification email as soon as Google sends it:</p>
            {"<div style='margin-bottom:12px;'><strong>Verification Code:</strong> <span class='code-box'>" + code + "</span></div>" if code else "<p style='color:#b06000;'><em>No code received yet. Add the address in Step 1, then click 'Refresh Status' below.</em></p>"}
            {("<p><a class='btn btn-green' href='" + vf_link + "' target='_blank'>1-Click Confirm Verification Link</a></p>") if vf_link else ""}
            <p style="font-size:13px; color:#5f6368;">Paste the code into the Gmail tab and click Verify, OR click the 1-Click Confirm link above.</p>
        </div>

        <div class="step" style="border-left-color: #f2994a; background: #fffcf7;">
            <h3>Step 3: Enable Auto-Forwarding</h3>
            <p>Once verified, either select "Forward a copy..." in your Gmail tab and click Save, or activate via API:</p>
            <a class="btn btn-green" href="/activate?account={acct}">Activate Auto-Forwarding via API</a>
            <a class="btn" style="background:#5f6368; margin-left:8px;" href="/guide?account={acct}">Refresh Status</a>
        </div>

        <div style="margin-top:24px; text-align:center;">
            <a href="/" style="color:#1a73e8; text-decoration:none; font-size:14px;">&larr; Back to Dashboard</a>
        </div>
    </div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def handle_activation(self, acct):
        svc = get_service(acct)
        if not svc:
            self.render_error(f"Cannot load token for {acct}")
            return
        try:
            svc.users().settings().updateAutoForwarding(
                userId="me",
                body={"enabled": True, "emailAddress": TARGET_EMAIL, "disposition": "leaveInInbox"}
            ).execute()
            acct_email = svc.users().getProfile(userId="me").execute().get("emailAddress", acct)
            self.render_success(acct, acct_email, "Auto-forwarding successfully ENABLED!")
        except Exception as e:
            self.render_guide(acct)

    def render_index(self):
        inboxes = sorted(accounts_config.gmail_tokens())
        rows = []
        for acct in inboxes:
            if acct == "mkamrankhan342":
                continue
            info = check_account_status(acct)
            email = info.get("email") or acct

            # Forwarding badge
            if info["forwarding"]:
                fwd_badge = '<span style="background:#e6f4ea; color:#137333; padding:4px 8px; border-radius:12px; font-weight:600; font-size:12px;">Active & Forwarding</span>'
            elif info["verified"]:
                fwd_badge = '<span style="background:#fef7e0; color:#b06000; padding:4px 8px; border-radius:12px; font-weight:600; font-size:12px;">Verified (Off)</span>'
            else:
                fwd_badge = '<span style="background:#fce8e6; color:#c5221f; padding:4px 8px; border-radius:12px; font-weight:600; font-size:12px;">Not Forwarding</span>'

            # Scope badge
            if info["mega"]:
                scope_badge = '<span style="background:#e8f0fe; color:#1a73e8; padding:4px 8px; border-radius:12px; font-weight:600; font-size:12px;">⚡ Mega Access</span>'
            else:
                scope_badge = '<span style="background:#f1f3f4; color:#5f6368; padding:4px 8px; border-radius:12px; font-weight:600; font-size:12px;">Read Only</span>'

            # Actions
            auth_btn = f'<a href="/auth?account={acct}" style="padding:5px 10px; background:#1a73e8; color:white; border-radius:6px; text-decoration:none; font-size:12px; font-weight:600; margin-right:6px;">Authorize Mega</a>'
            guide_btn = f'<a href="/guide?account={acct}" style="padding:5px 10px; background:#f1f3f4; color:#3c4043; border-radius:6px; text-decoration:none; font-size:12px; font-weight:600;">Forward Setup</a>'

            rows.append(f"""
            <tr style="border-bottom:1px solid #e0e0e0;">
                <td style="padding:12px 14px; font-weight:600;">{acct}</td>
                <td style="padding:12px 14px; color:#5f6368; font-size:13px;">{email}</td>
                <td style="padding:12px 14px;">{fwd_badge}</td>
                <td style="padding:12px 14px;">{scope_badge}</td>
                <td style="padding:12px 14px; text-align:right;">{auth_btn}{guide_btn}</td>
            </tr>
            """)

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Gmail Mega-Access & Forwarding Hub</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #f8f9fa; margin: 0; padding: 40px 20px; }}
        .container {{ max-width: 960px; margin: 0 auto; background: white; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); overflow: hidden; }}
        .header {{ padding: 24px 32px; background: #1a73e8; color: white; }}
        .header h1 {{ margin: 0 0 8px 0; font-size: 24px; }}
        .header p {{ margin: 0; opacity: 0.9; font-size: 14px; }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; }}
        th {{ padding: 12px 14px; background: #f1f3f4; color: #202124; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
        .footer {{ padding: 20px 32px; background: #f8f9fa; border-top: 1px solid #e0e0e0; font-size: 13px; color: #70757a; text-align: center; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Gmail Mega-Access & Forwarding Hub</h1>
            <p>Grant full permissions (read, write, send, organize, settings) and route mail to <strong>{TARGET_EMAIL}</strong></p>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Account</th>
                    <th>Email Address</th>
                    <th>Forwarding</th>
                    <th>Permissions</th>
                    <th style="text-align:right;">Actions</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
        <div class="footer">
            Tokens saved with <code>mail.google.com</code> + <code>modify</code> + <code>send</code> in <code>Automations/data/All_Tokens</code>
        </div>
    </div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def render_success(self, acct, email, msg):
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Success — {acct}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8f9fa; display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
        .card {{ background:white; padding:40px; border-radius:12px; box-shadow:0 4px 16px rgba(0,0,0,0.1); max-width:520px; text-align:center; }}
        .icon {{ font-size:54px; margin-bottom:16px; color:#1e8e3e; }}
        h2 {{ margin:0 0 10px 0; color:#202124; }}
        p {{ color:#5f6368; line-height:1.5; }}
        .btn {{ display:inline-block; margin-top:20px; padding:10px 24px; background:#1a73e8; color:white; border-radius:6px; text-decoration:none; font-weight:600; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">⚡</div>
        <h2>{acct} Mega Access Granted!</h2>
        <p><strong>{email}</strong> now has full permissions for reading, sending, organizing/labeling, and settings management.</p>
        <p style="color:#1e8e3e; font-weight:600;">{msg}</p>
        <a class="btn" href="/">Configure Next Account</a>
    </div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def render_error(self, err_msg):
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Error</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8f9fa; display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
        .card {{ background:white; padding:40px; border-radius:12px; box-shadow:0 4px 16px rgba(0,0,0,0.1); max-width:500px; text-align:center; }}
        .icon {{ font-size:54px; margin-bottom:16px; color:#d93025; }}
        h2 {{ margin:0 0 10px 0; color:#202124; }}
        p {{ color:#d93025; line-height:1.5; }}
        .btn {{ display:inline-block; margin-top:20px; padding:10px 24px; background:#1a73e8; color:white; border-radius:6px; text-decoration:none; font-weight:600; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">⚠</div>
        <h2>Setup Notice</h2>
        <p>{err_msg}</p>
        <a class="btn" href="/">Return to Dashboard</a>
    </div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

def run():
    server = HTTPServer(("127.0.0.1", PORT), ForwardingHandler)
    print(f"Forwarding Manager portal running at http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    run()
