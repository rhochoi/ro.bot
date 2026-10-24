#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: bash scripts/delegate.sh [--dry-run] <codex|agy|auto> <brief-file>
  codex: codex exec -s workspace-write - (brief on stdin)
  agy:   agy -p -- BRIEF (default permissions)
  auto:  repository routing.toml keywords; Claude selection is rejected
  --dry-run: validate and show command without invoking an engine or writing logs
  --help: show this help
EOF
}

dry_run=false
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --dry-run) dry_run=true; shift ;;
esac
if [[ $# -ne 2 ]]; then usage >&2; exit 2; fi
engine=$1
brief=$2
case "$engine" in
    codex|agy|auto) ;;
    *) usage >&2; exit 2 ;;
esac
if [[ ! -f "$brief" || ! -r "$brief" ]]; then
    printf 'Error: brief must be a readable file\n' >&2
    exit 2
fi
[[ "$brief" = /* ]] || brief="$PWD/$brief"
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$root"
engine=$(python3 -c '
import sys
from pathlib import Path
from ro.log import redact

try:
    text = Path(sys.argv[2]).read_text(encoding="utf-8")
except (OSError, UnicodeError):
    sys.exit("Error: cannot read brief as UTF-8")
if not text.strip() or redact(text) != text:
    sys.exit("Error: brief must be nonempty and contain no secrets; use masked text")
engine = sys.argv[1]
if engine == "auto":
    from ro.route import choose, load_routing
    engine = choose(text, routing=load_routing(Path("routing.toml")))[0]
if engine not in ("codex", "agy"):
    sys.exit("Error: auto selected Claude; specify codex or agy explicitly")
print(engine)
' "$engine" "$brief")
if "$dry_run"; then
    if [[ "$engine" = codex ]]; then
        printf 'codex exec -s workspace-write - < BRIEF (dry-run)\n'
    else
        printf 'agy -p -- BRIEF (dry-run; default permissions)\n'
    fi
    exit 0
fi
if ! command -v "$engine" >/dev/null 2>&1; then
    printf 'Error: %s is not installed\n' "$engine" >&2
    exit 127
fi

umask 077
mkdir -p .ro-delegate
log=$(mktemp ".ro-delegate/$(date -u +%Y%m%dT%H%M%SZ).XXXXXX.log")
if [[ "$engine" = codex ]]; then
    command=(codex exec -s workspace-write -)
    input=$brief
else
    command=(agy -p -- "$(< "$brief")")
    input=/dev/null
fi
set +e
"${command[@]}" < "$input" 2>&1 | python3 -c '
import sys
from collections import deque
from ro.log import redact

tail = deque(maxlen=40)
private_key = False
# shortcut: known secret patterns only; extend ro.log.redact when new formats appear.
with open(sys.argv[1], "w", encoding="utf-8") as log:
    for line in sys.stdin:
        if "-----BEGIN " in line and "PRIVATE KEY-----" in line:
            private_key = True
        if private_key:
            if "-----END " in line and "PRIVATE KEY-----" in line:
                private_key = False
            line = "[REDACTED]\n"
        else:
            line = redact(line)
        log.write(line)
        tail.append(line)
footer = "\nLog: " + sys.argv[1] + "\n"
budget = 4096 - len(footer.encode("utf-8"))
preview = "".join(tail).encode("utf-8")[-budget:].decode("utf-8", errors="ignore")
sys.stdout.write(preview + footer)
' "$log"
status=("${PIPESTATUS[@]}")
if [[ ${status[1]} -ne 0 ]]; then exit "${status[1]}"; fi
exit "${status[0]}"
