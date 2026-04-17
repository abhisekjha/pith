#!/usr/bin/env bash
# Runs every regression test in this directory. Exit 0 if all pass.
#
#   bash tests/run_all.sh

set -u
cd "$(dirname "$0")/.."

FAIL=0

run() {
  echo "── $1 ──"
  if "$@"; then
    echo
  else
    echo "  (exit $?)"
    FAIL=$((FAIL + 1))
    echo
  fi
}

run bash      tests/test_shell_safety.sh
run python3   tests/test_safe_paths.py
run python3   tests/test_safe_fetch.py
run python3   tests/test_graph_xss.py

if [ "$FAIL" -eq 0 ]; then
  echo "All test files passed."
  exit 0
else
  echo "$FAIL test file(s) failed."
  exit 1
fi
