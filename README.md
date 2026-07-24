# mailmail

[![check](https://github.com/seokhoonj/mailmail/actions/workflows/check.yml/badge.svg)](https://github.com/seokhoonj/mailmail/actions/workflows/check.yml)
[![PyPI](https://img.shields.io/pypi/v/mailmail)](https://pypi.org/project/mailmail/)
[![Python](https://img.shields.io/pypi/pyversions/mailmail)](https://pypi.org/project/mailmail/)
[![License](https://img.shields.io/pypi/l/mailmail)](https://github.com/seokhoonj/mailmail/blob/main/LICENSE)

Send mail from Python through Gmail or Naver SMTP — one message, or a personalised
batch to a whole list — with the provider's own rules checked before anything
leaves your machine: blocked file types, the real size limit, the app-password
login both services now demand. Attachments, an HTML body, cc/bcc, and an address
book so you can call the people you write often by name.

**English** | [한국어](README.ko.md)

---

- [Quick start](#quick-start)
- [What you need](#what-you-need)
- [1. Install](#1-install)
- [2. Configure your accounts](#2-configure-your-accounts) (`~/.config/mailmail/config.toml`)
- [3. Get an app password](#3-get-an-app-password) — Naver, Gmail
- [4. Send mail](#4-send-mail)
- [Many at once (mail merge)](#many-at-once-mail-merge) (`send_bulk`)
- [From the command line](#from-the-command-line) (`mailmail`)
- [Files you can't send](#files-you-cant-send)
- [Use it from Claude Code](#use-it-from-claude-code)
- [When something goes wrong](#when-something-goes-wrong)
- [Development](#development)

### Quick start

```python
from mailmail import send

send(
    to          = "someone@example.com",
    subject     = "Weekly report",
    body        = "Please find the report attached.",
    attachments = ["report.xlsx"],
)
```

Using Claude Code, you can send mail by asking, without writing any Python →
[Use it from Claude Code](#use-it-from-claude-code)

Runs on Windows, macOS, and Linux. Installing it installs this package and nothing
else — it pulls in no other libraries.

### What you need

- **Python 3.11 or newer.** Check with `python --version` in a terminal (on Windows
  it may be `py --version`). If it is missing or older, get it from
  [python.org](https://www.python.org/downloads/).
- **A Naver or Gmail account.** Gmail is open to anyone, anywhere. A Naver account
  is easiest to create with a Korean mobile number — which foreign residents get
  with an Alien Registration Card (ARC). Without a Korean number, Naver also lets
  you verify with a passport or government-issued ID, which takes a day or two.
  These requirements change and vary by country, so check Naver's signup page; if
  you don't have a Naver account, Gmail works everywhere.
- **That account's app password.** You get it in step 3. **Your normal login
  password will not work** — both services reject it over SMTP.

### 1. Install

```sh
pip install mailmail
```

Check it landed:

```sh
python -c "import mailmail; print(mailmail.__version__)"
```

### 2. Configure your accounts

Create a `config.toml` under `.config/mailmail/` in your home folder. The full path:

| | Path |
|---|---|
| macOS · Linux | `~/.config/mailmail/config.toml` |
| Windows | `C:\Users\<name>\.config\mailmail\config.toml` |

Make the folder if it isn't there. Put your own addresses in:

```toml
default_account = "naver"

[accounts.naver]
provider = "naver"
username = "you@naver.com"

[accounts.gmail]
provider = "gmail"
username = "you@gmail.com"

[contacts]
me   = "you@naver.com"
lead = "lead@example.com"
team = ["me", "lead"]
```

- `default_account` — which account to send as when you don't pick one.
- `[accounts.*]` — the accounts you'll use. One is enough. `provider` is `naver` or
  `gmail`.
- `[contacts]` — an address book, optional. A group (`team`) sends to everyone in it
  at once, and a group may contain other names.

**The password does not go in this file.** You store it separately in step 3.

### 3. Get an app password

**This is where people get stuck most.** Both services reject your normal login
password and make you generate a separate app password. The two formats are near
opposites, which is easy to confuse.

| | Length | Form | Two-step verification |
|---|---|---|---|
| **Naver** | **12 characters** | **UPPERCASE + digits** | required |
| **Gmail** | **16 characters** | **lowercase** | required |

#### Naver

Since 2025-06-24, connecting a mail program requires two-step verification and an
app password. If a setup that used to work suddenly stopped, this is why.

1. Turn on **Naver ID → Security → Two-step verification.** Without it, the next
   menu does not appear at all.
2. On the same screen, **Application password → Generate.**
   - **The "type" you pick is just a label.** Outlook, iPhone, Gmail — whichever you
     choose, the password is the same. Typing `mailmail` in the free-text field
     makes it easy to recognise later.
   - A 12-character uppercase-and-digit password appears. **You cannot see it again
     once you leave that screen** — copy it.
3. Under **Mail → Settings → POP3/IMAP**, confirm **SMTP is enabled.** Even if it
   already is, toggle it once: **Off → Save → On → Save.** That is what applies the
   June 2025 policy change to an older account.

#### Gmail

1. **Turn on two-step verification first.** Without it, the app-password menu does
   not appear.
2. Generate one at
   [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
3. A 16-character lowercase password appears in four groups. The spaces don't
   matter — keep them or drop them.

#### Store the password you got

**Store it once and you are not asked again.** Running this prompts for it:

```sh
python -c "
from getpass import getpass
from mailmail import load_config, store_password

account = load_config().resolve_account('naver')                  # 'gmail' for Gmail
password = getpass(f'{account.username} app password: ').strip()
print(f'  length: {len(password)}')                               # Naver 12, Gmail 16
store_password(account, password)
print('  saved')
"
```

**Nothing showing on screen while you paste is normal.** So you can't tell if you
pasted twice — check the length it prints.

The password is stored, **readable only by you**, in `credentials.json` beside the
config — not in the config file. That file is not encrypted, so the only thing that
goes in it is an **app password**: it is used only to send mail, it leaves your
account password untouched, and you can revoke it any time. Do not put your account
password here.

#### Check it worked

You can test just the login, without sending anything:

```sh
python -c "
import smtplib
from mailmail import load_config, resolve_password

for name in ('naver',):  # ('naver', 'gmail') if you stored Gmail too
    account = load_config().resolve_account(name)
    smtp = smtplib.SMTP(account.provider.smtp_host, account.provider.smtp_port, timeout=20)
    smtp.ehlo(); smtp.starttls(); smtp.ehlo()
    try:
        smtp.login(account.username, resolve_password(account))
        print(f'  {name}: OK')
    except smtplib.SMTPAuthenticationError as e:
        print(f'  {name}: {e.smtp_code} {e.smtp_error.decode()[:60]}')
    smtp.quit()
"
```

`OK` means you're done. If not:

| What the server says | What it means |
|---|---|
| `535 5.7.1 Username and Password not accepted` (Naver) | Wrong password, or SMTP is off. **Check both** — this message does not tell them apart |
| `534 5.7.9 Application-specific password required` (Gmail) | You used the login password. Try the app password |

### 4. Send mail

```python
from mailmail import send

send(
    to      = "someone@example.com",
    subject = "Weekly report",
    body    = "Please find the report attached.",
)
```

If you registered a name in the address book, you can call it. You can mix names and
addresses.

```python
send(to="lead", subject="Weekly report", body="Please review.")
send(to=["lead", "someone@example.com"], subject="Weekly report", body="...")
send(to="team", subject="Weekly report", body="...")  # the group expands
```

The body goes out exactly as written, so line breaks survive. A triple-quoted
string is handy for a long one.

```python
body = """\
Hello,

This week's report is attached. The loss ratio moved 1.2%p from last month;
the detail is on the second sheet.

Best regards,
"""

send(to="lead", subject="Weekly report", body=body, attachments=["report.xlsx"])
```

With an account, attachments, cc, and HTML:

```python
receipt = send(
    account     = "gmail",  # default_account if omitted
    to          = "lead",
    cc          = "team",
    bcc         = "audit@example.com",
    subject     = "Month-end close",
    body        = "Month-end figures attached.",
    html        = "<p>Month-end figures attached.</p>",
    attachments = ["close.xlsx", "notes.pdf"],
)

if not receipt.is_complete:  # when some addresses were refused
    print(receipt.reason_by_refused_recipient)
```

A few things to know:

- **Omit `cc` and there is no cc.** What you don't name doesn't ride along.
- **`bcc` is invisible to the recipients.** Blind recipients don't see each other
  either.
- **`html` and `body` should say the same thing.** A mail client shows one or the
  other, so if they differ, different people read different mail.
- **Korean (or any non-ASCII) just works.** Nothing special to do in the subject or
  the body.

### Many at once (mail merge)

To send the same note to many people with a different value or attachment for each,
use `send_bulk`. One `Mail` per person, and they all go out over **one connection** —
thirty people is one login, not thirty.

```python
from mailmail import Mail, send_bulk

receipts = send_bulk([
    Mail(to="alice@example.com", subject="June results",
         body="Hi Alice, please find yours attached.", attachments=["alice.xlsx"]),
    Mail(to="bob@example.com", subject="June results",
         body="Hi Bob, please find yours attached.", attachments=["bob.xlsx"]),
])

for receipt in receipts:
    if not receipt.is_complete:  # an address this message refused
        print(receipt.reason_by_refused_recipient)
```

`Mail` is the same vocabulary as `send` — `to`/`cc`/`bcc` take addresses or
address-book names, and `attachments` are paths. Personalise (name, figures,
attachment) by building each `Mail` differently.

A few things to know:

- **You get a list of receipts in the same order.** `zip(mails, receipts)` pairs who
  got what. If one message is refused for every recipient, that message comes back as
  a receipt with empty `accepted` and **the rest still go** — 3 of 30 blocked means
  27 sent.
- **Most bad rows are stopped before anything is sent.** An unknown address-book
  name, a blank subject, a blocked attachment, or attachments already over the limit,
  in any row, raises **before the connection opens** and nothing goes out.
- **Two failures can still land mid-batch:** (1) a message that crosses the size limit
  only once assembled (the up-front screen weighs attachments, not the finished MIME),
  and (2) the connection dropping partway through. In both, earlier messages have
  already gone and cannot be unsent, and the receipts collected so far are lost. What
  the server refuses **per recipient** goes into the receipt, not an exception.

### From the command line

Everything above works from a terminal too, no Python required — installing the package
puts a `mailmail` command on your `PATH`.

```sh
mailmail send --to lead --subject "Weekly report" --body "Please review."
```

`--to` (and `--cc`, `--bcc`) take an address or an address-book name, and repeat for
several; `--attach` repeats too. A body with line breaks reads better from a file or a
pipe than as one shell argument:

```sh
mailmail send --to team --subject "Month-end close" \
    --body-file note.txt --attach close.xlsx --attach notes.pdf --account gmail

mailmail send --to lead --subject "Weekly report" < note.txt
```

Send many at once from a CSV — one row per message, one login for the batch. The header
names the fields: `to`, `subject`, and `body` are required; `cc`, `bcc`, `html`, and
`attachments` are optional. A cell holding several entries (recipients, attachments)
separates them with a semicolon:

```sh
mailmail send-bulk batch.csv --account gmail
```

```csv
to,subject,body,attachments
alice@example.com,June results,"Hi Alice, yours is attached.",alice.xlsx
team,June summary,"All figures attached.",june.xlsx;notes.pdf
```

The other three commands set things up and check them:

```sh
mailmail setup                         # the config and credentials paths, and a template
mailmail contacts                      # the accounts and address-book names you can use
mailmail set-password --account naver  # store the app password, prompted
```

`set-password` asks for the password at a prompt instead of taking it as an argument, so
it never lands in your shell history. Anything a send would reject — a blocked
attachment, a message over the limit, a missing password — is reported the same way it
is from Python, before a connection opens. A partial refusal exits non-zero, so a script
can tell.

### Files you can't send

Attachments a mail service would reject are reported **before sending**, as an
exception. That beats learning it minutes later from a bounce (or from a message that
silently lands in a folder nobody reads).

- **Executables.** `.exe`, `.dll`, `.jar`, `.js`, `.bat`, `.vbs`, `.ps1`, `.msi`, and
  the like. **Putting them in a `.zip` or `.tar.gz` doesn't help** — those are looked
  inside. ([Gmail's published list](https://support.google.com/mail/answer/6590).
  Naver does not publish one, so the same standard is applied.)
- **A password-protected zip.** Rejected whatever is in it, because the service cannot
  open it.
- **Archives too big or too deeply nested.** Scanned up to 4 levels deep, and up to
  64 MB per inner file.

Share those through a link instead.

**Size limit** — an attachment grows about 37% on the way out, so the ceiling on the
raw files is about **25 MB for Gmail** and about **27 MB for Naver**. That can differ
from the number the web UI shows; this is the one the server actually accepts.

**`.7z` and `.rar` cannot be looked inside.** The Python standard library cannot read
those formats. If one holds an executable it passes the check, reaches the server, and
is refused there. Better not to use those two.

### Use it from Claude Code

Install the skill into [Claude Code](https://claude.com/claude-code) and you can send
mail **by asking**, without writing Python. A skill is an instruction sheet that tells
Claude "when a request like this comes in, do this."

The skill lives in this repository, so clone it first, then link its skill folder into
the place Claude looks:

```sh
git clone https://github.com/seokhoonj/mailmail.git
ln -s "$PWD/mailmail/skills/send" ~/.claude/skills/send  # macOS, Linux
```

```powershell
git clone https://github.com/seokhoonj/mailmail.git
New-Item -ItemType SymbolicLink -Path "$HOME\.claude\skills\send" `
         -Target "$PWD\mailmail\skills\send"  # Windows (PowerShell)
```

After that, just say it in Claude Code:

> Summarise June's P&L as a table, attach balance.xlsx, and send it to lead.

Point out **what goes in the body, what is an attachment, and who it's for**, and it
is assembled as told. What you don't specify, it asks about — it does not guess.

Before sending, it **shows the recipients, subject, and attachments for approval.**
Address-book names are expanded to real addresses, so you can see exactly who `team`
reaches. Mail cannot be unsent.

### When something goes wrong

The exception message states, in words, what is wrong and how to fix it.

| | Meaning |
|---|---|
| `ConfigError` | The config file is missing or malformed. Go to step 2 |
| `MissingPasswordError` | No password stored yet. Go to step 3 |
| `UnknownContactError` | A name that isn't in the address book. It lists the names it knows |
| `BlockedAttachmentError` | A file the mail service blocks. Share it as a link |
| `MessageTooLargeError` | The attachments are over the limit |
| `AuthenticationFailedError` | The login was refused. Check the app password is right, and for Naver that SMTP is on |

### Development

```sh
git clone https://github.com/seokhoonj/mailmail.git
cd mailmail
python -m venv .venv
source .venv/bin/activate        # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest        # sends no real mail; a fake SMTP server stands in
ruff check src tests scripts
mypy
```
