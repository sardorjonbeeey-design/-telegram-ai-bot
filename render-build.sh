#!/usr/bin/env bash

set -e

echo "=== Installing Python packages ==="
pip install -r requirements.txt

echo "=== Installing Chromium ==="
export PLAYWRIGHT_BROWSERS_PATH=0
playwright install chromium

echo "=== Build finished ==="