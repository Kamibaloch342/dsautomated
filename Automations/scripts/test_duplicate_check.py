#!/usr/bin/env python3
"""test_duplicate_check.py — tests for smart repeat-buyer duplicate detection logic.

Scenarios tested:
  1. Same Amazon Order ID -> Always blocked (Fatal duplicate)
  2. Same address + Different ASIN -> Allowed (Multi-item customer)
  3. Same address + Different SKU -> Allowed (Multi-item customer)
  4. Same address + Same ASIN placed > 72 hours ago -> Allowed (Melissa repeat buyer case)
  5. Same address + Same ASIN placed <= 72 hours ago -> Blocked (Accidental double-order)
  6. Backward compatibility (no ASIN/SKU passed) -> Blocked if <=72h, Allowed if >72h
"""
import os
import sys
import time
import unittest
import sqlite3
import tempfile
import shutil

# Add Automations to sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from utils import dup_check_local


class TestSmartDuplicateCheck(unittest.TestCase):
    def setUp(self):
        # Create a temporary sqlite db
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test_automations.db")
        
        # Point dup_check_local.DB to our test DB
        self._orig_db = dup_check_local.DB
        dup_check_local.DB = self.test_db
        
        conn = sqlite3.connect(self.test_db)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE order_placer_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amazon_order_id TEXT,
                wb_order_id TEXT,
                asin TEXT,
                sku TEXT,
                ship_street TEXT,
                ship_postal_code TEXT,
                placed_at TIMESTAMP
            );
        """)
        c.execute("""
            CREATE TABLE archive_zoro (
                wb_order_id TEXT,
                sku TEXT,
                ship_street TEXT,
                ship_zip TEXT,
                email_timestamp TEXT
            );
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        dup_check_local.DB = self._orig_db
        shutil.rmtree(self.test_dir)

    def _insert_placer_order(self, amazon_id, wb, asin, sku, street, zip_code, hours_ago):
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - hours_ago * 3600))
        conn = sqlite3.connect(self.test_db)
        c = conn.cursor()
        c.execute("""
            INSERT INTO order_placer_orders (amazon_order_id, wb_order_id, asin, sku, ship_street, ship_postal_code, placed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (amazon_id, wb, asin, sku, street, zip_code, ts))
        conn.commit()
        conn.close()

    def test_same_amazon_order_id_is_fatal_duplicate(self):
        # Existing order placed 100 hours ago
        self._insert_placer_order(
            amazon_id="111-1234567-0000001",
            wb="WB1111111111",
            asin="B0GP34MFN9",
            sku="G6441365",
            street="1 SWAIN RD",
            zip_code="19973",
            hours_ago=100.0
        )
        # Attempt to process the EXACT SAME Amazon Order ID again
        res = dup_check_local.check_address_local(
            street="1 Swain Rd",
            zip_code="19973",
            current_asin="B0GP34MFN9",
            current_sku="G6441365",
            current_amazon_id="111-1234567-0000001"
        )
        self.assertIsNotNone(res)
        self.assertEqual(res["wb"], "WB1111111111")
        self.assertEqual(res["reason"], "same_amazon_order")

    def test_melissa_case_repeat_buyer_over_72h(self):
        # Order 1 placed 5 days ago (120 hours ago)
        self._insert_placer_order(
            amazon_id="111-4856569-3265016",
            wb="WB5912259615",
            asin="B0GP34MFN9",
            sku="hath-G6441365",
            street="1 SWAIN RD",
            zip_code="19973",
            hours_ago=120.0
        )
        # Order 2 placed today (new Amazon order ID, same ASIN/address)
        res = dup_check_local.check_address_local(
            street="1 Swain Rd",
            zip_code="19973",
            current_asin="B0GP34MFN9",
            current_sku="hath-G6441365",
            current_amazon_id="113-8914936-9369045"
        )
        # Should be allowed (None) because > 72 hours elapsed
        self.assertIsNone(res)

    def test_same_asin_under_72h_is_blocked(self):
        # Order 1 placed 4 hours ago
        self._insert_placer_order(
            amazon_id="111-1111111-1111111",
            wb="WB9999999999",
            asin="B0GP34MFN9",
            sku="G6441365",
            street="1 SWAIN RD",
            zip_code="19973",
            hours_ago=4.0
        )
        # Order 2 placed now (different Amazon order ID, same ASIN, 4h later)
        res = dup_check_local.check_address_local(
            street="1 Swain Rd",
            zip_code="19973",
            current_asin="B0GP34MFN9",
            current_sku="G6441365",
            current_amazon_id="112-2222222-2222222"
        )
        # Should be blocked because it's within 72 hours (potential accidental double order)
        self.assertIsNotNone(res)
        self.assertEqual(res["wb"], "WB9999999999")

    def test_different_asin_under_72h_is_allowed(self):
        # Order 1 placed 2 hours ago: Cardboard Sheets (B0GP34MFN9)
        self._insert_placer_order(
            amazon_id="111-1111111-1111111",
            wb="WB1234567890",
            asin="B0GP34MFN9",
            sku="G6441365",
            street="1 SWAIN RD",
            zip_code="19973",
            hours_ago=2.0
        )
        # Order 2 placed 1 hour ago: Packaging Tape (B07DIFFERENT)
        res = dup_check_local.check_address_local(
            street="1 Swain Rd",
            zip_code="19973",
            current_asin="B07DIFFERENT",
            current_sku="G9999999",
            current_amazon_id="113-3333333-3333333"
        )
        # Should be allowed (None) because it's a completely different product
        self.assertIsNone(res)

    def test_backward_compatibility_no_asin(self):
        # Order placed 10 hours ago, but caller doesn't provide current_asin
        self._insert_placer_order(
            amazon_id="111-0000000-0000000",
            wb="WB8888888888",
            asin="B0GP34MFN9",
            sku="G6441365",
            street="1 SWAIN RD",
            zip_code="19973",
            hours_ago=10.0
        )
        res = dup_check_local.check_address_local(
            street="1 Swain Rd",
            zip_code="19973"
        )
        # Within 72h and ASIN unknown -> conservative duplicate block
        self.assertIsNotNone(res)
        self.assertEqual(res["wb"], "WB8888888888")


if __name__ == "__main__":
    unittest.main()
