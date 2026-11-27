#!/bin/sh
# SPDX-License-Identifier: MIT
# Package lifecycle test in an Ubuntu chroot: install -> use -> upgrade (modified conffile) -> remove -> purge.
# Usage: sudo packaging/deb/test_lifecycle.sh <chroot-dir> <old.deb> <new.deb>
#        (<chroot-dir> = / runs directly in a disposable container; dependencies must be installed)
set -eu
R="$1"; OLD="$2"; NEW="$3"
run_in() { chroot "$R" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root "$@"; }
mounted=""
cleanup() { for m in $mounted; do umount -l "$R/$m" 2>/dev/null || true; done; }
trap cleanup EXIT
if [ "$R" != / ]; then
  for m in proc sys dev; do mkdir -p "$R/$m"; mount --bind "/$m" "$R/$m" && mounted="$m $mounted"; done
fi
cp "$OLD" "$R/tmp/old.deb"; cp "$NEW" "$R/tmp/new.deb"
step() { printf '\n== %s\n' "$*"; }

step "install $(basename "$OLD")"
run_in dpkg -i /tmp/old.deb
run_in getent group ailx
run_in stat -c '%a %U:%G %n' /var/lib/ailx /var/lib/ailx/engines /var/lib/ailx/models /usr/libexec/ailx/ailx-apply-helper
run_in head -1 /usr/bin/ailx /usr/libexec/ailx/ailx-apply-helper
run_in ailx --version
run_in ailx --no-nvml --json status | grep -E '"mode"|"state_dir"|"models_dir"'
run_in ailx --no-nvml doctor | sed -n '1,30p' || true
run_in ailx profile plan inference --include-experimental
run_in ailx-bench --version
run_in ailx system install && { echo "FAIL: system install must refuse on a dpkg-managed layer"; exit 1; } || echo "ok: system install refused (packaged)"
run_in ailx system sudoers enable
run_in cat /etc/sudoers.d/ailx

step "use: state that must survive the upgrade"
mkdir -p "$R/var/lib/ailx/models" && echo "user model" > "$R/var/lib/ailx/models/keep.gguf"
printf '# local admin edit\n' >> "$R/etc/ailx/ailx.toml"

step "upgrade to $(basename "$NEW") (modified conffile, non-interactive)"
run_in env DEBIAN_FRONTEND=noninteractive dpkg -i --force-confold /tmp/new.deb
run_in dpkg -s ailx | grep -E '^(Status|Version)'
run_in grep -c 'local admin edit' /etc/ailx/ailx.toml
run_in test -f /var/lib/ailx/models/keep.gguf && echo "ok: models kept"
run_in ailx --version

step "remove (configuration and data stay)"
run_in dpkg -r ailx
run_in test ! -e /usr/bin/ailx && echo "ok: binaries removed"
run_in test -f /etc/ailx/ailx.toml && echo "ok: conffile kept after remove"
run_in test -f /etc/sudoers.d/ailx && echo "ok: sudoers kept until purge"

step "purge (state, engines, sudoers rule removed; models kept)"
run_in dpkg -P ailx
run_in test ! -e /etc/ailx/ailx.toml && echo "ok: conffile purged"
run_in test ! -e /etc/sudoers.d/ailx && echo "ok: sudoers rule purged"
run_in test ! -e /var/lib/ailx/engines && echo "ok: engines purged"
run_in test -f /var/lib/ailx/models/keep.gguf && echo "ok: user models kept after purge"
run_in sh -c 'dpkg -l ailx 2>/dev/null | tail -1' || true
echo; echo "LIFECYCLE OK"
