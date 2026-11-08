#!/bin/bash
# Install HA component requirements once, then run the test suite.
V=/tmp/whistle-venv
S=$V/lib/python3.14/site-packages/homeassistant/components
if [ ! -f $V/.reqs_done ]; then
  reqs=$($V/bin/python - "$S" <<'PY'
import json, sys
out = set()
for c in ("conversation", "stt", "homeassistant", "intent", "light", "http"):
    try:
        out.update(json.load(open(f"{sys.argv[1]}/{c}/manifest.json")).get("requirements", []))
    except FileNotFoundError:
        pass
print(" ".join(sorted(out)))
PY
)
  $V/bin/pip install -q $reqs 2>&1 | grep -v notice | tail -3 && touch $V/.reqs_done
fi
cd "$(dirname "$0")/.."
timeout 300 $V/bin/python -m pytest -q -p no:cacheprovider "$@" 2>&1 | tail -80
