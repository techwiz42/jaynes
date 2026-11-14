"""Setting values in the environment file (~/.config/jaynes/env) without disturbing anything else in it.

Run on the server by scripts/google-auth.py:  python -m bicameral.envfile  (a JSON object of {NAME: value} on standard input). Values never appear in a command line.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from typing import Dict

NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def clean(name: str, value: str) -> str:
    """A value as it should be stored: no line breaks, and without a pasted `NAME=` in front of it (a mistake that is easy to make when copying a line)."""
    value = value.strip()
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError(f"the value for {name} has a line break")
    return re.sub(r"^[A-Z][A-Z0-9_]*=", "", value) if re.match(r"^[A-Z][A-Z0-9_]*=", value) else value


def merge_env(text: str, updates: Dict[str, str]) -> str:
    """`text` with each of `updates` set: an existing line for the name is replaced in place, a missing one is added at the end. Other lines and comments are kept."""
    for name in updates:
        if not NAME.match(name):
            raise ValueError(f"not a valid variable name: {name!r}")
    pending = {name: clean(name, value) for name, value in updates.items()}
    out = []
    for line in text.splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=", line)
        if match and match.group(1) in pending:
            name = match.group(1)
            out.append(f"{name}={pending.pop(name)}")
        else:
            out.append(line)
    out.extend(f"{name}={value}" for name, value in pending.items())
    return "\n".join(out) + "\n"


def update_file(path: str, updates: Dict[str, str]) -> None:
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path) as f:
            current = f.read()
    except FileNotFoundError:
        current = ""
    merged = merge_env(current, updates)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".env-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(merged)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> int:
    updates = json.load(sys.stdin)
    if not isinstance(updates, dict) or not all(isinstance(v, str) for v in updates.values()):
        print("expected a JSON object of strings", file=sys.stderr)
        return 2
    update_file(os.environ.get("JAYNES_ENV_FILE", "~/.config/jaynes/env"), updates)
    print("updated " + ", ".join(sorted(updates)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
