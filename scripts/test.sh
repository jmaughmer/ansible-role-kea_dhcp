#!/usr/bin/env bash
# Run lint and the molecule scenarios with podman.
#
# Written for the AlmaLinux 10 WSL distribution (works on any EL10 host).
# Rootless podman is used by default; if rootless systemd containers don't
# work on your WSL setup, run the script with sudo for rootful podman.
#
# Usage: scripts/test.sh [lint|default|ha|el8|all ...]   (default: all)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROLE_NAME="$(basename "$ROOT")"
CACHE="${KEA_TEST_CACHE:-$ROOT/.cache/test}"
VENV="$CACHE/venv"            # current ansible-core: lint, EL9/EL10, HA
VENV_EL8="$CACHE/venv-el8"    # ansible-core 2.16: EL8 targets (Python 3.6 dnf)

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

check_host() {
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    [[ "${ID:-}" == "almalinux" && "${VERSION_ID%%.*}" == "10" ]] ||
      echo "Note: written for AlmaLinux 10 (WSL); this is ${PRETTY_NAME:-unknown}."
  fi
  grep -qi microsoft /proc/version 2>/dev/null && echo "Running under WSL."

  local missing=()
  for cmd in podman python3 git; do command -v "$cmd" >/dev/null || missing+=("$cmd"); done
  if ((${#missing[@]})); then
    log "Installing ${missing[*]}"
    local sudo=""; ((EUID == 0)) || sudo="sudo"
    $sudo dnf install -y podman python3 python3-pip git
  fi
  podman info >/dev/null 2>&1 || die "podman is not working: run 'podman info' to see why."
}

make_venv() { # <dir> <pip packages> <collections...>
  local dir="$1" pkgs="$2"; shift 2
  if [[ ! -x "$dir/bin/molecule" ]]; then
    log "Creating $(basename "$dir")"
    python3 -m venv "$dir"
    "$dir/bin/pip" install -q --upgrade pip
    # shellcheck disable=SC2086
    "$dir/bin/pip" install -q $pkgs molecule "molecule-plugins[podman]"
  fi
  ANSIBLE_COLLECTIONS_PATH="$dir/collections" \
    "$dir/bin/ansible-galaxy" collection install -p "$dir/collections" "$@" >/dev/null
}

run_lint() {
  log "yamllint + ansible-lint"
  (cd "$ROOT" && "$VENV/bin/yamllint" . && "$VENV/bin/ansible-lint")
}

run_scenario() { # <venv> <scenario>
  local venv="$1" scenario="$2"
  log "molecule test -s $scenario"
  # molecule expects the role directory's name on ANSIBLE_ROLES_PATH.
  [[ "$ROLE_NAME" == "ansible-role-kea_dhcp" ]] ||
    die "the repository directory must be named ansible-role-kea_dhcp (it is $ROLE_NAME)"
  (cd "$ROOT" &&
    PATH="$venv/bin:$PATH" ANSIBLE_COLLECTIONS_PATH="$venv/collections" \
      molecule test -s "$scenario")
}

main() {
  local targets=("$@")
  ((${#targets[@]})) || targets=(all)
  [[ " ${targets[*]} " == *" all "* ]] && targets=(lint default ha el8)

  check_host
  make_venv "$VENV" "ansible-core ansible-lint yamllint" -r "$ROOT/requirements.yml"
  if [[ " ${targets[*]} " == *" el8 "* ]]; then
    make_venv "$VENV_EL8" "ansible-core>=2.16,<2.17" \
      "ansible.posix:>=1.5.0,<2.0.0" "community.general:>=8.0.0,<11.0.0" "containers.podman:>=1.10.0,<1.17.0"
  fi

  local failed=()
  for t in "${targets[@]}"; do
    case "$t" in
      lint) run_lint || failed+=("$t") ;;
      default|ha) run_scenario "$VENV" "$t" || failed+=("$t") ;;
      el8) run_scenario "$VENV_EL8" el8 || failed+=("$t") ;;
      *) die "unknown target: $t (expected lint, default, ha, el8 or all)" ;;
    esac
  done

  if ((${#failed[@]})); then
    printf '\n\033[1;31mFailed: %s\033[0m\n' "${failed[*]}"; exit 1
  fi
  printf '\n\033[1;32mAll passed: %s\033[0m\n' "${targets[*]}"
}

main "$@"
