#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_ENV="${ROOT_DIR}/.env"
PROFILE="${1:-}"

usage() {
  cat <<'EOF'
Usage:
  ./switch_profile.sh baseline
  ./switch_profile.sh moderate
  ./switch_profile.sh aggressive
  ./switch_profile.sh baseline /path/to/.env
EOF
}

if [[ -z "${PROFILE}" ]]; then
  usage
  exit 1
fi

if [[ "${2:-}" != "" ]]; then
  TARGET_ENV="$2"
fi

case "${PROFILE}" in
baseline)
  PROFILE_FILE="${ROOT_DIR}/.env.baseline.example"
  ;;
moderate)
  PROFILE_FILE="${ROOT_DIR}/.env.moderate.example"
  ;;
aggressive)
  PROFILE_FILE="${ROOT_DIR}/.env.aggressive.example"
  ;;
*)
  echo "Unknown profile: ${PROFILE}" >&2
  usage
  exit 1
  ;;
esac

if [[ ! -f "${TARGET_ENV}" ]]; then
  echo "Target .env not found: ${TARGET_ENV}" >&2
  exit 1
fi

if [[ ! -f "${PROFILE_FILE}" ]]; then
  echo "Profile file not found: ${PROFILE_FILE}" >&2
  exit 1
fi

TMP_INPUT="$(mktemp)"
TMP_OUTPUT="$(mktemp)"
cp "${TARGET_ENV}" "${TMP_INPUT}"

while IFS= read -r line; do
  [[ "${line}" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
  key="${line%%=*}"
  value="${line#*=}"

  awk -v k="${key}" -v v="${value}" '
    BEGIN { replaced = 0 }
    $0 ~ ("^" k "=") { print k "=" v; replaced = 1; next }
    { print }
    END {
      if (replaced == 0) {
        print k "=" v
      }
    }
  ' "${TMP_INPUT}" > "${TMP_OUTPUT}"

  mv "${TMP_OUTPUT}" "${TMP_INPUT}"
  TMP_OUTPUT="$(mktemp)"
done < "${PROFILE_FILE}"

cp "${TARGET_ENV}" "${TARGET_ENV}.bak"
mv "${TMP_INPUT}" "${TARGET_ENV}"
rm -f "${TMP_OUTPUT}"

echo "Applied profile '${PROFILE}' to ${TARGET_ENV}"
echo "Backup saved to ${TARGET_ENV}.bak"
