#!/usr/bin/env python3
"""
test_address_parse.py — the City/State/ZIP line of an Amazon address is read the same way as
before, plus the forms that used to fail.

12 Oct 2026: 'Loganville,, Georgia 30052' (a doubled comma) matched nothing, so DM_COUPON failed
order 114-0583064-9226627 every cycle. platforms.amazon._clean_city_line collapses the comma;
every line that parsed before parses to exactly the same city / state / ZIP.

Run:  python scripts/test_address_parse.py   (no Chrome, no network)
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

from platforms import amazon as A  # noqa: E402
from utils.config import STATE_MAP  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   ({detail})" if detail and not ok else ""))


def parse(line):
    m = A._CITY_STATE_ZIP.search(A._clean_city_line(line))
    if not m:
        return None
    return m.group(1).strip(), STATE_MAP.get(m.group(2).strip().upper(), "INVALID"), m.group(3).strip()


OLD = re.compile(r'([^,]+),\s*([A-Z][A-Z\s]*)\s*(\d{5}(?:-\d{4})?)', re.IGNORECASE)

print("\ncity / state / zip")
check("double comma + full state name", parse("Loganville,, Georgia 30052") == ("Loganville", "GA", "30052"),
      str(parse("Loganville,, Georgia 30052")))
check("comma, space, comma", parse("Austin, , TX 78731") == ("Austin", "TX", "78731"), str(parse("Austin, , TX 78731")))
check("ZIP+4 and abbreviation", parse("LAKE FOREST, CA 92630-2711") == ("LAKE FOREST", "CA", "92630-2711"))
check("dots still dropped (St. Louis)", parse("St. Louis, MO 63101") == ("St Louis", "MO", "63101"))
check("two-word state", parse("Albany, New York 12207") == ("Albany", "NY", "12207"))
check("no ZIP → not a city line", parse("1370 Summer Lake Terr") is None)

print("\nnothing that parsed before changes")
same = all((OLD.search(s.replace('.', '')) and OLD.search(s.replace('.', '')).groups())
           == A._CITY_STATE_ZIP.search(A._clean_city_line(s)).groups()
           for s in ("HOUSTON, TX 77040-5801", "spfld, MA 01118", "THE VILLAGES, FL 32163-3033",
                     "Hoboken, NJ 07030", "SAINT GEORGE, UT 84790", "Washington, DC 20001"))
check("ordinary lines give identical groups", same)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
