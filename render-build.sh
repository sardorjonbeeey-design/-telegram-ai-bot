#!/usr/bin/env bash

set -e

echo "=== Installing Python packages ==="
pip install -r requirements.txt

echo "=== Installing Chromium ==="
playwright install chromium

echo "=== Build finished ==="