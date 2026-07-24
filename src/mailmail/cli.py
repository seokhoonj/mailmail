"""The command line: turn `argv` into one package call, and a result into text.

This module authors no mail logic. Every fact a send depends on -- which file
types a provider blocks, how a MIME message is assembled, what an alias resolves
to, where a password is kept -- already lives in the package, and the CLI only
reaches for it. It parses arguments, calls `send` / `send_bulk` /
`load_config` / `store_password`, prints what came back, and turns the package's
exceptions into an exit code. Anything it computed itself would be a second home
for a fact the package already owns, and the two always drift.

`main(argv)` returns the process exit code for a dispatched subcommand rather
than calling `sys.exit`, so a test can drive one and read the code back. The
argparse-owned options -- `--version`, `--help`, and a usage error -- still exit
the way argparse does, by raising `SystemExit` before any subcommand runs.
`python -m mailmail` and the `mailmail` console script both enter here.
"""

import argparse
import csv
import getpass
import smtplib
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from mailmail import (
    STARTER_CONFIG,
    Mail,
    MailmailError,
    SendReceipt,
    __version__,
    default_config_path,
    default_credentials_path,
    load_config,
    resolve_recipients,
    send,
    send_bulk,
    store_password,
)

__all__ = ["main"]

# send-bulk CSV columns. A row is one message; the list-valued cells hold several
# entries separated by LIST_CELL_DELIMITER, chosen as ';' because ',' is the
# field separator CSV already spends.
REQUIRED_CSV_COLUMNS = ("to", "subject", "body")
LIST_CELL_DELIMITER = ";"


def main(argv: Sequence[str] | None = None) -> int:
    """Parse a command line and carry out the one command it names.

    Parameters
    ----------
    argv
        The arguments after the program name. `None` reads `sys.argv[1:]`, so a
        real invocation needs to pass nothing; a test passes a list.

    Returns
    -------
    int
        The exit code for a dispatched subcommand: 0 when it fully succeeded, 1
        for an error the package raised or a send the server did not take in
        full. A usage error exits 2, and `--version`/`--help` exit 0, the way
        argparse does -- by raising `SystemExit` before dispatch, not by
        returning here.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    # `handler` is set by each subparser's set_defaults; typed here because a
    # Namespace attribute is Any, and the exit code must stay an int.
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except (
        MailmailError,
        smtplib.SMTPException,
        OSError,
        UnicodeDecodeError,
        csv.Error,
    ) as err:
        # The package's own messages are already complete sentences -- relay them
        # verbatim rather than a traceback, which is noise to someone running a
        # command. The last two families are the CLI's own boundary: a body or
        # config file that is not UTF-8 (a cp949 note is easy to reach for here)
        # raises UnicodeDecodeError, and a malformed send-bulk file raises
        # csv.Error -- both ordinary bad input, not the unexpected bug that
        # propagation is reserved for. A message from anywhere else does propagate.
        print(str(err), file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog        = "mailmail",
        description = "Send mail through Gmail or Naver SMTP.",
    )
    parser.add_argument(
        "--version", action="version", version=f"mailmail {__version__}"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    send_parser = subcommands.add_parser("send", help="send one message")
    send_parser.add_argument("--to", action="append", metavar="ADDR",
                             help="a recipient address or alias; repeat for several")
    send_parser.add_argument("--cc", action="append", metavar="ADDR", help="copy")
    send_parser.add_argument("--bcc", action="append", metavar="ADDR",
                             help="blind copy")
    send_parser.add_argument("--subject", required=True, help="the subject line")
    body_source = send_parser.add_mutually_exclusive_group()
    body_source.add_argument("--body", help="the plain-text body")
    body_source.add_argument("--body-file", type=Path, metavar="PATH",
                             help="read the body from a file instead of --body")
    send_parser.add_argument("--html-file", type=Path, metavar="PATH",
                             help="an HTML alternative to the plain-text body")
    send_parser.add_argument("--attach", action="append", type=Path, metavar="PATH",
                             help="a file to attach; repeat for several")
    send_parser.add_argument("--account", metavar="NAME",
                             help="which configured mailbox to send as")
    send_parser.add_argument("--config", type=Path, metavar="PATH",
                             help="a configuration file other than the default")
    send_parser.set_defaults(handler=_send_cmd)

    send_bulk_parser = subcommands.add_parser(
        "send-bulk", help="send one message per row of a CSV file"
    )
    send_bulk_parser.add_argument("csv", type=Path, metavar="CSV",
                                  help="a CSV whose columns are the message fields")
    send_bulk_parser.add_argument("--account", metavar="NAME",
                                  help="which configured mailbox to send as")
    send_bulk_parser.add_argument("--config", type=Path, metavar="PATH",
                                  help="a configuration file other than the default")
    send_bulk_parser.set_defaults(handler=_send_bulk_cmd)

    contacts_parser = subcommands.add_parser(
        "contacts", help="list the accounts and address-book aliases in the config"
    )
    contacts_parser.add_argument("--config", type=Path, metavar="PATH",
                                 help="a configuration file other than the default")
    contacts_parser.set_defaults(handler=_contacts_cmd)

    setup_parser = subcommands.add_parser(
        "setup", help="show where the config and credentials go, and a template"
    )
    setup_parser.set_defaults(handler=_setup_cmd)

    set_password_parser = subcommands.add_parser(
        "set-password", help="store an account's app password (prompted, not typed)"
    )
    set_password_parser.add_argument("--account", metavar="NAME",
                                     help="which mailbox the password is for")
    set_password_parser.add_argument("--config", type=Path, metavar="PATH",
                                     help="a configuration file other than the default")
    set_password_parser.set_defaults(handler=_set_password_cmd)

    return parser


def _send_cmd(args: argparse.Namespace) -> int:
    receipt = send(
        to          = _as_addresses(args.to),
        cc          = _as_addresses(args.cc),
        bcc         = _as_addresses(args.bcc),
        subject     = args.subject,
        body        = _read_body(args),
        html        = _read_optional_text(args.html_file),
        attachments = tuple(args.attach or ()),
        account     = args.account,
        config      = load_config(args.config),
    )
    return 0 if _report_receipt(receipt) else 1


def _send_bulk_cmd(args: argparse.Namespace) -> int:
    mails = _read_mails(args.csv)
    receipts = send_bulk(mails, account=args.account, config=load_config(args.config))
    delivered_in_full = True
    for index, receipt in enumerate(receipts):
        if not _report_receipt(receipt, label=f"[row {index + 1}]"):
            delivered_in_full = False
    return 0 if delivered_in_full else 1


def _contacts_cmd(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print("Accounts:")
    for name, account in sorted(config.account_by_name.items()):
        default_marker = "  (default)" if name == config.default_account else ""
        print(f"  {name}  {account.username}{default_marker}")
    if not config.address_book:
        print("Contacts: (none)")
        return 0
    print("Contacts:")
    for alias in sorted(config.address_book):
        addresses = resolve_recipients([alias], address_book=config.address_book)
        print(f"  {alias}  ->  {', '.join(addresses)}")
    return 0


def _setup_cmd(args: argparse.Namespace) -> int:
    config_path = default_config_path()
    credentials_path = default_credentials_path()
    print(f"config file:      {config_path}  {_existence_note(config_path)}")
    print(f"credentials file: {credentials_path}  {_existence_note(credentials_path)}")
    print()
    print("Create the config at the path above with, for example:\n")
    print(STARTER_CONFIG)
    print("Then store each account's app password with:")
    print("  mailmail set-password --account naver")
    return 0


def _set_password_cmd(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    account = config.resolve_account(args.account)
    # Prompted, never taken as an argument: a password on the command line lands
    # in the shell history and in `ps`.
    password = getpass.getpass(f"{account.username} app password: ")
    store_password(account, password)
    print(f"stored the app password for {account.username}")
    return 0


def _as_addresses(values: list[str] | None) -> tuple[str, ...]:
    """The recipients an --to/--cc/--bcc flag collected, or () when it was absent.

    argparse's `action="append"` leaves the attribute `None` until the flag
    appears, and `send` wants the empty case spelled as a tuple, not `None`.
    """
    return tuple(values or ())


def _read_body(args: argparse.Namespace) -> str:
    """The message body: from --body, from --body-file, or from stdin.

    The two flags are a mutually exclusive group at the argument layer, so at
    most one is set here. With neither the body is read from standard input, so
    `mailmail send ... < note.txt` works and a body with newlines never has to
    survive shell quoting.
    """
    body: str | None = args.body
    body_file: Path | None = args.body_file
    if body is not None:
        return body
    if body_file is not None:
        return body_file.read_text(encoding="utf-8")
    return sys.stdin.read()


def _read_optional_text(path: Path | None) -> str | None:
    """A file's text, or None when no path was given (as for --html-file)."""
    if path is None:
        return None
    return path.read_text(encoding="utf-8")


def _existence_note(path: Path) -> str:
    return "(exists)" if path.exists() else "(not created yet)"


def _read_mails(path: Path) -> list[Mail]:
    """Read a send-bulk CSV into one `Mail` per row.

    The header names the fields: `to`, `subject`, and `body` are required, and
    `cc`, `bcc`, `html`, `attachments` are optional. The list-valued columns
    (`to`, `cc`, `bcc`, `attachments`) hold several entries separated by ';';
    the text columns are taken whole, so a quoted body may contain commas and
    newlines. Nothing here is validated beyond the columns -- an unknown alias
    or a blank subject is caught when `send_bulk` composes the row, the one place
    that check lives.

    Raises
    ------
    csv.Error
        The file has no header, or the header is missing a required column.
        Raised as `csv.Error` so it flows through `main`'s one error funnel to a
        message and exit 1, the way every other bad-input path does -- not out
        the side as a `SystemExit` that skips it.
    """
    # utf-8-sig, not utf-8: a spreadsheet that exports "UTF-8 CSV" writes a BOM,
    # and plain utf-8 would fold it into the first header so `to` reads as
    # `﻿to` and the required-column check rejects a file that has the column.
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise csv.Error(f"{path} is empty; it needs a header row")
        missing = [column for column in REQUIRED_CSV_COLUMNS
                   if column not in reader.fieldnames]
        if missing:
            raise csv.Error(
                f"{path} is missing the required column(s) {', '.join(missing)}; "
                f"the header needs {', '.join(REQUIRED_CSV_COLUMNS)}"
            )
        return [_mail_from_row(row) for row in reader]


def _mail_from_row(row: dict[str, str | None]) -> Mail:
    # `row` is one DictReader row. Only the named string columns are read here;
    # a row with more fields than the header parks the surplus under a None key,
    # which the annotation ignores because nothing below asks for it.
    #
    # subject and body are taken whole, the way send takes them -- the CLI
    # does not strip them, so the CSV path and the flag path send identical
    # content. An empty html cell becomes None, not "": an empty string would
    # add a blank HTML alternative that clients render in place of the body.
    return Mail(
        subject     = row.get("subject") or "",
        body        = row.get("body") or "",
        to          = _split_list_cell(row.get("to")),
        cc          = _split_list_cell(row.get("cc")),
        bcc         = _split_list_cell(row.get("bcc")),
        html        = row.get("html") or None,
        attachments = _split_list_cell(row.get("attachments")),
    )


def _split_list_cell(cell: str | None) -> tuple[str, ...]:
    """The entries in a list-valued CSV cell, split on ';' and trimmed.

    An empty or missing cell is no entries. Blank pieces (a trailing ';', or
    ';;') are dropped rather than passed on as empty recipients.
    """
    if not cell:
        return ()
    return tuple(piece.strip() for piece in cell.split(LIST_CELL_DELIMITER)
                 if piece.strip())


def _report_receipt(receipt: SendReceipt, *, label: str = "") -> bool:
    """Print what the server did with one message; say whether it took all of it.

    Always prints exactly one summary line to stdout, so a batch that mixes
    accepted and wholly-refused rows stays one line per row -- a row the server
    took for nobody is `refused by every recipient`, not a silent gap that leaves
    the next row's `label` dangling on the line above it.

    Parameters
    ----------
    label
        A prefix such as `[row 3]` for `send-bulk`; empty for a single send.

    Returns
    -------
    bool
        `receipt.is_complete` -- True when every recipient was accepted. The
        caller turns this into the exit code, so a partial refusal, which
        `send` reports rather than raises, still surfaces as a failure to a
        script.
    """
    prefix = f"{label} " if label else ""
    if receipt.accepted:
        print(f"{prefix}sent to {', '.join(receipt.accepted)}  "
              f"(id {receipt.message_id})")
    else:
        print(f"{prefix}refused by every recipient")
    for address, reason in sorted(receipt.reason_by_refused_recipient.items()):
        print(f"refused {address}: {reason}", file=sys.stderr)
    return receipt.is_complete
