import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CHECK = runpy.run_path(str(ROOT / "bin/check-public-files"))
SECRET_CHECK = runpy.run_path(str(ROOT / "bin/check-secrets"))


@pytest.mark.parametrize(
    "name",
    [
        ".env.production",
        "backups/export.sql",
        "private/key.txt",
        "server.log",
        ".do/app.local.yaml",
    ],
)
def test_public_check_rejects_private_artifacts(name):
    assert PUBLIC_CHECK["problems"](name, b"private output")


def test_public_check_distinguishes_example_data_from_personal_data():
    check = PUBLIC_CHECK["problems"]
    assert not check(".env.example", b"OPENAI_API_KEY=\n")
    assert not check("tests/fixture.py", b"seller@example.com")
    assert not check("migrations/0001_users.sql", b"CREATE TABLE users (id integer);")
    address = "private-contact@" + "real-business.invalid"
    assert check("notes.md", address.encode()) == [(1, "non-example email address")]
    local_path = "/" + "Users/" + "example-person/private-project"
    assert check("notes.md", local_path.encode()) == [(1, "local user path")]


def test_scanner_reports_locations_without_raw_secrets(monkeypatch, capsys, tmp_path):
    secret = "private-credential-" + "for-regression-test"

    def fake_scan(command, **kwargs):
        assert "--no-verification" in command
        assert "--fail-on-scan-errors" in command
        kwargs["stdout"].write(
            json.dumps(
                {
                    "Raw": secret,
                    "DetectorName": "example-detector",
                    "SourceMetadata": {"Data": {"Git": {"file": "example.py", "line": 12}}},
                }
            ).encode()
            + b"\n"
        )
        kwargs["stderr"].write(secret.encode())
        return SimpleNamespace(returncode=183)

    monkeypatch.setattr(SECRET_CHECK["subprocess"], "run", fake_scan)
    assert not SECRET_CHECK["run_scan"](["git", "example"], cwd=tmp_path)
    output = capsys.readouterr()
    assert "example.py" in output.out
    assert secret not in output.out + output.err


@pytest.mark.parametrize("returncode,output", [(1, b"private diagnostic"), (0, b"invalid JSON")])
def test_scanner_failure_cannot_pass_or_print_raw_output(
    monkeypatch, capsys, tmp_path, returncode, output
):
    def fake_scan(_command, **kwargs):
        kwargs["stdout"].write(output)
        kwargs["stderr"].write(b"private scanner error")
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(SECRET_CHECK["subprocess"], "run", fake_scan)
    assert not SECRET_CHECK["run_scan"](["filesystem", "example"], cwd=tmp_path)
    captured = capsys.readouterr()
    assert "Secret scan failed" in captured.err
    assert output.decode() not in captured.out + captured.err
    assert "private scanner error" not in captured.out + captured.err
