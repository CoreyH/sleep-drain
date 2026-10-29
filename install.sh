#!/bin/bash
# Install the drain logger and sleep-guard. Run as root (pkexec bash install.sh).
set -euo pipefail
cd "$(dirname "$0")"

install -m 755 sleep-drain-log /usr/lib/systemd/system-sleep/sleep-drain-log
install -m 755 sleep-guard /usr/local/bin/sleep-guard
install -m 644 sleep-guard.service /etc/systemd/system/sleep-guard.service
install -m 755 sleep-drain-radios /usr/local/bin/sleep-drain-radios
install -m 755 sleep-drain-diff /usr/local/bin/sleep-drain-diff
install -m 755 sleep-drain-wake /usr/local/bin/sleep-drain-wake
install -m 755 sleep-drain-pcie /usr/local/bin/sleep-drain-pcie
install -m 755 sleep-drain-pmp /usr/local/bin/sleep-drain-pmp
cc -O2 -Wall -o /usr/local/bin/pmp-request pmp-request.c
# The pmp_sleep_release experiment module, built for the running kernel in a
# scratch copy so the repo gets no root-owned build files. Installed outside
# /usr/lib/modules, so nothing loads it but sleep-drain-pmp. Not fatal: right
# after a kernel update, the running kernel's headers are gone.
build=$(mktemp -d)
cp pmp-sleep-release/pmp_sleep_release.c pmp-sleep-release/Makefile "$build"/
if make -s -C "$build" > /dev/null; then
  install -D -m 644 "$build/pmp_sleep_release.ko" /usr/local/lib/sleep-drain/pmp_sleep_release.ko
else
  echo "pmp_sleep_release not built for $(uname -r); the pmp experiment is unavailable" >&2
fi
rm -rf -- "${build:?}"
install -d -m 755 /etc/sleep-drain /var/log/sleep-drain
touch /var/log/sleep-drain.log
chmod 644 /var/log/sleep-drain.log

systemctl daemon-reload
systemctl enable sleep-guard.service
systemctl restart sleep-guard.service
echo "installed"
