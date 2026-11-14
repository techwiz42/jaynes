import asyncio
import os

import pytest

from bicameral.tools.base import ToolRegistry
from bicameral.tools.sandbox import PythonSandbox, Sandbox, build_profile, self_test

PROBLEM = asyncio.run(self_test()) if Sandbox.supported() else "no supported sandbox on this system (needs macOS sandbox-exec)"
needs_sandbox = pytest.mark.skipif(PROBLEM is not None, reason=f"python_sandbox tests skipped: {PROBLEM}")


def registry():
    return ToolRegistry([PythonSandbox()])


async def run(code):
    return await registry().run("python_sandbox", {"code": code})


def test_the_profile_confines_writes_and_reads_and_exec():
    profile = build_profile("/private/var/folders/x/T/jsbx-1", "/usr/local/py")
    assert "(deny network*)" in profile
    assert '(deny file-write* (require-not (subpath "/private/var/folders/x/T/jsbx-1")))' in profile
    assert "(deny file-read* (subpath \"/Users\")" in profile and "(deny process-fork)" in profile
    assert '(deny process-exec* (require-not (subpath "/usr/local/py")))' in profile
    assert profile.index("(deny file-read*") < profile.index("(allow file-read*")           # later rules win: allowances come after the denials


async def test_the_tool_validates_its_arguments_without_a_sandbox():
    reg = ToolRegistry([PythonSandbox(sandbox=Sandbox())])
    assert not (await reg.run("python_sandbox", {"code": ""})).ok
    assert not (await reg.run("python_sandbox", {"code": "x" * 4001})).ok


@needs_sandbox
async def test_it_runs_code_and_returns_stdout_and_the_last_expression():
    result = await run("x = 17 * 24\nprint('hi')\nx")
    assert result.ok and "stdout:\nhi" in result.content and "result: 408" in result.content


@needs_sandbox
async def test_an_exception_in_the_code_is_reported_not_raised():
    result = await run("1/0")
    assert result.ok and "ZeroDivisionError" in result.content and "raised an error" in result.summary


@needs_sandbox
async def test_the_network_is_blocked():
    result = await run("import socket\nprint(socket.create_connection(('1.1.1.1', 53), timeout=1))")
    assert "error" in result.content.lower() and "stdout" not in result.content
    result = await run("import socket\nsocket.getaddrinfo('example.com', 443)")
    assert "error" in result.content.lower()


@needs_sandbox
async def test_writes_outside_the_temp_directory_are_blocked_and_inside_it_work(tmp_path):
    target = tmp_path / "pwned.txt"
    result = await run(f"open({str(target)!r}, 'w').write('x')")
    assert "PermissionError" in result.content and not target.exists()
    assert "result: 1" in (await run("open('scratch.txt', 'w').write('x')")).content            # the working directory is the scratch directory


@needs_sandbox
async def test_files_in_the_home_directory_cannot_be_read():
    secret = os.path.expanduser("~/.zshrc")
    result = await run(f"print(open({secret!r}).read(10))")
    assert "PermissionError" in result.content
    assert "PermissionError" in (await run("import os\nprint(os.listdir(os.path.expanduser('~')))")).content


@needs_sandbox
async def test_the_environment_is_empty_and_other_programs_cannot_be_started():
    env = await run("import os\nprint(sorted(k for k in os.environ if not k.startswith(('LC_', '__CF'))))")
    assert "stdout:\n[]" in env.content
    assert "PermissionError" in (await run("import subprocess\nsubprocess.run(['/bin/ls', '/'])")).content


@needs_sandbox
async def test_the_bicameral_package_is_not_importable():
    assert "ModuleNotFoundError" in (await run("import bicameral")).content


@needs_sandbox
async def test_a_busy_loop_is_stopped_by_the_cpu_limit():
    result = await run("while True: pass")
    assert not result.ok and "stopped" in result.error


@needs_sandbox
async def test_a_memory_hog_is_stopped():
    result = await run("x = bytearray(900*1024*1024)\nfor i in range(0, len(x), 4096): x[i] = 1\nlen(x)")
    assert not result.ok and "memory" in result.error


@needs_sandbox
async def test_output_is_capped():
    result = await run("print('x' * 100000)")
    assert result.ok and result.truncated and len(result.content) < 11_000
