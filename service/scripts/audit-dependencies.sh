#!/bin/sh
# Public package metadata only; deployment settings/secrets are never read.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
service_dir=$(dirname "$script_dir")
audit_root=$(mktemp -d /tmp/notron-service-audit.XXXXXXXX)
trap 'rm -rf -- "$audit_root"' EXIT HUP INT TERM
uv export --project "$service_dir" --frozen --no-dev --no-emit-project --format requirements-txt --output-file "$audit_root/requirements.txt" >/dev/null
uvx --python "${NOTRON_AUDIT_PYTHON:-3.14}" pip-audit==2.10.1 --require-hashes --disable-pip --strict -r "$audit_root/requirements.txt" "$@"
