#!/usr/bin/env bash
# Publish a validated local snapshot to the benchmark service on cpu75.
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
release_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
prefix=/opt/polymer-benchmark-board
remote="$prefix/releases/$release_id"
ssh_opts=(-o BatchMode=yes -o UpdateHostKeys=no)
test -s "$base/release/board.sqlite"
for file in index.html styles.css app.js rich.js vendor/katex/katex.min.js vendor/katex/mhchem.min.js vendor/katex/katex.min.css vendor/markdown-it/markdown-it.min.js vendor/katex/fonts/KaTeX_Main-Regular.woff2; do
    test -s "$base/web/$file"
done
digest=$(python3 - "$base/release/board.sqlite" <<'PY'
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], 'rb') as f:
    for block in iter(lambda: f.read(4*1024*1024), b''):
        h.update(block)
print(h.hexdigest())
PY
)
ssh "${ssh_opts[@]}" CPU75 bash -s -- "$remote" <<'SH'
set -eu
test "$(hostname -s)" = cpu75
ip -4 addr show | grep -q 'inet 10\.13\.13\.2/'
test ! -e "$1"
install -d -m 0755 "$1" "$1/release" "$1/web"
SH
rsync -a -e 'ssh -o BatchMode=yes -o UpdateHostKeys=no' "$base/server.py" "$base/deploy/polymer-benchmark-board.service" "CPU75:$remote/"
rsync -a -e 'ssh -o BatchMode=yes -o UpdateHostKeys=no' "$base/web/" "CPU75:$remote/web/"
rsync -az -e 'ssh -o BatchMode=yes -o UpdateHostKeys=no' "$base/release/board.sqlite" "CPU75:$remote/release/"
ssh "${ssh_opts[@]}" CPU75 bash -s -- "$remote" "$digest" <<'SH'
set -euo pipefail
prefix=/opt/polymer-benchmark-board
new=$1
[[ $new == "$prefix/releases/"* && $(readlink -f -- "$new") == "$new" ]]
exec 9>"$prefix/publish.lock"
flock -x 9
chmod 0755 "$new" "$new/web" "$new/release"
chmod 0644 "$new/server.py" "$new/release/board.sqlite"
chmod -R a+rX "$new/web"
printf '%s  %s\n' "$2" "$new/release/board.sqlite" | sha256sum -c -
unit=polymer-benchmark-board.service
if test -e "/etc/systemd/system/$unit"; then
    cmp "$new/$unit" "/etc/systemd/system/$unit"
else
    install -m 0644 "$new/$unit" "/etc/systemd/system/$unit"
fi
previous=$(readlink -f -- "$prefix/current" || true)
# Atomic replacement of this application's symlink only.
python3 - "$prefix" "$new" <<'PY'
import os, sys
from pathlib import Path
base, new = map(Path, sys.argv[1:])
current = base/'current'
if current.exists() and not current.is_symlink():
    raise RuntimeError('Unexpected current path')
tmp = base/('link-' + new.name)
tmp.symlink_to(new)
os.replace(tmp, current)
PY
systemctl daemon-reload
systemctl enable "$unit"
if systemctl restart "$unit" && curl --noproxy '*' -fsS --retry 30 --retry-delay 1 --retry-connrefused 'http://10.13.13.2:18175/api/overview' -o /dev/null && curl --noproxy '*' -fsS 'http://10.13.13.2:18175/rich.js' -o /dev/null; then
    systemctl is-active "$unit"
    printf 'Published %s\n' "$new"
else
    if [[ $previous == "$prefix/releases/"* && -d $previous ]]; then
        python3 - "$prefix" "$previous" <<'PY'
import os, sys
from pathlib import Path
base, previous = map(Path, sys.argv[1:])
tmp = base/('rollback-' + str(os.getpid()))
tmp.symlink_to(previous)
os.replace(tmp, base/'current')
PY
        systemctl restart "$unit"
    else
        systemctl disable --now "$unit"
    fi
    exit 1
fi
SH
