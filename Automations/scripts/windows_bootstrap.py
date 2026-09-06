#!/usr/bin/env python3
"""
windows_bootstrap.py — one-shot Windows setup for the Automations suite.

Run via Start_Windows.bat (double-click). Idempotent — safe to re-run any time.

  1. Verifies Python >= 3.9 (with Tkinter).
  2. Creates/repairs the venv at <Development_Unit>/.venv (Windows Scripts/ layout;
     tolerates a copied-over Mac venv living in the same folder).
  3. Installs requirements.txt into the venv.
  4. Checks Google Chrome; offers winget install if missing.
  5. Checks Tailscale (optional — only needed to VIEW the Mac dashboard privately);
     offers winget install, else prints the download link. Never fatal.
  6. Verifies the Google service-account / credential files exist.
  7. Prints a summary and (unless --no-launch) starts the launcher GUI.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)                    # .../Automations
ROOT = os.path.dirname(AUTO)                    # .../Development_Unit
VENV = os.path.join(ROOT, ".venv")
VPY = os.path.join(VENV, "Scripts", "python.exe")
VPYW = os.path.join(VENV, "Scripts", "pythonw.exe")
REQS = os.path.join(AUTO, "requirements.txt")

OK, WARN, ERR = "[ OK ]", "[WARN]", "[FAIL]"


def say(tag, msg):
    print(f"  {tag} {msg}", flush=True)


def step(title):
    print(f"\n== {title} " + "=" * max(0, 58 - len(title)), flush=True)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def check_python():
    step("Python")
    v = sys.version_info
    if v < (3, 9):
        say(ERR, f"Python {v.major}.{v.minor} is too old — install Python 3.10+ from python.org")
        say(ERR, "IMPORTANT: tick 'Add python.exe to PATH' in the installer, then re-run this.")
        sys.exit(1)
    say(OK, f"Python {v.major}.{v.minor}.{v.micro}")
    try:
        import tkinter  # noqa: F401
        say(OK, "Tkinter (GUI toolkit) available")
    except Exception:
        say(ERR, "Tkinter missing — reinstall Python from python.org with default options "
                 "(the Microsoft-Store Python sometimes lacks tcl/tk).")
        sys.exit(1)


def ensure_venv():
    step("Virtual environment")
    # A venv copied over FROM THE MAC has bin/ (not Scripts/) and mac binaries that can't run on
    # Windows — detect and remove it so we build a clean Windows one.
    if os.path.isdir(VENV) and not os.path.exists(VPY) and \
            os.path.exists(os.path.join(VENV, "bin", "python")):
        import shutil
        say(WARN, "found a macOS venv (copied over) — removing it and building a Windows one…")
        try:
            shutil.rmtree(VENV)
        except Exception as e:
            say(ERR, f"couldn't remove the old venv at {VENV}: {e} — delete that folder manually.")
            sys.exit(1)
    if not os.path.exists(VPY):
        say(WARN, f"creating venv at {VENV} (this can take a minute)…")
        r = run([sys.executable, "-m", "venv", VENV])
        if r.returncode != 0 or not os.path.exists(VPY):
            say(ERR, f"venv creation failed: {(r.stderr or r.stdout)[-300:]}")
            sys.exit(1)
    say(OK, f"venv ready: {VPY}")
    say(WARN, "installing/refreshing libraries (first run downloads ~100 MB)…")
    r = run([VPY, "-m", "pip", "install", "--upgrade", "pip", "--quiet"])
    r = run([VPY, "-m", "pip", "install", "-r", REQS, "--quiet"])
    if r.returncode != 0:
        say(ERR, f"pip install failed: {(r.stderr or r.stdout)[-400:]}")
        sys.exit(1)
    # Two-tier self-test. ESSENTIAL libs (placer + dashboard + Gmail/Sheets) are fatal.
    # DATA libs (pandas/openpyxl — only tracking + a rare CSV fallback) are a WARNING,
    # because Windows 11 Smart App Control blocks pandas' compiled numpy DLLs and we
    # don't want that to stop the core suite from running.
    ESSENTIAL = ["selenium", "googleapiclient", "google_auth_oauthlib",
                 "google.auth", "tkcalendar", "requests"]
    probe = ("import importlib,sys\n"
             "for m in sys.argv[1:]:\n"
             "    try: importlib.import_module(m); print('OK '+m)\n"
             "    except Exception as e: print('ERR '+m+' :: '+repr(e)[:160])\n")
    chk = run([VPY, "-c", probe, *ESSENTIAL])
    lines = [ln for ln in (chk.stdout or "").splitlines() if ln.strip()]
    failed = [ln for ln in lines if ln.startswith("ERR ")]
    if failed:
        for ln in failed:
            say(ERR, ln[4:])
        say(ERR, "essential libraries failed to import — the placer can't run without these.")
        say(ERR, "if the error says 'Application Control policy blocked', turn OFF Smart App Control:")
        say(ERR, "  Windows Security > App & browser control > Smart App Control settings > Off")
        sys.exit(1)
    say(OK, "essential libraries import-tested (selenium, Google API, requests, tkcalendar)")

    data_chk = run([VPY, "-c", probe, "pandas", "openpyxl"])
    data_failed = [ln for ln in (data_chk.stdout or "").splitlines() if ln.startswith("ERR ")]
    if data_failed:
        for ln in data_failed:
            say(WARN, ln[4:])
        say(WARN, "pandas/openpyxl blocked — usually Windows 11 Smart App Control blocking numpy's DLL.")
        say(WARN, "The ORDER PLACER and DASHBOARD still run fine without them.")
        say(WARN, "Only Tracking-ID import + the rare CSV fallback need these. To enable them,")
        say(WARN, "turn OFF Smart App Control: Windows Security > App & browser control >")
        say(WARN, "Smart App Control settings > Off  (then re-run this script).")
    else:
        say(OK, "data libraries (pandas, openpyxl) import-tested")


def _winget_install(pkg_id, name):
    if input(f"       install {name} now via winget? [y/N] ").strip().lower() != "y":
        return False
    r = subprocess.run(["winget", "install", "-e", "--id", pkg_id,
                        "--accept-source-agreements", "--accept-package-agreements"])
    return r.returncode == 0


def check_chrome():
    step("Google Chrome (required — placer drives Chrome)")
    cands = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    found = next((c for c in cands if os.path.exists(c)), None)
    if found:
        say(OK, f"Chrome: {found}")
        return
    say(WARN, "Chrome not found.")
    if not _winget_install("Google.Chrome", "Google Chrome"):
        say(WARN, "install Chrome manually from https://www.google.com/chrome/ before placing orders")


def check_tailscale():
    step("Tailscale (optional — private access to the Mac dashboard)")
    cands = [os.path.expandvars(r"%ProgramFiles%\Tailscale\tailscale.exe"),
             os.path.expandvars(r"%ProgramFiles(x86)%\Tailscale\tailscale.exe")]
    if any(os.path.exists(c) for c in cands) or run(["where", "tailscale"]).returncode == 0:
        say(OK, "Tailscale installed (sign in from its tray icon; accept the shared machine)")
        return
    say(WARN, "Tailscale not installed. Only needed for the private dashboard link;")
    say(WARN, "the public funnel/tunnel link works without it.")
    if not _winget_install("Tailscale.Tailscale", "Tailscale"):
        say(WARN, "later: https://tailscale.com/download/windows")


def check_credentials():
    step("Credential files")
    from importlib.util import spec_from_file_location, module_from_spec
    sys.path.insert(0, AUTO)
    problems = 0
    try:
        from utils import config
        for label, p in [
            ("Google service account", getattr(config, "GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE", "")),
            ("Gmail master credentials", getattr(config, "MASTER_CREDENTIALS_FILE", "")),
            ("Token directory", getattr(config, "TOKEN_DIRECTORY", "")),
        ]:
            if p and os.path.exists(str(p)):
                say(OK, f"{label}: found")
            else:
                problems += 1
                say(WARN, f"{label}: MISSING ({p}) — copy it from the Mac (same relative path)")
    except Exception as e:
        problems += 1
        say(WARN, f"could not import utils.config to verify credentials: {e}")
    if problems:
        say(WARN, "the launcher will start, but Gmail/Sheets features need the files above.")


def launch():
    step("Launching")
    exe = VPYW if os.path.exists(VPYW) else VPY
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    subprocess.Popen([exe, os.path.join(AUTO, "gui", "launcher.py")],
                     cwd=AUTO, env=env,
                     creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    say(OK, "launcher started — this window can be closed.")


def main():
    print("=" * 64)
    print("  AUTOMATIONS — Windows setup")
    print("=" * 64)
    if os.name != "nt":
        say(WARN, "not Windows — this bootstrap is for the Windows machine. Nothing done.")
        return
    check_python()
    ensure_venv()
    check_chrome()
    check_tailscale()
    check_credentials()
    if "--no-launch" not in sys.argv:
        launch()
    print("\nAll set. Next time just double-click Start_Windows.bat — it skips straight to the launcher.")


if __name__ == "__main__":
    main()
