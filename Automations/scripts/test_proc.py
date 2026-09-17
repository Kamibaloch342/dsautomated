#!/usr/bin/env python3
"""test_proc.py — Chrome process matching used by Stop / close (no processes touched)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import proc as P  # noqa: E402

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name); print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))

D = "/Users/k/Library/Application Support/Google/Chrome/hathoro_tracking_ids"
BROWSER = f"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9333 --user-data-dir={D} --window-size=1920,1080 --no-first-run"
HELPER = f"/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Versions/152/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer) --type=renderer --user-data-dir={D} --remote-debugging-port=9333"
OWN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OTHER = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9221 --user-data-dir=/Users/k/Library/Application Support/Google/Chrome/hathoro_coupon_orders"
M = P._chrome_cmd_matches
check("browser process matches by dir", M(BROWSER, user_data_dir=D))
check("browser process matches by port", M(BROWSER, port=9333))
check("browser process matches by dir AND port", M(BROWSER, D, 9333))
check("dir with trailing slash / different case still matches", M(BROWSER, user_data_dir=D + "/") and M(BROWSER, user_data_dir=D.upper()))
check("renderer helper never matches", not M(HELPER, D, 9333) and not M(HELPER, port=9333))
check("the user's own Chrome (no --user-data-dir) never matches", not M(OWN, port=9333) and not M(OWN, user_data_dir=D))
check("another profile / port does not match", not M(OTHER, user_data_dir=D) and not M(OTHER, port=9333))
check("nothing matches when neither dir nor port is given", not M(BROWSER))
P._ps_lines = lambda: [(101, BROWSER), (102, HELPER), (103, OWN), (104, OTHER)]
check("chrome_browser_pids returns only the browser pid", P.chrome_browser_pids(D, 9333) == {101} and P.chrome_browser_pids(port=9221) == {104})
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
if FAIL:
    print("FAILED:", ", ".join(FAIL)); sys.exit(1)
