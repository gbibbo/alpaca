#!/usr/bin/env python3
"""
scripts/set_secret.py
Store a secret in the repo's .env WITHOUT it ever appearing on screen, in shell history, or in
a chat transcript. Input is hidden (getpass), asked twice to catch typos, written atomically,
and never printed back.

    python scripts/set_secret.py                 # defaults to TIINGO_API_KEY
    python scripts/set_secret.py SOME_OTHER_KEY

Refuses to write unless .env is ignored by git (so the secret cannot be committed by accident).
"""
import os
import re
import subprocess
import sys
from getpass import getpass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV_PATH = REPO / ".env"


def env_is_gitignored() -> bool:
    try:
        r = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=REPO, capture_output=True)
        return r.returncode == 0
    except Exception:
        return False


def env_is_tracked() -> bool:
    try:
        r = subprocess.run(["git", "ls-files", "--error-unmatch", ".env"], cwd=REPO, capture_output=True)
        return r.returncode == 0
    except Exception:
        return False


def write_env_var(name: str, value: str) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    pattern = re.compile(rf"^\s*(export\s+)?{re.escape(name)}\s*=")
    new_line = f"{name}={value}"
    replaced = False
    out = []
    for line in lines:
        if pattern.match(line):
            if not replaced:
                out.append(new_line)
                replaced = True
            # drop any duplicate definitions
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"# {name} (set via scripts/set_secret.py; never commit .env)")
        out.append(new_line)
    tmp = ENV_PATH.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)  # effective on POSIX; best-effort on Windows
    except Exception:
        pass
    os.replace(tmp, ENV_PATH)


def main() -> int:
    name = (sys.argv[1] if len(sys.argv) > 1 else "TIINGO_API_KEY").strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
        print(f"Refusing: '{name}' is not a valid env var name (use UPPER_SNAKE_CASE).")
        return 2
    if not sys.stdin.isatty():
        print("Refusing: run this in an interactive terminal so the key can be typed hidden.")
        return 2
    if env_is_tracked():
        print("Refusing: .env is TRACKED by git. Untrack it first (git rm --cached .env).")
        return 2
    if not env_is_gitignored():
        print("Refusing: .env is not ignored by git. Add '.env' to .gitignore first.")
        return 2

    print(f"Setting {name} in {ENV_PATH}")
    print("The key is not echoed while you type. Paste it and press Enter.")
    first = getpass(f"{name}: ").strip()
    if not first:
        print("Aborted: empty value.")
        return 1
    second = getpass("Repeat to confirm: ").strip()
    if first != second:
        print("Aborted: the two entries do not match. Nothing was written.")
        return 1
    if any(ch.isspace() for ch in first):
        print("Aborted: the value contains whitespace, which is almost certainly a paste error.")
        return 1

    write_env_var(name, first)
    print(f"Saved {name} to .env (length {len(first)}). The value was not displayed or logged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
