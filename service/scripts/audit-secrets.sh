#!/bin/sh
# Scan actual runtime source and deployment/CI scripts; report only counts.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."
scan_file=$(mktemp /tmp/notron-secret-scan.XXXXXXXX)
trap 'rm -f -- "$scan_file"' EXIT HUP INT TERM
uvx --python "${NOTRON_AUDIT_PYTHON:-3.14}" detect-secrets==1.5.0 scan --all-files \
  service/notron_service service/scripts service/deploy .github/workflows notron >"$scan_file"
python3 - "$scan_file" <<'PY'
import json,sys
results=json.load(open(sys.argv[1]))['results']
count=sum(len(findings) for findings in results.values())
print(json.dumps({'potential_secrets':count},sort_keys=True))
raise SystemExit(bool(count))
PY
