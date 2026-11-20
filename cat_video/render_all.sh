#!/bin/bash
# Renders every scene in its own Blender process, then assembles the MP4.
# Usage: ./render_all.sh [extra scene.py args, e.g. --scale 50]
set -e
cd "$(dirname "$0")"
N=$(python3 -c "import json;print(len(json.load(open('config.json'))['scenes']))")
for n in $(seq 1 $N); do
  for attempt in 1 2 3; do
    python3 scene.py --render $n "$@" > build/render_s$n.log 2>&1 && break
    echo "scene $n failed (attempt $attempt), retrying"; done
  echo "scene $n done"
done
python3 compose.py
