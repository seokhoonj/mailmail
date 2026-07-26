"""The mail services mailmail can send through, and what each one accepts.

A provider bundles the two facts that differ between mail services: how to reach
the SMTP server, and what that service refuses to carry. Adding a service means
adding one `MailProvider` here -- not another send function.
"""

from dataclasses import dataclass
from typing import Literal, TypeAlias

from mailmail.errors import UnknownProviderError

__all__ = ["GMAIL", "NAVER", "MailProvider", "SmtpSecurity", "resolve_provider"]

SmtpSecurity: TypeAlias = Literal["starttls", "ssl"]

# File types mail providers refuse because they execute on the recipient's
# machine. Gmail publishes this exact list; see
# https://support.google.com/mail/answer/6590 . The same list is applied to Naver
# conservatively: Naver states that executable files are restricted -- including
# inside archives -- but does not publish an enumeration, so this errs toward
# rejecting locally rather than letting the send bounce at the server.
EXECUTABLE_EXTENSIONS = frozenset(
    {
        ".ade", ".adp", ".apk", ".appx", ".appxbundle", ".bat", ".cab", ".chm",
        ".cmd", ".com", ".cpl", ".diagcab", ".diagcfg", ".diagpkg", ".dll",
        ".dmg", ".ex", ".ex_", ".exe", ".hta", ".img", ".ins", ".iso", ".isp",
        ".jar", ".jnlp", ".js", ".jse", ".lib", ".lnk", ".mde", ".mjs", ".msc",
        ".msi", ".msix", ".msixbundle", ".msp", ".mst", ".nsh", ".pif", ".ps1",
        ".scr", ".sct", ".shb", ".sys", ".vb", ".vbe", ".vbs", ".vhd", ".vxd",
        ".wsc", ".wsf", ".wsh", ".xll",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class MailProvider:
    """A mail service: where to reach it, and what it will carry.

    Attributes
    ----------
    name
        The key used in the configuration file (`provider = "gmail"`).
    smtp_host, smtp_port, security
        How to open an authenticated SMTP session.
    blocked_extensions
        Suffixes the service refuses, lowercase and dot-prefixed (`.exe`).
    max_message_bytes
        Largest *encoded* message the SMTP server accepts. This is the value the
        server advertises in its EHLO `SIZE` reply, so it is the transport limit
        rather than the smaller figure the web UI quotes. `Mailer` re-reads the
        live value at send time and prefers it, so this constant only has to be
        right enough to fail fast before connecting.
    max_recipients
        Most recipients (`to` + `cc` + `bcc` combined) the server accepts in one
        message, or `None` when the limit is unknown and no screen should run. The
        server counts them during the envelope and rejects the whole message once
        past the cap; screening here fails at the call site instead. Not advertised
        by the server, so unlike `max_message_bytes` there is no live value to
        prefer. Defaults to `None` so a custom provider need not supply it.
    login_requirements
        What the service demands before it will accept an SMTP login, in the
        words a rejected user needs to read. Both services here reject the
        account login password, so "authentication failed" on its own would send
        the reader hunting for a typo that is not there.
    """

    name: str
    smtp_host: str
    smtp_port: int
    security: SmtpSecurity
    blocked_extensions: frozenset[str]
    max_message_bytes: int
    max_recipients: int | None = None
    login_requirements: str


GMAIL = MailProvider(
    name               = "gmail",
    smtp_host          = "smtp.gmail.com",
    smtp_port          = 587,
    security           = "starttls",
    blocked_extensions = EXECUTABLE_EXTENSIONS,
    max_message_bytes  = 35_882_577,  # SIZE advertised by smtp.gmail.com
    max_recipients     = 100,  # to + cc + bcc, per message, over SMTP
    login_requirements = (
        "Gmail rejects the account login password over SMTP. Turn on 2-step "
        "verification, then generate a 16-character app password at "
        "https://myaccount.google.com/apppasswords and store that. The app "
        "password menu does not appear until 2-step verification is on."
    ),
)

NAVER = MailProvider(
    name               = "naver",
    smtp_host          = "smtp.naver.com",
    smtp_port          = 587,
    security           = "starttls",
    blocked_extensions = EXECUTABLE_EXTENSIONS,
    max_message_bytes  = 39_845_888,  # SIZE advertised by smtp.naver.com
    max_recipients     = 100,  # to + cc + bcc, per message
    # Naver tightened this on 2025-06-24: the account login password used to
    # work over SMTP and no longer does, so credentials that worked before that
    # date fail with a bare "535 Username and Password not accepted".
    login_requirements = (
        "Naver has required 2-step verification and an app password for "
        "POP3/IMAP/SMTP since 2025-06-24; the account login password is "
        "rejected. Turn on 2-step verification and generate an app password in "
        "the Naver ID security settings. Also check Mail > Settings > POP3/IMAP "
        "that SMTP is enabled -- toggling it off, saving, then on and saving "
        "again is what applies the change to an older account."
    ),
)

PROVIDER_BY_NAME: dict[str, MailProvider] = {
    provider.name: provider for provider in (GMAIL, NAVER)
}


def resolve_provider(name: str) -> MailProvider:
    """Look up a provider by its configuration key.

    Raises
    ------
    UnknownProviderError
        If `name` is not a provider mailmail knows.
    """
    try:
        return PROVIDER_BY_NAME[name]
    except KeyError as err:
        known = ", ".join(sorted(PROVIDER_BY_NAME))
        raise UnknownProviderError(
            f"unknown mail provider {name!r}; mailmail knows: {known}"
        ) from err
