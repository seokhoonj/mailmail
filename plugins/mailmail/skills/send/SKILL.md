---
name: send
description: "Send a result, report, or file from the conversation as email. Holds no logic of its own -- it calls the mailmail package's send(), and always shows the recipients, subject, and attachments for approval before sending. Trigger phrases: send mail, email this, mail this, send this by email, email the report, 메일 보내줘, 메일로 보내, 메일 발송, 이거 메일로, 부장님께 보내줘, 상무님께 보내줘."
---

# send — send a conversation result as email

Sending mail is an **irreversible external transmission**. A wrong mail cannot be
recalled, and an attachment sent to the wrong person stays there forever. So half
of this skill is not the sending — it is the confirmation before it.

## What this skill does not do

It holds no logic. MIME assembly, the blocked-extension check, size calculation,
alias resolution — **the mailmail package does all of it.** Do not reimplement any
of that here, do not copy the blocked list into this file, do not check extensions
yourself. That would put one fact in two homes, and the two always drift. This
skill only assembles one `send(...)` call and runs it.

## Confirm the command is ready

This skill calls the **`mailmail` command** the package installs. Once per session,
check it is there:

```sh
mailmail --version
```

If a version prints, you are ready -- every example below uses this command. If
`command not found` appears, do not invent a path; tell the user how to install it:

```sh
pip install mailmail        # into the current environment
pipx install mailmail       # for a global command, kept isolated
```

If the user installed it into a specific virtual environment, confirm they run it
with that environment active.

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

If you don't know what accounts or aliases exist, read the address book first:

```sh
mailmail contacts
```

It prints the accounts (the default marked) and every alias expanded to its
addresses.

### 2. Confirm before sending — do not skip this

Expand aliases to **their real addresses** and show them. When the user says
`team`, they must be able to see who exactly it goes to -- `mailmail contacts`
(step 1) already prints every alias expanded, so read the real addresses off it.

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

The body mixes newlines, quotes, and non-ASCII text, so do not pass it as a shell
argument -- **write it to a file and hand it to `--body-file`** (or pipe it in on
stdin):

```sh
# after writing the body to <scratchpad>/body.txt:
mailmail send --account naver \
    --to lead --to reviewer \
    --subject "Weekly report" \
    --body-file <scratchpad>/body.txt \
    --attach /path/to/report.xlsx
```

- `--to` (and `--cc`, `--bcc`) take an address or an alias, and repeat for several.
- `--attach` repeats per file; drop it when there is nothing to attach.
- Add `--html-file <path>` only when a table or formatting is needed.
- `--account` defaults to the config's `default_account` when omitted.

### 4. Report the result as it is

`mailmail send` prints the accepted recipients and the message-id, and exits
non-zero when only some of it went.

- If it exited zero, it was sent. Report the recipients and the message-id.
- If it exited non-zero, **only some of it went.** Always tell the user which
  addresses were refused and why -- the command prints them. Do not round it up to
  success.

## When an exception is raised

The `mailmail` command prints the package's exception, which is already a sentence a
user can read. Relay it as it is, and add only the action below.

**Rule: relay `str(err)` verbatim.** Each exception already carries, in a sentence,
what went wrong and how to fix it. Do not summarise or copy that content here — one
fact in two homes always drifts, and it has drifted before. The table below records
only the **action to add** per exception.

| Exception | Action to add |
|---|---|
| `MissingPasswordError` | **Do not have the user paste the password into the chat.** Point them to `mailmail set-password --account <name>`, which prompts in their own terminal. Once stored, it is not asked again. |
| `InsecureCredentialsError` | None — the exception carries the `chmod 600` command itself. |
| `BlockedAttachmentError` | Suggest sharing it as a link. |
| `EncryptedArchiveError` | Ask whether to remove the archive password or send a link. |
| `UnscannableArchiveError` | The archive is too deep or too large to scan to the bottom. Ask whether to unpack one layer or send a link. (`EncryptedArchiveError` is one kind of this, so catch this name to catch both.) |
| `MessageTooLargeError` | Ask whether to shrink the attachments. |
| `TooManyRecipientsError` | The message names more than the provider's per-message cap (100 for Gmail and Naver). Ask whether to split the recipients into several sends. |
| `UnknownContactError` | The exception lists the aliases it knows, so let the user pick one or give an address directly. |
| `ContactCycleError` | None — the exception draws the loop. |
| `InvalidMessageError` | A recipient or the subject is empty. Get it from the user and reassemble. |
| `RecipientRefusedError` | Suspect a typo in the address first. |
| `AuthenticationFailedError` | None — the exception carries that provider's full requirements. |
| `UnknownAccountError` / `UnknownProviderError` | An account or provider not in the config. Go to "First-time setup" below. |

## First-time setup

A `ConfigError` means the config file is missing. Its format is in step 2 of the
repository's `README.md`. Ask the package where the files go rather than writing a
path down:

```sh
mailmail setup
```

It prints where the config and credentials belong -- computed from `MAILMAIL_CONFIG`,
`MAILMAIL_CREDENTIALS`, and `XDG_CONFIG_HOME`, so it is right even when the user set
one of those -- plus a starter template. Then store the app password at a prompt:

```sh
mailmail set-password --account naver
```

**Never write a password in plain text into the chat or a script.** It stays in the
transcript. `mailmail set-password` reads it at a prompt in the user's own terminal,
so it is never typed on the command line and never has to be pasted into the chat.
