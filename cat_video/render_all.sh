#!/bin/bash
# Renders every scene (resumable: frames already rendered are skipped), then assembles the MP4.
# Usage: ./render_all.sh [extra scene.py args, e.g. --scale 50]
cd "$(dirname "$0")"
mkdir -p build
N=$(python3 -c "import json;print(len(json.load(open('config.json'))['scenes']))")
for n in $(seq 1 $N); do
  for attempt in $(seq 1 30); do
    python3 scene.py --render $n "$@" > build/render_s$n.log 2>&1 && break
    echo "scene $n crashed (attempt $attempt), resuming"
  done
  echo "scene $n done"
done
python3 compose.py
