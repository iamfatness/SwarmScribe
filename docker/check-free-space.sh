#!/usr/bin/env bash
# Stop before Docker or kind fills the disk: a full disk has corrupted Docker's data on the
# development machine. Run before every step that builds an image, loads one into a
# cluster or creates a cluster:
#
#   bash docker/check-free-space.sh        # at least 20 GB free
#   bash docker/check-free-space.sh 30     # another floor, in GB
#
# On Windows (Git Bash) the disk checked is C:; elsewhere it is the one holding /.
set -euo pipefail

floor="${1:-20}"
case "$floor" in '' | *[!0-9]*) echo "usage: check-free-space.sh [GB]" >&2; exit 2 ;; esac
if [ -d /c/Windows ]; then disk=/c; name="C:"; else disk=/; name="/"; fi
# POSIX output: one line per filesystem, available space in 1024-byte blocks in column 4.
free_kb="$(df -Pk "$disk" | awk 'NR == 2 { print $4 }')"
case "$free_kb" in '' | *[!0-9]*) echo "FAILED: cannot read the free space of $name" >&2; exit 1 ;; esac
free_gb=$((free_kb / 1024 / 1024))
if [ "$free_gb" -lt "$floor" ]; then
  echo "STOP: $name has $free_gb GB free, under the floor of $floor GB. Free some space first;" >&2
  echo "do not run Docker or kind until this passes." >&2
  exit 1
fi
echo "ok: $name has $free_gb GB free (floor: $floor GB)"
