#!/bin/sh
# A private, disposable database; never uses deployment DATABASE_URL.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
service_dir=$(dirname "$script_dir")
pg_bin=${NOTRON_TEST_PG_BIN:-}
if [ -z "$pg_bin" ]; then
    pg_bin=$(pg_config --bindir)
fi
for executable in initdb pg_ctl pg_dump pg_restore; do
    test -x "$pg_bin/$executable" || { echo "PostgreSQL test binaries unavailable" >&2; exit 1; }
done
test_root=$(mktemp -d /tmp/notron-service-test.XXXXXXXX)
chmod 700 "$test_root"
cleanup() {
    "$pg_bin/pg_ctl" -D "$test_root/data" stop -m immediate >/dev/null 2>&1 || true
    rm -rf -- "$test_root"
}
trap cleanup EXIT HUP INT TERM
mkdir "$test_root/socket"
if [ -n "${NOTRON_TEST_PG_SHARE:-}" ]; then
    "$pg_bin/initdb" -L "$NOTRON_TEST_PG_SHARE" -D "$test_root/data" --auth=trust --no-locale --encoding=UTF8 >"$test_root/init.log"
else
    "$pg_bin/initdb" -D "$test_root/data" --auth=trust --no-locale --encoding=UTF8 >"$test_root/init.log"
fi
"$pg_bin/pg_ctl" -D "$test_root/data" -l "$test_root/server.log" -o "-k $test_root/socket -h ''" -w start >/dev/null
export NOTRON_TEST_DATABASE_URL="host=$test_root/socket dbname=postgres user=$(id -un)"
export NOTRON_TEST_PG_BIN="$pg_bin"
cd "$service_dir/.."
if [ "$#" -eq 0 ]; then
    set -- "$service_dir/tests"
fi
uv run --project "$service_dir" --frozen pytest -o addopts='' -q "$@"
