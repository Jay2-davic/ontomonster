#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
# Dev launcher (no install needed): pip install -r requirements.txt, then ./run.sh
set -e
cd "$(dirname "$0")"

# Find python3 or python
if command -v python3 &> /dev/null; then
    PYTHON="python3"
elif command -v python &> /dev/null; then
    PYTHON="python"
else
    echo "Error: Python not found. Please install Python 3.8 or later."
    exit 1
fi

echo "Starting OntoMonster with $PYTHON..."
PYTHONPATH=src exec "$PYTHON" -m ontomonster "$@"
