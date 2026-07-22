---
name: send-mail
description: "Send a result, report, or file from the conversation as email. Holds no logic of its own -- it calls the mailmail package's send_mail(), and always shows the recipients, subject, and attachments for approval before sending. Trigger phrases: send mail, email this, mail this, send this by email, email the report, 메일 보내줘, 메일로 보내, 메일 발송, 이거 메일로, 부장님께 보내줘, 상무님께 보내줘."
---

# send-mail — send a conversation result as email

Sending mail is an **irreversible external transmission**. A wrong mail cannot be
recalled, and an attachment sent to the wrong person stays there forever. So half
of this skill is not the sending — it is the confirmation before it.

## What this skill does not do

It holds no logic. MIME assembly, the blocked-extension check, size calculation,
alias resolution — **the mailmail package does all of it.** Do not reimplement any
of that here, do not copy the blocked list into this file, do not check extensions
yourself. That would put one fact in two homes, and the two always drift. This
skill only assembles one `send_mail(...)` call and runs it.

## Where to find the package

**Do not hardcode a path.** People clone the repository to different places. This
skill is symlinked out of the repository, so the repository is **two levels above**
this skill's folder. Find it once the first time it's needed in a session, then
reuse that value.

```sh
python3 -c "
import os, pathlib
skill = pathlib.Path(os.path.realpath(os.path.expanduser('~/.claude/skills/send-mail')))
venv  = skill.parents[1] / '.venv' / ('Scripts' if os.name == 'nt' else 'bin') / 'python'
print(venv if venv.exists() else 'NOT FOUND')
"
```

If it prints `NOT FOUND`, do not invent a path — **ask the user where the
repository is.** The skill was copied rather than symlinked, or the venv has not
been created yet (README step 1).

The examples below write the resolved path as `$MAILMAIL_PY`.

## Procedure

### 1. Work out what to send

Pull these from the user's request. If anything is missing, **do not guess — ask.**

- `to` / `cc` / `bcc` — addresses or aliases
- `subject` — if absent, propose one from the content but confirm it with the user
- `body` — the plain-text body
- `html` — only when a table or formatting is needed
- `attachments` — file paths
- `account` — which account to send as (defaults to the config's `default_account`)

**There are no default recipients.** `to` is required, and a cc you don't name is
not added. Every address on the envelope is one you wrote into the call, so **if
you don't know the recipient, don't invent one — ask.** The config does not fill it
in behind you.

If you don't know what aliases exist, read the address book first:

```sh
$MAILMAIL_PY -c "
from mailmail import load_config
config = load_config()
print('accounts:', ', '.join(sorted(config.account_by_name)))
print('default:', config.default_account)
print('contacts:', ', '.join(sorted(config.address_book)))
"
```

### 2. Confirm before sending — do not skip this

Expand aliases to **their real addresses** and show them. When the user says
`team`, they must be able to see who exactly it goes to. The package expands it too:

```sh
$MAILMAIL_PY -c "
from mailmail import load_config, resolve_recipients
config = load_config()
print(resolve_recipients(['team'], address_book=config.address_book))
"
```

Then show the user this and get approval:

```
Confirm what will be sent:

  Account   naver (sending as me@example.com)
  To        lead@example.com (lead)
  Cc        reviewer@example.com (reviewer)
  Subject   Weekly report
  Attach    report.xlsx (1.2 MB)

  Body:
  ---
  Please find the report attached.
  ---

Send it?
```

If there is no cc, write `Cc (none)` explicitly. With the line absent entirely, the
user cannot tell whether a cc they never mentioned was added somewhere.

Do not send without approval. Even if the user already said "send it," show the
recipients, subject, and attachments once in their final form — what the user
approved was the *act* of sending, not yet this specific content.

**Never send a test mail to anyone but yourself — not to a manager, a director, or
any real recipient.** If the goal is to check that it works, send only between your
own addresses.

### 3. Send

Write the script to the scratchpad and run it. The body mixes newlines, quotes, and
non-ASCII text, so write it to a file rather than passing it as a shell argument.

```python
# <scratchpad>/send.py
from mailmail import send_mail

receipt = send_mail(
    account     = "naver",
    to          = ["lead", "reviewer"],   # aliases from the address book (read in step 1)
    subject     = "Weekly report",
    body        = "Hi,\n\nThis week's report is attached.\n\nBest regards,\n",
    attachments = ["/path/to/report.xlsx"],
)
print("message-id:", receipt.message_id)
print("accepted:", receipt.accepted)
if not receipt.is_complete:
    print("REFUSED:", receipt.reason_by_refused_recipient)
```

```sh
$MAILMAIL_PY <scratchpad>/send.py
```

### 4. Report the result as it is

- If `receipt.is_complete` is true, it was sent. Report the recipients and the
  message-id.
- If it is false, **only some of it went.** Always tell the user which addresses
  were refused and why. Do not round it up to success.

## When an exception is raised

The package's exceptions are already sentences a user can read. Relay them as they
are, and add only the action below.

**Rule: relay `str(err)` verbatim.** Each exception already carries, in a sentence,
what went wrong and how to fix it. Do not summarise or copy that content here — one
fact in two homes always drifts, and it has drifted before. The table below records
only the **action to add** per exception.

| Exception | Action to add |
|---|---|
| `MissingPasswordError` | **Do not have the user paste the password into the chat.** Point them to the `getpass` command they run in their own terminal (see the README). Once stored, it is not asked again. |
| `InsecureCredentialsError` | None — the exception carries the `chmod 600` command itself. |
| `BlockedAttachmentError` | Suggest sharing it as a link. |
| `EncryptedArchiveError` | Ask whether to remove the archive password or send a link. |
| `UnscannableArchiveError` | The archive is too deep or too large to scan to the bottom. Ask whether to unpack one layer or send a link. (`EncryptedArchiveError` is one kind of this, so catch this name to catch both.) |
| `MessageTooLargeError` | Ask whether to shrink the attachments. |
| `UnknownContactError` | The exception lists the aliases it knows, so let the user pick one or give an address directly. |
| `ContactCycleError` | None — the exception draws the loop. |
| `InvalidMessageError` | A recipient or the subject is empty. Get it from the user and reassemble. |
| `RecipientRefusedError` | Suspect a typo in the address first. |
| `AuthenticationFailedError` | None — the exception carries that provider's full requirements. |
| `UnknownAccountError` / `UnknownProviderError` | An account or provider not in the config. Go to "First-time setup" below. |

## First-time setup

A `ConfigError` means the config file is missing. Its format is in step 2 of the
repository's `README.md`.

**Do not write a path down.** The package decides it from `MAILMAIL_CONFIG`,
`MAILMAIL_CREDENTIALS`, and `XDG_CONFIG_HOME`, so a path written here would send a
user who set any of those to fix the wrong file. Ask the package:

```sh
$MAILMAIL_PY -c "
from mailmail import default_config_path, default_credentials_path
print('config:     ', default_config_path())
print('credentials:', default_credentials_path())
"
```

**Do not create the config file or the password inside the repository** — the
repository is committed, and may sit under a synced drive. Create them where the
command above prints. Both are outside the repository.

**Never write a password in plain text into the chat or a script.** It stays in the
transcript. Point the user to a `getpass` command they run in their own terminal.
