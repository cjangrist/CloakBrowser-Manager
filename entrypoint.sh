#!/bin/bash
set -e

# Initialize data directories
mkdir -p /data/cloakbrowser /data/downloads /data/extensions /data/profiles

fonts_dir="${CLOAKBROWSER_FONTS_DIR:-/data/fonts/windows}"
if [ -d "$fonts_dir" ]; then
    fc-cache -f "$fonts_dir"
fi

# Kill stale processes from previous container runs
pkill -f 'Xvnc :[0-9]' 2>/dev/null || true
pkill -f 'cloakbrowser.*chrome' 2>/dev/null || true
pkill -f 'chromium.*fingerprint' 2>/dev/null || true
pkill -f xclip 2>/dev/null || true

archive_timestamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
profile_lock_archive="/data/trash/runtime-startup/$archive_timestamp"
x11_lock_archive="/tmp/trash/cloakbrowser-manager-startup-$archive_timestamp"
mkdir -p "$profile_lock_archive" "$x11_lock_archive"

archive_lock_file() {
    source_path="$1"
    archive_root="$2"
    archive_name="${source_path#/}"
    archive_name="${archive_name//\//__}"
    if ! mv -- "$source_path" "$archive_root/$archive_name"; then
        printf 'Warning: failed to archive stale lock %s\n' "$source_path" >&2
    fi
}

while IFS= read -r -d '' lock_path; do
    archive_lock_file "$lock_path" "$profile_lock_archive"
done < <(
    find /data/profiles -maxdepth 2 \
        \( -name 'SingletonLock' -o -name 'SingletonCookie' -o -name 'SingletonSocket' \) \
        -print0
)

while IFS= read -r -d '' lock_path; do
    archive_lock_file "$lock_path" "$x11_lock_archive"
done < <(find /tmp -maxdepth 1 -name '.X1*-lock' -print0)

# Start FastAPI (serves built React + API)
cd /app
echo ""
echo "  CloakBrowser Manager running at http://localhost:8080"
echo ""
exec uvicorn backend.main:app --host 0.0.0.0 --port 8080 --log-level warning
