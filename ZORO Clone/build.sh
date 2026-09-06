#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status
set -e

echo "=========================================="
echo "🚀 1. Building Vite React Frontend..."
echo "=========================================="
cd frontend
npm install
npm run build
cd ..

echo "=========================================="
echo "📦 2. Installing Backend Dependencies..."
echo "=========================================="
pip install -r "Backend(Task)/requirements.txt"

echo "=========================================="
echo "🔍 3. Preparing Catalog Databases..."
echo "=========================================="
cd "Backend(Task)"

# Reassemble multi-part archive from repository
if ls catalog_enrichment.db.gz.part_* 1> /dev/null 2>&1; then
    echo "Reassembling catalog_enrichment.db from multi-part archives..."
    cat catalog_enrichment.db.gz.part_* > catalog_enrichment.db.gz
    gzip -d -f -k catalog_enrichment.db.gz
elif [ -f "catalog_enrichment.db.gz" ]; then
    echo "Decompressing catalog_enrichment.db.gz..."
    gzip -d -f -k catalog_enrichment.db.gz
fi

# Always build fresh search index from enriched catalog
echo "Building catalog_index.db from enriched catalog..."
python build_search_index.py || python3 build_search_index.py

cd ..

echo "=========================================="
echo "✅ Build Complete! Ready for Launch."
echo "=========================================="
