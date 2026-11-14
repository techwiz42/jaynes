import json
import os
import subprocess
import sys

import pytest

from bicameral.envfile import clean, merge_env, update_file
from bicameral.google_auth import GoogleAuthError, parse_redirect


def test_existing_names_are_replaced_in_place_and_new_ones_added_with_the_rest_kept():
    text = "# keys\nGEMINI_API_KEY=old\nANTHROPIC_API_KEY=keep\nexport OTHER=1\n"
    merged = merge_env(text, {"GEMINI_API_KEY": "new", "GOOGLE_REFRESH_TOKEN": "tok"})
    assert merged == "# keys\nGEMINI_API_KEY=new\nANTHROPIC_API_KEY=keep\nexport OTHER=1\nGOOGLE_REFRESH_TOKEN=tok\n"
    assert merge_env("", {"A_B": "1"}) == "A_B=1\n"
    assert merge_env("export X=1\n", {"X": "2"}) == "X=2\n"


def test_a_pasted_name_equals_prefix_is_removed_and_line_breaks_are_refused():
    assert clean("GEMINI_API_KEY", "GEMINI_JAYNES_API_KEY=AQ.abc") == "AQ.abc"
    assert clean("K", "  value  ") == "value" and clean("K", "a=b") == "a=b"
    with pytest.raises(ValueError):
        clean("K", "a\nB=c")
    with pytest.raises(ValueError):
        merge_env("", {"lower": "x"})


def test_the_file_is_written_with_mode_600_and_the_other_lines_survive(tmp_path):
    path = tmp_path / "cfg" / "env"
    path.parent.mkdir()
    path.write_text("A=1\n")
    update_file(str(path), {"B": "2"})
    assert path.read_text() == "A=1\nB=2\n" and oct(path.stat().st_mode & 0o777) == "0o600"
    update_file(str(tmp_path / "new" / "env"), {"C": "3"})
    assert oct((tmp_path / "new" / "env").stat().st_mode & 0o777) == "0o600"


def test_the_command_reads_json_from_standard_input_so_values_are_never_arguments(tmp_path):
    path = tmp_path / "env"
    done = subprocess.run([sys.executable, "-m", "bicameral.envfile"], input=json.dumps({"GOOGLE_REFRESH_TOKEN": "secret-xyz"}), text=True, capture_output=True,
                          env={**os.environ, "JAYNES_ENV_FILE": str(path)})
    assert done.returncode == 0 and done.stdout.strip() == "updated GOOGLE_REFRESH_TOKEN" and "secret-xyz" not in done.stdout + done.stderr
    assert path.read_text() == "GOOGLE_REFRESH_TOKEN=secret-xyz\n"
    bad = subprocess.run([sys.executable, "-m", "bicameral.envfile"], input='["not", "an", "object"]', text=True, capture_output=True, env={**os.environ, "JAYNES_ENV_FILE": str(path)})
    assert bad.returncode == 2


def test_the_redirect_is_checked_for_state_errors_and_a_code():
    assert parse_redirect("/?state=abc&code=4%2F0AX&scope=x", "abc") == "4/0AX"
    for path, words in [("/?state=other&code=c", "wrong state"), ("/?error=access_denied&state=abc", "access_denied"), ("/?state=abc", "no code"), ("/", "wrong state")]:
        with pytest.raises(GoogleAuthError, match=words):
            parse_redirect(path, "abc")
