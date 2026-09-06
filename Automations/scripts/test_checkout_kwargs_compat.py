#!/usr/bin/env python3
"""
test_checkout_kwargs_compat.py — guard against checkout-backend signature drift.

engine/orders.py builds one `_checkout_kwargs` dict and splats it into all
three checkout backends (true_api → vuex → DOM). The signatures drifted once
before (original_sku / shipping_amt / amazon_fees / second_sku were added to
true_api only), so whenever the engine fell back past the true API, the vuex
call raised TypeError at call time — uncaught, crashing order processing.

This test:
  1. Extracts the _checkout_kwargs keys straight from engine/orders.py source
     (importing engine.orders runs profile selection at module level, so the
     AST is parsed instead).
  2. Asserts the primary true_api path accepts EVERY key — nothing may be
     silently dropped on the main path.
  3. Asserts each backend's call binds cleanly after the kwargs pass through
     utils.helpers.filter_kwargs_for_callee — i.e. the exact call shape
     _run_single_checkout uses cannot raise TypeError.

Usage:
    cd /Users/kamranbaloch342/Desktop/Dropshipping/Development_Unit/Automations
    source ../.venv/bin/activate
    python scripts/test_checkout_kwargs_compat.py
    # or: pytest -q scripts/test_checkout_kwargs_compat.py
"""

import os
import sys
import ast
import inspect

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

from utils.helpers import filter_kwargs_for_callee
from checkout.api import place_order_fast_vuex
from checkout.dom import place_order_fast
from checkout.true_api import place_order_fast_true_api

ORDERS_PY = os.path.join(SCRIPT_DIR, "engine", "orders.py")


def _checkout_kwargs_keys():
    """Pull the keyword names out of `_checkout_kwargs = dict(...)` in orders.py."""
    with open(ORDERS_PY) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_checkout_kwargs"
                        for t in node.targets)
                and isinstance(node.value, ast.Call)):
            keys = [kw.arg for kw in node.value.keywords if kw.arg]
            assert keys, "_checkout_kwargs dict() call has no keyword arguments"
            return keys
    raise AssertionError("_checkout_kwargs assignment not found in engine/orders.py")


def _bind(func, kwargs):
    """Bind the exact positional shape _run_single_checkout uses, plus kwargs.

    Raises TypeError on any mismatch — the same error the live splat would hit.
    """
    inspect.signature(func).bind(
        "driver", "SKU-123", 1, {"state": "TX"}, "PO-1", "DM_COUPON", **kwargs)


def test_true_api_accepts_all_checkout_kwargs():
    kwargs = {k: None for k in _checkout_kwargs_keys()}
    filtered = filter_kwargs_for_callee(place_order_fast_true_api, kwargs)
    dropped = sorted(set(kwargs) - set(filtered))
    assert not dropped, (
        f"place_order_fast_true_api no longer accepts {dropped} — "
        "the primary checkout path would silently lose them")
    _bind(place_order_fast_true_api, kwargs)


def test_vuex_fallback_binds_after_filter():
    kwargs = {k: None for k in _checkout_kwargs_keys()}
    _bind(place_order_fast_vuex,
          filter_kwargs_for_callee(place_order_fast_vuex, kwargs))


def test_dom_fallback_binds_after_filter():
    kwargs = {k: None for k in _checkout_kwargs_keys()}
    _bind(place_order_fast,
          filter_kwargs_for_callee(place_order_fast, kwargs))


def test_filter_only_drops_keys_the_callee_rejects():
    kwargs = {k: None for k in _checkout_kwargs_keys()}
    for func in (place_order_fast_true_api, place_order_fast_vuex, place_order_fast):
        params = inspect.signature(func).parameters
        filtered = filter_kwargs_for_callee(func, kwargs)
        for k in set(kwargs) - set(filtered):
            assert k not in params, (
                f"filter dropped {k!r} even though {func.__name__} accepts it")


def test_filter_passes_everything_through_var_keyword():
    def sink(a, **kw):
        pass
    kwargs = {"x": 1, "y": 2}
    assert filter_kwargs_for_callee(sink, kwargs) == kwargs


if __name__ == "__main__":
    failures = 0
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
        except Exception as e:
            failures += 1
            print(f"  ❌ {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
