"""python_sandbox: run a short piece of Python in a separate, locked-down process.

On macOS this uses `sandbox-exec` (Apple's sandbox; deprecated, and weaker than a container). The child process:
  - is a fresh interpreter (`-I -S`: no site packages, no environment variables, no user site) that never imports this package;
  - cannot use the network (no sockets, no DNS), cannot start other programs, and cannot write anywhere but its own temporary directory;
  - cannot read /Users, /Volumes, other temporary directories, or anything outside the system and the Python installation, so the server's keys file is out of reach;
  - gets 5 seconds of CPU time and 8 of wall time, small file sizes, and its output is capped. Memory: macOS does not enforce address-space limits, so a watchdog in the server
    checks the child's resident memory every 100 ms and kills it above MEMORY_LIMIT_MB. That is a limit with a short delay, not a hard one: a single allocation can overshoot
    before the next check.
It is never run in the server process. If the sandbox is not available, or its self-test shows a guarantee does not hold on this machine, the tool is not registered: there is no
fallback to running the code unprotected.
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import signal
import sys
import tempfile
from typing import Optional

from pydantic import BaseModel, Field

from .base import Tool, ToolResult

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
CPU_SECONDS = 5
WALL_SECONDS = 8
MEMORY_LIMIT_MB = 256
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_OUTPUT_CHARS = 10_000

RUNNER = r'''
import ast, contextlib, json, sys

LIMIT = %(limit)d


class Capped:
    def __init__(self): self.parts, self.size, self.cut = [], 0, False
    def write(self, text):
        room = LIMIT - self.size
        if room <= 0: self.cut = True; return len(text)
        self.parts.append(text[:room]); self.size += min(len(text), room); self.cut = self.cut or len(text) > room
        return len(text)
    def flush(self): pass
    def value(self): return "".join(self.parts) + ("\n[output truncated]" if self.cut else "")


code_path, result_path = sys.argv[1], sys.argv[2]
source = open(code_path).read()
out, err = Capped(), Capped()
value = None
try:
    tree = ast.parse(source)
    last = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = ast.Expression(tree.body.pop().value)
    scope = {"__name__": "__main__"}
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        exec(compile(tree, "<sandbox>", "exec"), scope)
        if last is not None:
            result = eval(compile(last, "<sandbox>", "eval"), scope)
            value = None if result is None else repr(result)[:LIMIT]
except BaseException as error:
    import traceback
    err.write("".join(traceback.format_exception_only(type(error), error)))
json.dump({"stdout": out.value(), "stderr": err.value(), "result": value}, open(result_path, "w"))
'''


def _real(path: str) -> str:
    return os.path.realpath(path)


def build_profile(tmp: str, python_prefix: str) -> str:
    """The sandbox profile. Later rules win, so the broad denials come first and the narrow allowances after them."""
    quote = lambda p: p.replace("\\", "\\\\").replace('"', '\\"')
    return "\n".join([
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        '(deny file-write* (require-not (subpath "%s")))' % quote(tmp),
        '(allow file-write* (literal "/dev/null") (literal "/dev/dtracehelper") (literal "/dev/tty"))',
        '(deny file-read* (subpath "/Users") (subpath "/Volumes") (subpath "/private/var/folders") (subpath "/private/tmp") (subpath "/private/var/root") (subpath "/home"))',
        '(allow file-read* (subpath "%s") (subpath "%s"))' % (quote(tmp), quote(python_prefix)),
        "(deny process-fork)",
        '(deny process-exec* (require-not (subpath "%s")))' % quote(python_prefix),
    ])


def _limits() -> None:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        limit = MEMORY_LIMIT_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_DATA, (limit, limit))
    except (ValueError, OSError):
        pass                                           # macOS does not always allow or enforce these; the wall-clock kill is the backstop


async def _resident_mb(pid: int) -> float:
    """The resident memory of a process, in megabytes, or 0 when it has gone."""
    try:
        process = await asyncio.create_subprocess_exec("/bin/ps", "-o", "rss=", "-p", str(pid), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await process.communicate()
        return int(out.strip() or 0) / 1024.0
    except (OSError, ValueError):
        return 0.0


async def _watch_memory(process, limit_mb: float, killed: dict) -> None:
    while process.returncode is None:
        if await _resident_mb(process.pid) > limit_mb:
            killed["memory"] = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return
        await asyncio.sleep(0.1)


class Sandbox:
    def __init__(self) -> None:
        self.python = _real(sys.executable)
        self.prefix = _real(sys.base_prefix)

    @staticmethod
    def supported() -> bool:
        return platform.system() == "Darwin" and os.path.exists(SANDBOX_EXEC)

    async def execute(self, code: str, wall_seconds: float = WALL_SECONDS) -> dict:
        tmp = _real(tempfile.mkdtemp(prefix="jsbx-"))
        try:
            with open(os.path.join(tmp, "runner.py"), "w") as f:
                f.write(RUNNER % {"limit": MAX_OUTPUT_CHARS})
            with open(os.path.join(tmp, "code.py"), "w") as f:
                f.write(code)
            result_path = os.path.join(tmp, "result.json")
            process = await asyncio.create_subprocess_exec(
                SANDBOX_EXEC, "-p", build_profile(tmp, self.prefix), self.python, "-I", "-S", "-B", os.path.join(tmp, "runner.py"), os.path.join(tmp, "code.py"), result_path,
                cwd=tmp, env={}, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, start_new_session=True, preexec_fn=_limits)
            timed_out = False
            killed: dict = {}
            watchdog = asyncio.get_running_loop().create_task(_watch_memory(process, MEMORY_LIMIT_MB, killed))
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=wall_seconds)
            except asyncio.TimeoutError:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                _, stderr = await process.communicate()
            finally:
                watchdog.cancel()
            data = {}
            try:
                with open(result_path) as f:
                    data = json.loads(f.read(4 * MAX_OUTPUT_CHARS))
            except (OSError, ValueError):
                pass
            return {"timed_out": timed_out, "memory_killed": bool(killed.get("memory")), "returncode": process.returncode, "launcher_stderr": stderr.decode("utf-8", "replace")[:500],
                    "stdout": data.get("stdout", ""), "stderr": data.get("stderr", ""), "result": data.get("result"), "have_result": bool(data)}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


SELF_TEST = r'''
import os, socket, sys
checks = {}
def blocked(name, fn):
    try:
        fn(); checks[name] = "allowed"
    except BaseException:
        checks[name] = "blocked"
blocked("network", lambda: socket.create_connection(("1.1.1.1", 53), timeout=1))
blocked("dns", lambda: socket.getaddrinfo("example.com", 443))
blocked("write_outside", lambda: open(os.path.expanduser("~/.jaynes_sandbox_selftest"), "w"))
blocked("read_home", lambda: os.listdir(os.path.expanduser("~")))
blocked("spawn", lambda: os.fork())
print(checks)
'''


async def self_test(sandbox: Optional[Sandbox] = None) -> Optional[str]:
    """None when the sandbox works and every guarantee holds here; otherwise the reason it must not be used."""
    sandbox = sandbox or Sandbox()
    if not sandbox.supported():
        return "no supported sandbox on this system (needs macOS sandbox-exec)"
    try:
        outcome = await sandbox.execute(SELF_TEST, wall_seconds=10)
    except Exception as error:
        return f"the sandbox could not be started ({type(error).__name__})"
    if not outcome["have_result"]:
        return "the sandbox did not run Python (" + (outcome["launcher_stderr"].strip()[:120] or f"exit {outcome['returncode']}") + ")"
    try:
        checks = eval(outcome["stdout"].strip(), {"__builtins__": {}})
    except Exception:
        return "the sandbox self-test gave an unreadable answer"
    failed = sorted(name for name, state in checks.items() if state != "blocked")
    return ("the sandbox does not block: " + ", ".join(failed)) if failed else None


class SandboxArgs(BaseModel):
    code: str = Field(min_length=1, max_length=4000, description="Python code to run. Standard library only; no network, no files outside a scratch directory. Use print() for output; the value of the last expression is also returned.")


class PythonSandbox(Tool):
    name = "python_sandbox"
    description = ("Run a short piece of Python (standard library only) in an isolated process with no network and no access to the user's files, and get its stdout, stderr and the value "
                   "of its last expression. Use it for calculations and for checking code rather than guessing. Limits: 5 seconds of CPU, small memory, 10,000 characters of output.")
    args_model = SandboxArgs
    timeout_seconds = WALL_SECONDS + 4
    max_output_chars = 24_000

    def __init__(self, sandbox: Optional[Sandbox] = None) -> None:
        self._sandbox = sandbox or Sandbox()

    async def run(self, args: SandboxArgs) -> ToolResult:
        outcome = await self._sandbox.execute(args.code)
        if outcome["timed_out"]:
            return ToolResult.failure(f"the code did not finish within {WALL_SECONDS} seconds and was stopped", chars=len(args.code))
        if outcome["memory_killed"]:
            return ToolResult.failure(f"the code used more than {MEMORY_LIMIT_MB} MB of memory and was stopped", chars=len(args.code))
        if not outcome["have_result"]:
            reason = "was stopped for using too much CPU time" if outcome["returncode"] in (-9, -24, -6, 137, 152) else "ended without a result"
            return ToolResult.failure(f"the code {reason}", chars=len(args.code))
        parts = []
        if outcome["stdout"]:
            parts.append("stdout:\n" + outcome["stdout"])
        if outcome["stderr"]:
            parts.append("stderr:\n" + outcome["stderr"])
        if outcome["result"] is not None:
            parts.append("result: " + outcome["result"])
        failed = bool(outcome["stderr"]) and outcome["result"] is None
        return ToolResult(ok=True, content="\n".join(parts) or "(no output)", truncated="[output truncated]" in (outcome["stdout"] + outcome["stderr"]),
                          summary="ran the code" + (" (it raised an error)" if failed else ""), meta={"chars": len(args.code)})
