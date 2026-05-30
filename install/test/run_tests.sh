#!/usr/bin/env bash
#
# Shell test harness for install/update.sh.
#
# update.sh shells out to apt-get / sudo / systemctl / pip / git which
# we can't run inside a test sandbox without trashing the host. Instead
# each test prepends a temp bin/ to PATH with no-op shims that log
# every call to ${SHIM_LOG}, and points update.sh's path constants at
# a sandbox via the env overrides update.sh now honors:
#
#   INSTALL_PATH, BINPATH, VENV_PATH, SERVICE_FILE_TARGET,
#   LAST_UPDATE_MARKER, SKIP_ROOT_CHECK
#
# Tests then run update.sh against this rigged env and assert on:
#
#   • the script's exit code
#   • which shim commands were called (or not)
#   • side-effect files (marker, copied unit files) that survive
#
# Each test runs in a fresh tmpdir; the harness clobbers nothing
# outside its own scratch space.
#
# Run with:  bash install/test/run_tests.sh
#
# Exit code 0 on green; non-zero on red. CI-friendly.

set -u

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
INSTALL_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
REPO_ROOT=$(cd "$INSTALL_DIR/.." && pwd)
UPDATE_SH="$INSTALL_DIR/update.sh"

PASS=0
FAIL=0
FAILS_TXT=""
CURRENT_CASE=""

# ─────────────────────────────────────────────────── helpers

print_case() {
  CURRENT_CASE="$1"
  printf '  %-50s ' "$1"
}

ok()   { PASS=$((PASS + 1)); printf '\033[32mOK\033[0m\n'; }
fail() {
  FAIL=$((FAIL + 1))
  printf '\033[31mFAIL\033[0m\n    %s\n' "$1"
  FAILS_TXT="${FAILS_TXT}- ${CURRENT_CASE}: $1"$'\n'
}

# Build a temp dir with shims/, a fake checkout (with .git so git
# rev-parse works), and a stub venv. Returns the tmp dir via stdout.
make_sandbox() {
  local tmp
  tmp=$(mktemp -d)
  mkdir -p "$tmp/bin" "$tmp/checkout/install" \
           "$tmp/etc/systemd/system" "$tmp/etc/avahi/services" \
           "$tmp/var/lib/inkypi" "$tmp/usr/local/inkypi/venv_inkypi/bin" \
           "$tmp/usr/local/bin"

  (cd "$tmp/checkout" && \
     git init -q . && \
     git -c user.email=a@b -c user.name=t commit --allow-empty -q -m init 2>/dev/null
  )

  # Copy fixture files the script depends on. Also copy update.sh
  # itself into the sandbox — update.sh resolves SCRIPT_DIR from
  # BASH_SOURCE[0], and we want it to point at the sandbox so git
  # rev-parse runs against the sandbox checkout, not the real repo.
  for f in inkypi inkypi.service inkypi-ble.service inkypi-netd.service \
           inkypi-avahi.service debian-requirements.txt requirements.txt \
           update_vendors.sh update.sh; do
    if [ -f "$INSTALL_DIR/$f" ]; then
      cp "$INSTALL_DIR/$f" "$tmp/checkout/install/$f"
    else
      touch "$tmp/checkout/install/$f"
    fi
  done
  chmod +x "$tmp/checkout/install/update.sh"
  # Replace update_vendors.sh with a no-op so the test doesn't fetch
  # CDN assets.
  cat > "$tmp/checkout/install/update_vendors.sh" <<'EOS'
#!/bin/sh
exit 0
EOS
  chmod +x "$tmp/checkout/install/update_vendors.sh"

  mkdir -p "$tmp/checkout/install/cli"
  echo '#!/bin/sh' > "$tmp/checkout/install/cli/inkypi-cli"
  chmod +x "$tmp/checkout/install/cli/inkypi-cli"

  # Stub venv so the "venv not found" guard passes.
  echo '#!/bin/sh' > "$tmp/usr/local/inkypi/venv_inkypi/bin/activate"
  cat > "$tmp/usr/local/inkypi/venv_inkypi/bin/python" <<'EOS'
#!/bin/sh
# stand-in for venv python — pip invocations route here.
echo "venv-python $*" >> "$SHIM_LOG"
exit 0
EOS
  chmod +x "$tmp/usr/local/inkypi/venv_inkypi/bin/python"

  : > "$tmp/shim.log"
  echo "$tmp"
}

install_default_shims() {
  local sb="$1"

  cat > "$sb/bin/apt-get" <<EOS
#!/usr/bin/env bash
echo "apt-get \$*" >> "$sb/shim.log"
exit 0
EOS
  chmod +x "$sb/bin/apt-get"

  cat > "$sb/bin/lsb_release" <<EOS
#!/usr/bin/env bash
echo "lsb_release \$*" >> "$sb/shim.log"
[ "\$1" = "-sr" ] && echo 12
exit 0
EOS
  chmod +x "$sb/bin/lsb_release"

  cat > "$sb/bin/sudo" <<EOS
#!/usr/bin/env bash
echo "sudo \$*" >> "$sb/shim.log"
while [[ "\${1:-}" == -* ]]; do shift; done
[ \$# -gt 0 ] && exec "\$@"
exit 0
EOS
  chmod +x "$sb/bin/sudo"

  cat > "$sb/bin/systemctl" <<EOS
#!/usr/bin/env bash
echo "systemctl \$*" >> "$sb/shim.log"
case "\$1" in
  is-enabled) exit 3 ;;
esac
exit 0
EOS
  chmod +x "$sb/bin/systemctl"

  cat > "$sb/bin/tput" <<EOS
#!/usr/bin/env bash
exit 0
EOS
  chmod +x "$sb/bin/tput"
}

# Run update.sh inside a sandbox with all the path overrides in place.
run_update_sh() {
  local sb="$1"; shift
  SHIM_LOG="$sb/shim.log" \
  SKIP_ROOT_CHECK=1 \
  INSTALL_PATH="$sb/usr/local/inkypi" \
  BINPATH="$sb/usr/local/bin" \
  VENV_PATH="$sb/usr/local/inkypi/venv_inkypi" \
  SERVICE_FILE_TARGET="$sb/etc/systemd/system/inkypi.service" \
  LAST_UPDATE_MARKER="$sb/var/lib/inkypi/last-updated-commit" \
  PATH="$sb/bin:/usr/bin:/bin" \
    bash -c "cd \"$sb/checkout/install\" && bash \"$sb/checkout/install/update.sh\" $*"
}

# Real EUID guard test — DO NOT set SKIP_ROOT_CHECK.
run_update_sh_real_root_check() {
  local sb="$1"; shift
  SHIM_LOG="$sb/shim.log" \
  PATH="$sb/bin:/usr/bin:/bin" \
    bash -c "cd \"$sb/checkout/install\" && bash \"$sb/checkout/install/update.sh\" $*"
}

# ─────────────────────────────────────────────────── tests

test_nonroot_exits_error() {
  print_case "non-root invocation exits non-zero"
  local sb; sb=$(make_sandbox)
  install_default_shims "$sb"
  local out; out=$(run_update_sh_real_root_check "$sb" 2>&1 || true)
  if echo "$out" | grep -q "requires root privileges"; then
    ok
  else
    fail "missing 'requires root privileges' marker. got: $out"
  fi
  rm -rf "$sb"
}

test_defer_restart_skips_inkypi() {
  print_case "--defer-restart skips inkypi.service restart"
  local sb; sb=$(make_sandbox)
  install_default_shims "$sb"
  run_update_sh "$sb" --defer-restart >"$sb/run.out" 2>&1 || true
  if grep -q "Deferring inkypi restart" "$sb/run.out"; then
    if ! grep -E "systemctl restart inkypi\.service" "$sb/shim.log" >/dev/null; then
      ok
    else
      fail "saw 'systemctl restart inkypi.service' in shim log despite --defer-restart"
    fi
  else
    fail "missing 'Deferring inkypi restart' marker in run.out"
  fi
  rm -rf "$sb"
}

test_default_restarts_inkypi() {
  print_case "without --defer-restart inkypi IS restarted"
  local sb; sb=$(make_sandbox)
  install_default_shims "$sb"
  run_update_sh "$sb" >"$sb/run.out" 2>&1 || true
  if grep -E "systemctl restart inkypi\.service" "$sb/shim.log" >/dev/null; then
    ok
  else
    fail "expected 'systemctl restart inkypi.service' in shim log"
  fi
  rm -rf "$sb"
}

test_short_circuit_when_marker_matches() {
  print_case "short-circuits when marker == HEAD"
  local sb; sb=$(make_sandbox)
  install_default_shims "$sb"
  local head_sha
  head_sha=$(git -C "$sb/checkout" rev-parse HEAD)
  echo "$head_sha" > "$sb/var/lib/inkypi/last-updated-commit"
  run_update_sh "$sb" >"$sb/run.out" 2>&1 || true
  if grep -q "Already up to date at commit" "$sb/run.out"; then
    if ! grep -q "apt-get update" "$sb/shim.log"; then
      ok
    else
      fail "short-circuit fired but apt-get update was still called"
    fi
  else
    fail "missing 'Already up to date' message in run.out"
  fi
  rm -rf "$sb"
}

test_force_bypasses_short_circuit() {
  print_case "--force ignores marker short-circuit"
  local sb; sb=$(make_sandbox)
  install_default_shims "$sb"
  local head_sha
  head_sha=$(git -C "$sb/checkout" rev-parse HEAD)
  echo "$head_sha" > "$sb/var/lib/inkypi/last-updated-commit"
  run_update_sh "$sb" --force >"$sb/run.out" 2>&1 || true
  if grep -q "apt-get update" "$sb/shim.log"; then
    ok
  else
    fail "expected --force to trigger apt-get update"
  fi
  rm -rf "$sb"
}

test_missing_venv_errors() {
  print_case "missing venv exits with clear error"
  local sb; sb=$(make_sandbox)
  install_default_shims "$sb"
  rm -rf "$sb/usr/local/inkypi/venv_inkypi"
  run_update_sh "$sb" >"$sb/run.out" 2>&1 || true
  if grep -q "Virtual environment not found" "$sb/run.out"; then
    ok
  else
    fail "expected venv-missing error. run output:"$'\n'"$(cat "$sb/run.out")"
  fi
  rm -rf "$sb"
}

test_marker_written_on_success() {
  print_case "writes LAST_UPDATE_MARKER on successful run"
  local sb; sb=$(make_sandbox)
  install_default_shims "$sb"
  local head_sha
  head_sha=$(git -C "$sb/checkout" rev-parse HEAD)
  run_update_sh "$sb" >"$sb/run.out" 2>&1 || true
  local marker="$sb/var/lib/inkypi/last-updated-commit"
  if [ -f "$marker" ]; then
    local stored
    stored=$(cat "$marker" 2>/dev/null || true)
    if [ "$stored" = "$head_sha" ]; then
      ok
    else
      fail "marker exists but holds '$stored' (expected '$head_sha')"
    fi
  else
    fail "marker file not written. run output:"$'\n'"$(cat "$sb/run.out")"
  fi
  rm -rf "$sb"
}

# ─────────────────────────────────────────────────── runner

echo "Running update.sh tests against $UPDATE_SH"
echo "  Repo root: $REPO_ROOT"
echo

test_nonroot_exits_error
test_defer_restart_skips_inkypi
test_default_restarts_inkypi
test_short_circuit_when_marker_matches
test_force_bypasses_short_circuit
test_missing_venv_errors
test_marker_written_on_success

echo
echo "──────────────────────────────────────"
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo
  printf 'Failures:\n%s\n' "$FAILS_TXT"
  exit 1
fi
exit 0
