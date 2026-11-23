# shellcheck shell=bash
# Release signing settings: load_release_env reads <repo>/.env (see .env.example).
#
#   source scripts/lib/release_env.sh
#   load_release_env "$REPO/.env"
#
# The file is parsed, never sourced, so it cannot run code. Each line is blank, a comment, or
# `[export ]KEY=value` with the value optionally in matching single or double quotes. Only the
# keys in RELEASE_ENV_KEYS are taken; others are ignored. A key already set (non-empty) in the
# environment wins over the file. A missing file is not an error: every key can come from the
# environment instead. Returns 2 on a line it cannot parse (naming the line number, never its
# contents, which may hold an ID). Bash 3.2 compatible.

RELEASE_ENV_KEYS="APPLE_TEAM_ID SIGN_IDENTITY NOTARY_PROFILE"

load_release_env() {
    local file="$1" line key value n=0
    local re_line='^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$'
    local re_dq='^"(.*)"$' re_sq="^'(.*)'$" re_skip='^[[:space:]]*(#.*)?$'
    [[ -f "$file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        n=$((n + 1))
        line="${line%$'\r'}"
        [[ "$line" =~ $re_skip ]] && continue
        if [[ ! "$line" =~ $re_line ]]; then
            echo "error: $file:$n: expected KEY=value" >&2
            return 2
        fi
        key="${BASH_REMATCH[2]}"
        value="${BASH_REMATCH[3]}"
        # Trailing whitespace is never part of a value.
        while [[ "$value" == *[[:space:]] ]]; do value="${value%?}"; done
        if [[ "$value" =~ $re_dq || "$value" =~ $re_sq ]]; then
            value="${BASH_REMATCH[1]}"
        fi
        case " $RELEASE_ENV_KEYS " in
            *" $key "*) ;;
            *) continue ;;
        esac
        if [[ -z "${!key:-}" ]]; then
            export "$key=$value"
        fi
    done < "$file"
}

# valid_team_id <id>: a 10-character Apple Team ID.
valid_team_id() {
    [[ "${1:-}" =~ ^[A-Z0-9]{10}$ ]]
}
