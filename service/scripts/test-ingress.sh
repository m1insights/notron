#!/bin/sh
# Real local TLS ingress harness; synthetic loopback traffic, no upstream network.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
command -v nginx >/dev/null
sandbox=$(mktemp -d /tmp/notron-ingress.XXXXXXXX)
cleanup() {
    if [ -f "$sandbox/nginx.pid" ]; then kill "$(cat "$sandbox/nginx.pid")" 2>/dev/null || true; fi
    rm -rf -- "$sandbox"
}
trap cleanup EXIT HUP INT TERM
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$sandbox/key.pem" -out "$sandbox/cert.pem" -days 1 -subj /CN=localhost >/dev/null 2>&1
python3 - "$script_dir/../deploy/nginx.conf" "$sandbox" <<'PY'
import pathlib,socket,sys
source,folder=map(pathlib.Path,sys.argv[1:])
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
text=source.read_text().replace('${APPROVED_DOMAIN}','localhost').replace('${TLS_CERTIFICATE_PATH}',str(folder/'cert.pem')).replace('${TLS_KEY_PATH}',str(folder/'key.pem'))
text=text.replace('listen 443 ssl;',f'listen 127.0.0.1:{port} ssl;').replace('http://notron:8000','http://127.0.0.1:1')
(folder/'port').write_text(str(port))
(folder/'nginx.conf').write_text(f'pid {folder}/nginx.pid;\nerror_log {folder}/errors crit;\nevents {{}}\nhttp {{\nclient_body_temp_path {folder}/body;\nproxy_temp_path {folder}/proxy;\nfastcgi_temp_path {folder}/fastcgi;\nuwsgi_temp_path {folder}/uwsgi;\nscgi_temp_path {folder}/scgi;\n{text}\n}}\n')
PY
nginx -t -c "$sandbox/nginx.conf" -p "$sandbox" -e "$sandbox/errors"
nginx -c "$sandbox/nginx.conf" -p "$sandbox" -e "$sandbox/errors"
port=$(cat "$sandbox/port")
# Changing untrusted forwarded IPs must not evade the peer-IP auth bucket.
limited=0
for n in 1 2 3 4 5 6 7 8 9 10 11 12; do
    status=$(curl --silent --insecure --output "$sandbox/response" --write-out '%{http_code}' \
        --header "X-Forwarded-For: 192.0.2.$n" --header "Forwarded: for=192.0.2.$n" \
        "https://127.0.0.1:$port/v1/oidc/verify")
    if [ "$status" = 429 ]; then
        python3 - "$sandbox/response" <<'PYCODE'
import json,sys
assert json.load(open(sys.argv[1]))=={'code':'rate_limited'}
PYCODE
        limited=$((limited+1))
    fi
done
[ "$limited" -gt 0 ]
echo '{"tls_ingress_rate_limit":"passed"}'
