#!/bin/sh
set -eu

option() {
  python3 - "$1" "$2" <<'PY'
import json
import sys

key, default = sys.argv[1], sys.argv[2]
try:
    with open("/data/options.json", "r", encoding="utf-8") as options_file:
        value = json.load(options_file).get(key, default)
except (FileNotFoundError, json.JSONDecodeError):
    value = default

if isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

model="$(option model base-q5_1)"
language="$(option language ru)"
beam_size="$(option beam_size 1)"
debug="$(option debug false)"

set -- \
  --whisper-cpp-dir /usr/share/wyoming-whisper-cpp/whisper.cpp \
  --uri tcp://0.0.0.0:10301 \
  --data-dir /data \
  --download-dir /data \
  --model "$model" \
  --language "$language" \
  --beam-size "$beam_size"

if [ "$debug" = "true" ]; then
  set -- "$@" --debug
fi

exec python3 -m wyoming_whisper_cpp "$@"
