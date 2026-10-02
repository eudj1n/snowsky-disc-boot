#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/build.sh host
python3 -m unittest discover -s tests/conformance -p 'test_*.py' -v
