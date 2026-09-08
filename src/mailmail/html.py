"""Build an email-safe HTML layout -- brand-neutral, palette-swappable.

Email clients strip `<style>` blocks and external CSS and block remote images, so
`HTMLLayout` writes only inline styles on a table layout -- the text, tables, and
colors render the same in Gmail, Naver, and Outlook. It carries no brand of its
own: the look lives entirely in a `Theme`, and the default `Theme` is a neutral
slate. Swap the palette to dress it in a brand's colors -- change only the `Theme`,
and every block follows.

Figures (`figure_png`/`figure_data`) embed their bytes as a base64 `data:` URI.
Naver and most desktop clients show these, but Gmail and Outlook drop `data:`
images in received mail -- until the message is sent as `multipart/related` with
`cid:` references, treat figures as best-effort, not guaranteed in Gmail.

    from mailmail import HTMLLayout, Theme, send

    d = HTMLLayout()   # neutral default; HTMLLayout(Theme(primary=...)) to rebrand
    html = d.render_page([
        d.header(eyebrow="WEEKLY", title="Report", tags=["2026-08-21"]),
        d.summary("Highlights", ["First point", "Second point"]),
        d.section("Detail"),
        d.para_row("Body " + d.hl("42%") + " and " + d.strong("emphasis") + "."),
        d.table_simple(["Name", "Q1", "Q2"], [["A", "1", "2"]], unit="units"),
        d.footer("Source: ..."),
    ])
    send(to="lead", subject="Report", body="See below.", html=html)

The content -- text, figures, tables -- is supplied by the caller; `HTMLLayout` only
supplies the layout and the palette. `render_page` returns a complete HTML document
(with the `<head>` metas mail clients need for correct dark-mode rendering) -- pass
it to `send(html=...)` for a standalone mail, or save it as an attachment. `render`
returns just the card fragment, for embedding inside a document you already have.
"""

import base64
import html as _html
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal

__all__ = ["Align", "Group", "HTMLLayout", "Row", "Theme"]

Cell = object
Row = list[Cell]
Group = tuple[str, list[Row]]
Align = Literal["left", "center"]


@dataclass(frozen=True, kw_only=True)
class Theme:
    """The palette and font a `HTMLLayout` draws with.

    Every field is a CSS color string (or, for `font`, a font-family stack). The
    defaults are a neutral cool slate that belongs to no brand; override the ones
    you care about and the rest stay neutral::

        Theme(primary="#5b3a8c", dark="#3a2560")   # a purple brand

    Attributes
    ----------
    primary
        Header band, section rules, and table heads.
    dark
        The header band's gradient end, and the default heading color (a deeper
        shade of `primary`).
    heading
        Heading and bold-text color; defaults to `dark`. Set apart (e.g. light) when
        the body surface inverts, as in `Theme.DARK`.
    ink, muted
        Body text and secondary/caption text.
    line
        Borders and dividers.
    bg
        The card and table-cell surface (the content background) -- white in the
        light themes, dark in `Theme.DARK`.
    page
        The backdrop behind the card (`render_page`'s outer background). Kept on the
        theme so a dark card carries its own page tone and callers need not pass one.
    soft, soft2
        Soft panel background and the table zebra stripe.
    group, group_line
        Background and border of a grouped-table's group row.
    eyebrow, subtitle
        Text colors used on the dark header band (a light tint of `primary`).
    on_primary
        Text/icon color that sits on the `primary` band and the table heads; keep it
        readable against `primary` (light `primary` needs a dark `on_primary`).
    negative
        Numbers that read as negative (a leading `-` or Unicode minus).
    highlight
        Background of an inline `hl(...)` mark.
    footer_ink
        Text color of the footer band.
    font
        The CSS font-family stack for text.
    numeric
        The monospace font-family stack for numeric table cells, so every digit
        shares one width and columns of numbers line up. Email clients largely
        ignore `font-variant-numeric: tabular-nums`, so a monospace face -- not a
        proportional one asked to use tabular figures -- is what actually aligns
        digits in Gmail and Naver.
    """

    # Built-in palettes, assigned below the class body (a Theme cannot be
    # constructed until the class exists) -- reached as `Theme.SLATE` etc.
    SLATE: ClassVar["Theme"]
    DARK:  ClassVar["Theme"]
    PAPER: ClassVar["Theme"]

    primary:    str = "#37505c"
    dark:       str = "#243740"
    heading:    str | None = None   # None -> falls back to `dark` (see heading_color)
    ink:        str = "#1e2a33"
    muted:      str = "#5a6a75"
    line:       str = "#dde5ea"
    bg:         str = "#ffffff"
    page:       str = "#eef2f5"     # backdrop behind the card (render_page)
    soft:       str = "#eef2f5"
    soft2:      str = "#f5f8fa"
    group:      str = "#e3ebf0"
    group_line: str = "#cdd8df"
    eyebrow:    str = "#cdd9e0"
    subtitle:   str = "#e6edf1"
    on_primary: str = "#ffffff"     # text color on the primary band + table heads
    negative:   str = "#c0392b"
    highlight:  str = "#fff2cc"
    footer_ink: str = "#8a97a0"
    font:       str = ("'Apple SD Gothic Neo','Malgun Gothic',"
                       "'Noto Sans KR','Segoe UI',sans-serif")
    numeric:    str = "'Consolas','Menlo','Courier New',monospace"

    @property
    def heading_color(self) -> str:
        """Heading and bold-text color: `heading` when set, otherwise `dark` -- so a
        theme that sets only `dark` still colors its headings, and `Theme.DARK`
        can make them light without disturbing the header band."""
        return self.heading or self.dark


# Built-in palettes. Constructed here, below the class body, because a Theme
# instance cannot exist until Theme is fully defined. Any other palette is a
# direct `Theme(primary=..., dark=...)`; the "dark" mode is one complete theme
# rather than a flag, the way plotly's `plotly_dark` and bokeh's `dark_minimal`
# ship dark as its own named theme.
Theme.SLATE = Theme()
Theme.DARK = Theme(
    bg="#212b34", ink="#e2e9ef", heading="#f4f8fa", muted="#97a7b3",
    line="#3a4a56", soft="#2b3844", soft2="#27313b",
    primary="#33556a", dark="#0f151a",
    group="#2c3a45", group_line="#42525e",
    negative="#e06a5c", highlight="#4a3f1a", footer_ink="#93a3af",
    page="#eef1f4",   # a light backdrop the dark card lifts off of
)
Theme.PAPER = Theme(
    primary="#333333", dark="#111111",
    ink="#1a1a1a", muted="#6b7280", line="#ececec",
    soft="#f7f7f8", soft2="#fbfbfc",
    group="#f0f0f2", group_line="#e2e2e6",
    eyebrow="#d9d9d9", subtitle="#ededed", footer_ink="#9a9aa0",
    page="#f4f5f6",   # a hair of gray so the white card edge stays visible
)


def _is_negative(value: Cell) -> bool:
    s = str(value)
    return s.startswith("-") or s.startswith("−")  # ASCII or Unicode minus


class HTMLLayout:
    """An email-safe HTML layout builder for one `Theme`.

    Construct once with a theme, then call the block methods -- each returns an
    HTML fragment -- and hand the list to `render`. Because the theme is fixed at
    construction, the blocks share it without threading a palette through every
    call. Nothing is stateful between calls: the same `HTMLLayout` builds any number
    of independent documents.
    """

    def __init__(self, theme: Theme | None = None) -> None:
        self.theme = Theme() if theme is None else theme

    # ---- inline marks ---------------------------------------------------
    def paragraph(self, inner: str) -> str:
        """A body paragraph fragment. `inner` is HTML -- combine with `hl`/`strong`.

        This is a bare `<p>` for placing *inside* a cell. As a top-level block, hand it
        to `para_row` (or `row`) instead -- otherwise it is foster-parented out of the
        table and loses its left alignment.
        """
        t = self.theme
        return (
            f'<p style="margin:11px 0;font-size:14px;line-height:1.68;'
            f'color:{t.ink};font-family:{t.font};">{inner}</p>'
        )

    def hl(self, text: str) -> str:
        """Inline highlight -- a keyword or number on a highlight background. `text`
        is plain text and is escaped."""
        return (
            f'<span style="background:{self.theme.highlight};padding:0 2px;'
            f'font-weight:700;">{_html.escape(text)}</span>'
        )

    def strong(self, text: str) -> str:
        """Bold emphasis in the theme's heading color. `text` is plain text and is
        escaped."""
        return f'<b style="color:{self.theme.heading_color};">{_html.escape(text)}</b>'

    # ---- structural blocks ----------------------------------------------
    def row(self, inner: str, pad: str = "4px 24px") -> str:
        """Wrap arbitrary inner HTML in one padded table row."""
        return f'<tr><td style="padding:{pad};">{inner}</td></tr>'

    def para_row(self, inner: str) -> str:
        """A paragraph on its own row (the common case)."""
        return self.row(self.paragraph(inner))

    def section(self, title: str) -> str:
        """A section heading with a colored left rule. `title` is plain text and is
        escaped."""
        t = self.theme
        return (
            '<tr><td style="padding:26px 24px 0 24px;">'
            f'<div style="font-family:{t.font};font-size:18px;font-weight:800;'
            f'color:{t.heading_color};border-left:4px solid {t.primary};'
            f'padding:2px 0 2px 12px;line-height:1.4;">{_html.escape(title)}</div>'
            '</td></tr>'
        )

    def subhead(self, text: str) -> str:
        """A lighter sub-heading, e.g. a caption above a table. `text` is plain text
        and is escaped."""
        t = self.theme
        return (
            '<tr><td style="padding:18px 24px 0 24px;">'
            f'<div style="font-family:{t.font};font-size:14px;font-weight:700;'
            f'color:{t.primary};">{_html.escape(text)}</div></td></tr>'
        )

    def header(
        self,
        *,
        eyebrow: str,
        title: str,
        subtitle: str = "",
        tags: Sequence[str] = (),
    ) -> str:
        """The top band: a small eyebrow, a large title, an optional subtitle,
        and optional pill-shaped tags (a date, an as-of note). All are plain text
        and are escaped."""
        t = self.theme
        eyebrow = _html.escape(eyebrow)
        title = _html.escape(title)
        subtitle = _html.escape(subtitle)
        chips = "".join(
            '<span style="display:inline-block;'
            'background:rgba(255,255,255,.16);'
            'border:1px solid rgba(255,255,255,.3);'
            f'color:{t.on_primary};border-radius:20px;'
            'padding:4px 12px;font-size:12px;font-weight:600;'
            f'margin:0 6px 6px 0;">{_html.escape(tag)}</span>'
            for tag in tags
        )
        sub = (
            f'<div style="font-size:14px;color:{t.subtitle};margin-top:8px;'
            f'line-height:1.5;">{subtitle}</div>'
        ) if subtitle else ""
        tagbox = f'<div style="margin-top:16px;">{chips}</div>' if chips else ""
        return (
            f'<tr><td style="background:{t.primary};'
            f'background:linear-gradient(135deg,{t.primary},{t.dark});'
            'padding:26px 24px;">'
            f'<div style="font-size:11px;letter-spacing:2.5px;color:{t.eyebrow};'
            'font-weight:700;text-transform:uppercase;margin-bottom:8px;">'
            f'{eyebrow}</div>'
            f'<div style="font-size:27px;font-weight:800;color:{t.on_primary};'
            f'line-height:1.25;">{title}</div>{sub}{tagbox}</td></tr>'
        )

    def summary(self, title: str, bullets: Sequence[str]) -> str:
        """A soft-panel summary box: a title over a bullet list. The `title` is plain
        text (escaped); each bullet is HTML."""
        t = self.theme
        items = "<br>".join(f"• {b}" for b in bullets)
        return self.row(
            f'<div style="background:{t.soft};border-radius:10px;'
            f'padding:16px 18px;margin-top:16px;font-family:{t.font};">'
            f'<div style="font-size:15px;font-weight:800;color:{t.heading_color};'
            f'margin-bottom:9px;">{_html.escape(title)}</div>'
            f'<div style="font-size:13.5px;color:{t.ink};line-height:1.7;">'
            f'{items}</div></div>',
            pad="12px 24px 2px",
        )

    def footer(self, text: str) -> str:
        """The bottom band -- source lines, a disclaimer. `text` is HTML."""
        t = self.theme
        return (
            f'<tr><td style="background:{t.soft};border-top:1px solid {t.line};'
            f'padding:16px 24px;font-size:11px;color:{t.footer_ink};'
            f'line-height:1.7;font-family:{t.font};">{text}</td></tr>'
        )

    # ---- figures --------------------------------------------------------
    def figure_data(self, *, data_uri: str, caption: str) -> str:
        """A framed image (given as a data URI) with a caption below it. `caption`
        is plain text and is escaped (in both the visible cell and the `alt`)."""
        t = self.theme
        safe_caption = _html.escape(caption)
        return (
            '<tr><td style="padding:14px 24px 2px 24px;">'
            '<table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" style="border:1px solid {t.line};'
            f'border-radius:10px;overflow:hidden;background:{t.bg};">'
            f'<tr><td style="padding:0;"><img src="{data_uri}" alt="{safe_caption}" '
            'style="display:block;width:100%;max-width:100%;height:auto;'
            'border:0;"></td></tr>'
            f'<tr><td style="padding:8px 12px;font-family:{t.font};'
            f'font-size:11.5px;color:{t.muted};background:{t.soft};'
            f'border-top:1px solid {t.line};">{safe_caption}</td></tr>'
            '</table></td></tr>'
        )

    def figure_png(self, *, path: Path | str, caption: str) -> str:
        """A framed PNG file, embedded as a base64 data URI. `caption` is plain text
        and is escaped. Note: Gmail and Outlook drop `data:` images in received mail
        (see the module docstring)."""
        b64 = base64.b64encode(Path(path).read_bytes()).decode()
        return self.figure_data(
            data_uri=f"data:image/png;base64,{b64}", caption=caption
        )

    # ---- tables ---------------------------------------------------------
    def _num_cell(self, alt: bool) -> str:
        t = self.theme
        bg = t.soft2 if alt else t.bg
        return (
            f"padding:4px 6px;font-size:11px;font-family:{t.numeric};"
            f"font-variant-numeric:tabular-nums;border:1px solid {t.line};"
            f"text-align:right;white-space:nowrap;color:{t.ink};background:{bg};"
        )

    def _label_cell(self, alt: bool, indent: int = 16) -> str:
        t = self.theme
        bg = t.soft2 if alt else t.bg
        return (
            f"padding:4px 6px 4px {indent}px;font-size:11px;font-family:{t.font};"
            f"border:1px solid {t.line};text-align:left;white-space:nowrap;"
            f"color:{t.ink};background:{bg};"
        )

    def _head_cell(self, first: bool = False) -> str:
        t = self.theme
        align = "left" if first else "right"
        return (
            f"padding:6px 6px;background:{t.primary};color:{t.on_primary};"
            f"font-weight:700;font-size:11px;font-family:{t.font};"
            f"border:1px solid {t.primary};"
            f"text-align:{align};white-space:nowrap;"
        )

    def _cells(self, row: Row, alt: bool, indent: int) -> str:
        cells = [
            f'<td style="{self._label_cell(alt, indent)}">'
            f'{_html.escape(str(row[0]))}</td>'
        ]
        for c in row[1:]:
            col = f"color:{self.theme.negative};" if _is_negative(c) else ""
            cells.append(
                f'<td style="{self._num_cell(alt)}{col}">{_html.escape(str(c))}</td>'
            )
        return "<tr>" + "".join(cells) + "</tr>"

    def _wrap(self, rows: list[str], unit: str, min_width: int) -> str:
        t = self.theme
        unit_div = (
            f'<div style="text-align:right;font-size:10.5px;color:{t.muted};'
            f'font-family:{t.font};margin:0 0 3px;">{_html.escape(unit)}</div>'
        ) if unit else ""
        return (
            '<tr><td style="padding:10px 24px 2px 24px;">'
            f'{unit_div}<div style="overflow-x:auto;'
            '-webkit-overflow-scrolling:touch;">'
            '<table role="presentation" cellpadding="0" cellspacing="0" '
            'style="border-collapse:collapse;width:100%;'
            f'min-width:{min_width}px;">{"".join(rows)}</table></div></td></tr>'
        )

    def _head_row(self, headers: Sequence[str]) -> str:
        th = "".join(
            f'<td style="{self._head_cell(i == 0)}">{_html.escape(str(h))}</td>'
            for i, h in enumerate(headers)
        )
        return f"<tr>{th}</tr>"

    def table_simple(
        self,
        headers: Sequence[str],
        rows: Sequence[Row],
        unit: str = "",
        min_width: int = 520,
    ) -> str:
        """A flat table. First column is a left-aligned label, the rest are
        right-aligned numbers; values with a leading minus read as negative. Headers
        and cell values are plain text and are escaped."""
        out = [self._head_row(headers)]
        for j, r in enumerate(rows):
            out.append(self._cells(r, alt=j % 2 == 1, indent=8))
        return self._wrap(out, unit, min_width)

    def table_grouped(
        self,
        headers: Sequence[str],
        groups: Sequence[Group],
        unit: str = "",
        min_width: int = 520,
    ) -> str:
        """A table whose rows are split into named groups, each introduced by a
        full-width group row. `groups` is a list of `(group_name, rows)`. Group names,
        headers, and cell values are plain text and are escaped."""
        t = self.theme
        out = [self._head_row(headers)]
        for gname, rows in groups:
            out.append(
                f'<tr><td colspan="{len(headers)}" style="padding:5px 8px;'
                f'background:{t.group};color:{t.heading_color};font-weight:800;'
                f'font-size:11.5px;font-family:{t.font};'
                f'border:1px solid {t.group_line};">{_html.escape(gname)}</td></tr>'
            )
            for j, r in enumerate(rows):
                out.append(self._cells(r, alt=j % 2 == 1, indent=16))
        return self._wrap(out, unit, min_width)

    # ---- document wrapper -----------------------------------------------
    def render(
        self, parts: Sequence[str], max_width: int = 720, *, align: Align = "left"
    ) -> str:
        """Wrap the block fragments into one card table -- a fragment to embed inside
        a document you already have. For a standalone email use `render_page` (it adds
        the `<head>` metas mail clients need). `align` places the card: `left`
        (default) flushes it left, `center` centers it. The card is wrapped in its own
        table, so the fragment is valid HTML on its own."""
        inner = "".join(parts)
        t = self.theme
        # A left-flushed card takes an even 20px inset so its left gap matches the
        # top and bottom; centered keeps the tighter 12px side gutter.
        pad = "20px" if align == "left" else "20px 12px"
        return (
            '<table role="presentation" width="100%" cellpadding="0" '
            'cellspacing="0"><tr>'
            f'<td align="{align}" style="padding:{pad};">'
            '<table role="presentation" cellpadding="0" cellspacing="0" '
            f'style="width:100%;max-width:{max_width}px;background:{t.bg};'
            f'border:1px solid {t.line};border-radius:14px;overflow:hidden;'
            f'font-family:{t.font};">{inner}</table></td></tr></table>'
        )

    def render_page(
        self,
        parts: Sequence[str],
        max_width: int = 720,
        *,
        page_background: str | None = None,
        align: Align = "left",
        lang: str = "en",
    ) -> str:
        """A complete standalone HTML document -- pass this to `send(html=...)` for a
        whole email, or save it as an attachment. It adds the outer page background and
        the `color-scheme` metas that stop Apple Mail from re-coloring the header in
        dark mode. `page_background` defaults to the theme's own `page` tone; `align`
        places the card (`left` default or `center`); `lang` sets the document
        language."""
        page = page_background if page_background is not None else self.theme.page
        body = self.render(parts, max_width, align=align)
        return (
            f'<!DOCTYPE html><html lang="{lang}"><head><meta charset="utf-8">'
            '<meta name="viewport" '
            'content="width=device-width,initial-scale=1">'
            # Declare dark-mode awareness so iOS/Apple Mail stops auto-inverting our
            # colors -- without this it recolors the header band's light title to dark.
            '<meta name="color-scheme" content="light dark">'
            '<meta name="supported-color-schemes" content="light dark"></head>'
            f'<body style="margin:0;padding:0;background:{page};">'
            '<table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" style="background:{page};">'
            f'<tr><td>{body}</td></tr></table></body></html>'
        )
