#!/bin/bash
# Taal Cafe – Start the file watcher
# Double-click this file (or run from terminal) to start watching.
# Leave the terminal window open. Press Ctrl-C to stop.

cd "$(dirname "$0")"
echo "Starting Taal Cafe file watcher..."
python3 watcher.py
