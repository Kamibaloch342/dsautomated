"""
PostgreSQL Connection Test
──────────────────────────
Quick test to verify the database connection is working.
"""

import psycopg2

hostname = "localhost"
username = "postgres"       # Fixed: was "Postgres" (capital P)
password = "hoor2004"
database = "zoro_db"
port = 5432

try:
    conn = psycopg2.connect(
        host=hostname,
        user=username,
        password=password,
        dbname=database,
        port=port
    )

    cursor = conn.cursor()
    cursor.execute("SELECT version();")
    version = cursor.fetchone()

    print(f"✅ Connected to PostgreSQL successfully!")
    print(f"   Version: {version[0]}")

    # Check if products table exists
    cursor.execute("""
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_name = 'products';
    """)
    table_exists = cursor.fetchone()[0] > 0

    if table_exists:
        cursor.execute("SELECT COUNT(*) FROM products;")
        count = cursor.fetchone()[0]
        print(f"   Products table: {count} rows")
    else:
        print("   ⚠️  Products table not yet created. Run: python Backend(Task)/app.py")

    cursor.close()
    conn.close()

except psycopg2.Error as e:
    print(f"❌ Connection failed: {e}")
