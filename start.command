#!/bin/bash
# PowerLeads — double-click this file to start the app (Mac/Linux).
# It installs what it needs, then opens the app in your web browser.
cd "$(dirname "$0")"

echo "Starting PowerLeads..."

# Find Python.
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else
  echo "Python is not installed. Get it from https://www.python.org/downloads/ and try again."
  read -r -p "Press Enter to close."
  exit 1
fi

# Install the one dependency (quietly; fine if already installed).
"$PY" -m pip install --quiet flask >/dev/null 2>&1

# Launch — this opens your browser automatically.
"$PY" -m pwleads serve

read -r -p "PowerLeads stopped. Press Enter to close."
