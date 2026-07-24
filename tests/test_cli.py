"""The command line, driven end to end against the fake SMTP server.

`main(argv)` returns the exit code, so every test here reads that code back
directly. The `fake_smtp` fixture (conftest) stands in for the network and
redirects the credentials file into tmp; `configured` writes a config file and
stores the sending account's password, so a `send` has everything it needs
except a real server.
"""

import io
import subprocess
import sys

import pytest

from mailmail import SmtpAccount, store_password
from mailmail.cli import main
from mailmail.provider import NAVER

ACCOUNT = SmtpAccount(name="naver", username="me@example.com", provider=NAVER)

CONFIG_TOML = """\
default_account = "naver"

[accounts.naver]
provider = "naver"
username = "me@example.com"

[accounts.gmail]
provider = "gmail"
username = "me@gmail.com"

[contacts]
me       = "me@example.com"
lead     = "lead@example.com"
reviewer = "reviewer@example.com"
team     = ["lead", "reviewer"]
"""


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """A config file on disk, found through MAILMAIL_CONFIG."""
    path = tmp_path / "config.toml"
    path.write_text(CONFIG_TOML, encoding="utf-8")
    monkeypatch.setenv("MAILMAIL_CONFIG", str(path))
    return path


@pytest.fixture
def configured(config_file, fake_smtp):
    """A config on disk and the naver account's password stored.

    Depends on `fake_smtp` for the credentials redirect, so the password lands
    in tmp rather than the operator's real store.
    """
    store_password(ACCOUNT, "app-pw")
    return fake_smtp


class TestSend:
    def test_a_named_alias_is_resolved_and_delivered(self, configured, capsys):
        code = main(["send", "--to", "lead", "--subject", "s", "--body", "b"])
        assert code == 0
        assert configured.sent_messages[0].recipients == ["lead@example.com"]
        assert "sent to lead@example.com" in capsys.readouterr().out

    def test_a_group_alias_expands(self, configured):
        main(["send", "--to", "team", "--subject", "s", "--body", "b"])
        assert configured.sent_messages[0].recipients == [
            "lead@example.com",
            "reviewer@example.com",
        ]

    def test_several_to_flags_accumulate(self, configured):
        main(["send", "--to", "lead", "--to", "reviewer",
              "--subject", "s", "--body", "b"])
        assert configured.sent_messages[0].recipients == [
            "lead@example.com",
            "reviewer@example.com",
        ]

    def test_the_body_can_come_from_a_file(self, configured, tmp_path):
        body_file = tmp_path / "note.txt"
        body_file.write_text("from a file\nwith a newline\n", encoding="utf-8")
        main(["send", "--to", "me", "--subject", "s", "--body-file", str(body_file)])
        assert b"from a file" in configured.sent_messages[0].payload

    def test_the_body_can_come_from_stdin(self, configured, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO("body from stdin"))
        main(["send", "--to", "me", "--subject", "s"])
        assert b"body from stdin" in configured.sent_messages[0].payload

    def test_body_and_body_file_are_mutually_exclusive(self, configured, tmp_path):
        body_file = tmp_path / "note.txt"
        body_file.write_text("x", encoding="utf-8")
        with pytest.raises(SystemExit) as exit_info:
            main(["send", "--to", "me", "--subject", "s",
                  "--body", "b", "--body-file", str(body_file)])
        assert exit_info.value.code == 2
        assert configured.sent_messages == []

    def test_an_attachment_is_carried(self, configured, tmp_path):
        report = tmp_path / "report.txt"
        report.write_text("numbers", encoding="utf-8")
        code = main(["send", "--to", "me", "--subject", "s", "--body", "b",
                     "--attach", str(report)])
        assert code == 0
        assert b"report.txt" in configured.sent_messages[0].payload

    def test_an_html_file_becomes_the_alternative_part(self, configured, tmp_path):
        html = tmp_path / "body.html"
        html.write_text("<p>the figures</p>", encoding="utf-8")
        code = main(["send", "--to", "me", "--subject", "s", "--body", "plain",
                     "--html-file", str(html)])
        assert code == 0
        payload = configured.sent_messages[0].payload
        assert b"<p>the figures</p>" in payload
        assert b"text/html" in payload

    def test_a_successful_send_returns_integer_zero_not_true(self, configured):
        # Locks the fixed bug where _report_receipt's bool was returned as the
        # exit code: `True == 0` is False, so a success wrongly read as failure.
        code = main(["send", "--to", "me", "--subject", "s", "--body", "b"])
        assert code == 0
        assert type(code) is int


class TestSendReportsFailureWithoutRaising:
    def test_a_missing_password_exits_one_and_prints_the_reason(
        self, config_file, fake_smtp, capsys
    ):
        # No password stored: send_mail raises MissingPasswordError, which the
        # CLI relays to stderr rather than letting it become a traceback.
        code = main(["send", "--to", "me", "--subject", "s", "--body", "b"])
        assert code == 1
        assert "no password stored" in capsys.readouterr().err

    def test_a_blocked_attachment_exits_one(self, configured, tmp_path, capsys):
        program = tmp_path / "installer.exe"
        program.write_bytes(b"MZ")
        code = main(["send", "--to", "me", "--subject", "s", "--body", "b",
                     "--attach", str(program)])
        assert code == 1
        assert configured.sent_messages == []
        assert ".exe" in capsys.readouterr().err

    def test_an_unknown_account_exits_one(self, configured, capsys):
        code = main(["send", "--to", "me", "--subject", "s", "--body", "b",
                     "--account", "outlook"])
        assert code == 1
        assert "outlook" in capsys.readouterr().err

    def test_a_non_utf8_body_file_exits_one_without_a_traceback(
        self, configured, tmp_path, capsys
    ):
        # A cp949 note is easy to reach for in this workflow; decoding it as
        # UTF-8 raises UnicodeDecodeError, which must land as a message + exit 1,
        # not a traceback.
        body_file = tmp_path / "note.txt"
        body_file.write_bytes("보고서".encode("cp949"))
        code = main(["send", "--to", "me", "--subject", "s",
                     "--body-file", str(body_file)])
        assert code == 1
        assert configured.sent_messages == []

    def test_a_partial_refusal_exits_one_and_reports_both_sides(
        self, configured, capsys
    ):
        configured.refusals = {"reviewer@example.com": (550, b"no such user")}
        code = main(["send", "--to", "team", "--subject", "s", "--body", "b"])
        assert code == 1
        streams = capsys.readouterr()
        assert "sent to lead@example.com" in streams.out
        assert "refused reviewer@example.com" in streams.err


class TestSendBulk:
    def _write_csv(self, path, content, *, encoding="utf-8"):
        path.write_text(content, encoding=encoding)
        return path

    def test_one_message_per_row(self, configured, tmp_path):
        csv_path = self._write_csv(
            tmp_path / "batch.csv",
            "to,subject,body\nlead,First,hello\nreviewer,Second,hi\n",
        )
        code = main(["send-bulk", str(csv_path)])
        assert code == 0
        assert [message.recipients for message in configured.sent_messages] == [
            ["lead@example.com"],
            ["reviewer@example.com"],
        ]

    def test_a_list_cell_splits_on_semicolons(self, configured, tmp_path):
        csv_path = self._write_csv(
            tmp_path / "batch.csv",
            "to,subject,body\nlead;reviewer,Weekly,hello\n",
        )
        main(["send-bulk", str(csv_path)])
        assert configured.sent_messages[0].recipients == [
            "lead@example.com",
            "reviewer@example.com",
        ]

    def test_a_trailing_semicolon_in_a_list_cell_adds_no_empty_recipient(
        self, configured, tmp_path
    ):
        csv_path = self._write_csv(
            tmp_path / "batch.csv", "to,subject,body\nlead;,Weekly,hello\n"
        )
        code = main(["send-bulk", str(csv_path)])
        assert code == 0
        assert configured.sent_messages[0].recipients == ["lead@example.com"]

    def test_a_quoted_body_keeps_its_commas_and_newlines(self, configured, tmp_path):
        csv_path = self._write_csv(
            tmp_path / "batch.csv",
            'to,subject,body\nlead,Report,"line one, still one\nline two"\n',
        )
        main(["send-bulk", str(csv_path)])
        payload = configured.sent_messages[0].payload
        assert b"line one, still one" in payload
        assert b"line two" in payload

    def test_a_header_only_file_sends_nothing(self, configured, tmp_path):
        csv_path = self._write_csv(tmp_path / "batch.csv", "to,subject,body\n")
        code = main(["send-bulk", str(csv_path)])
        assert code == 0
        assert configured.sent_messages == []

    def test_a_utf8_bom_header_is_accepted(self, configured, tmp_path):
        # A spreadsheet exporting "UTF-8 CSV" writes a byte-order mark; the reader
        # must not fold it into the first column name and then reject `to`.
        csv_path = self._write_csv(
            tmp_path / "batch.csv",
            "to,subject,body\nlead,First,hello\n",
            encoding="utf-8-sig",
        )
        code = main(["send-bulk", str(csv_path)])
        assert code == 0
        assert configured.sent_messages[0].recipients == ["lead@example.com"]

    def test_a_row_refused_for_every_recipient_exits_one_and_keeps_the_rest(
        self, configured, tmp_path, capsys
    ):
        configured.refusals = {"reviewer@example.com": (550, b"no such user")}
        csv_path = self._write_csv(
            tmp_path / "batch.csv",
            "to,subject,body\nlead,First,hello\nreviewer,Second,hi\n",
        )
        code = main(["send-bulk", str(csv_path)])
        assert code == 1
        # The accepted row still went; the refused row did not sink it.
        assert [m.recipients for m in configured.sent_messages] == [
            ["lead@example.com"]
        ]
        streams = capsys.readouterr()
        assert "[row 1] sent to lead@example.com" in streams.out
        assert "[row 2] refused by every recipient" in streams.out
        assert "refused reviewer@example.com" in streams.err

    def test_a_blank_recipient_row_exits_one_before_sending(
        self, configured, tmp_path, capsys
    ):
        csv_path = self._write_csv(
            tmp_path / "batch.csv", "to,subject,body\n,Subject,hello\n"
        )
        code = main(["send-bulk", str(csv_path)])
        assert code == 1
        assert configured.sent_messages == []
        assert "recipient" in capsys.readouterr().err

    def test_an_unknown_alias_row_exits_one_before_sending(
        self, configured, tmp_path, capsys
    ):
        csv_path = self._write_csv(
            tmp_path / "batch.csv", "to,subject,body\nnobody,Subject,hello\n"
        )
        code = main(["send-bulk", str(csv_path)])
        assert code == 1
        assert configured.sent_messages == []
        assert "nobody" in capsys.readouterr().err

    def test_a_missing_required_column_exits_one_before_sending(
        self, configured, tmp_path, capsys
    ):
        csv_path = self._write_csv(tmp_path / "batch.csv", "to,body\nlead,hello\n")
        code = main(["send-bulk", str(csv_path)])
        assert code == 1
        assert configured.sent_messages == []
        assert "subject" in capsys.readouterr().err


class TestContacts:
    def test_lists_accounts_with_the_default_marked_and_expands_aliases(
        self, config_file, capsys
    ):
        code = main(["contacts"])
        assert code == 0
        out = capsys.readouterr().out
        assert "naver  me@example.com  (default)" in out
        assert "team  ->  lead@example.com, reviewer@example.com" in out


class TestSetup:
    def test_prints_the_paths_and_a_starter_template(self, capsys):
        code = main(["setup"])
        assert code == 0
        out = capsys.readouterr().out
        assert "config file:" in out
        assert 'default_account = "naver"' in out


class TestSetPassword:
    def test_prompts_and_stores_without_the_password_on_the_command_line(
        self, config_file, fake_smtp, monkeypatch, capsys
    ):
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "typed-app-pw")
        code = main(["set-password", "--account", "naver"])
        assert code == 0
        assert "stored the app password for me@example.com" in capsys.readouterr().out
        # The stored password is now the one a send would use.
        from mailmail import resolve_password

        assert resolve_password(ACCOUNT) == "typed-app-pw"


class TestUsage:
    def test_no_subcommand_is_a_usage_error(self):
        with pytest.raises(SystemExit) as exit_info:
            main([])
        assert exit_info.value.code == 2

    def test_python_m_mailmail_wires_the_entry_point(self):
        # Exercises __main__.py end to end: `python -m mailmail` with no
        # subcommand must reach argparse and exit 2, proving the console entry is
        # wired, not just main() called directly by the other tests.
        result = subprocess.run(
            [sys.executable, "-m", "mailmail"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "usage: mailmail" in result.stderr
