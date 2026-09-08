"""What the HTML builder promises a mail client: inline styles, embedded images,
and a palette that lives in one place.

The builder exists because the things a browser tolerates -- a `<style>` block,
a linked stylesheet, a remote `<img src>` -- are the things a mail client strips
or refuses. So the tests here are not about looks; they pin the properties that
decide whether the layout survives the trip: styles inline, image bytes carried
inside the message, a card that is valid HTML on its own, and no dependence on the
theme past the one object that holds it.
"""

import base64

import pytest

from mailmail import HTMLLayout, Theme


def _tiny_png(tmp_path):
    """A 1x1 PNG on disk -- enough to prove the bytes get embedded, not linked."""
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    path = tmp_path / "dot.png"
    path.write_bytes(raw)
    return path, raw


def _one_of_every_block(layout, data_uri="data:image/png;base64,AAAA"):
    """Every block kind the builder offers, so a property can be asserted across
    the whole surface rather than a lucky sample."""
    return [
        layout.header(eyebrow="EYE", title="Title", subtitle="Sub", tags=["2026"]),
        layout.summary("Highlights", ["First", "Second"]),
        layout.section("Section"),
        layout.subhead("Subhead"),
        layout.para_row("Body " + layout.hl("42%") + " " + layout.strong("bold")),
        layout.table_simple(["Name", "Q1"], [["A", "1"], ["B", "-2"]], unit="KRW"),
        layout.table_grouped(
            ["Name", "Q1"], [("Group", [["A", "1"], ["B", "-2"]])], unit="KRW"
        ),
        layout.figure_data(data_uri=data_uri, caption="A dot"),
        layout.footer("Source: x"),
    ]


class TestItStaysEmailSafe:
    def test_it_writes_no_style_block_or_linked_css(self):
        """A mail client drops `<style>` and `<link>`; everything must be an
        inline `style=` attribute instead."""
        layout = HTMLLayout()
        html = layout.render([layout.header(eyebrow="EYE", title="Title"),
                              layout.para_row("Body.")])
        assert "<style" not in html.lower()
        assert "<link" not in html.lower()
        assert 'style="' in html

    def test_every_block_is_inline_only_with_no_remote_references(self):
        """The email-safety guarantee is for every block, not a lucky sample: no
        `<style>`/`<link>`, and no remote `http(s)` reference anywhere."""
        layout = HTMLLayout()
        html = layout.render(_one_of_every_block(layout))
        lowered = html.lower()
        assert "<style" not in lowered
        assert "<link" not in lowered
        assert "http://" not in html and "https://" not in html
        assert 'style="' in html

    def test_a_png_travels_as_bytes_not_a_url(self, tmp_path):
        """Remote images are blocked, so `figure_png` must inline the file as a
        base64 data URI carrying the actual bytes."""
        layout = HTMLLayout()
        path, raw = _tiny_png(tmp_path)
        html = layout.render([layout.figure_png(path=path, caption="A dot")])
        assert f"data:image/png;base64,{base64.b64encode(raw).decode()}" in html
        assert "http://" not in html and "https://" not in html

    def test_a_caption_is_escaped_in_both_the_alt_and_the_visible_cell(self):
        """A caption is text, not markup -- the raw form must appear NOWHERE (an
        earlier bug escaped only the `alt`, leaving the visible cell raw)."""
        html = HTMLLayout().figure_data(
            data_uri="data:image/png;base64,AAAA", caption="<b>1 & 2</b>"
        )
        assert "<b>1 & 2</b>" not in html          # raw form nowhere
        assert html.count("&lt;b&gt;1 &amp; 2&lt;/b&gt;") == 2  # alt + visible cell

    def test_header_text_is_escaped(self):
        """Header eyebrow/title/subtitle/tags are text; angle brackets must not open
        a tag."""
        html = HTMLLayout().header(
            eyebrow="E<x>", title="T&D", subtitle="S<y>", tags=["<tag>"]
        )
        assert "E<x>" not in html and "T&D" not in html
        assert "E&lt;x&gt;" in html and "T&amp;D" in html
        assert "&lt;tag&gt;" in html


class TestTheThemeIsTheOnlyPlaceColorLives:
    def test_swapping_the_theme_repaints_every_block(self):
        """The default palette owns no brand; a caller's color must appear and the
        default must not survive beside it."""
        default_primary = Theme().primary
        brand = Theme(primary="#5b3a8c", dark="#3a2560")
        layout = HTMLLayout(brand)
        html = layout.render([layout.header(eyebrow="EYE", title="Title"),
                              layout.section("S"),
                              layout.table_simple(["A", "B"], [["x", "1"]])])
        assert "#5b3a8c" in html
        assert default_primary not in html

    def test_text_on_the_primary_band_uses_on_primary_not_hardcoded_white(self):
        """The header title and table head take their text color from `on_primary`,
        so a light-`primary` brand can darken it for legibility."""
        layout = HTMLLayout(Theme(primary="#e8e0ff", on_primary="#201040"))
        html = layout.render([layout.header(eyebrow="E", title="T"),
                              layout.table_simple(["A", "B"], [["x", "1"]])])
        assert "color:#201040" in html
        assert "color:#ffffff" not in html and "color:#fff;" not in html

    def test_a_negative_value_is_colored_negative(self):
        """A leading minus -- ASCII or the Unicode figure minus -- gets the negative
        color; a positive one does not."""
        neg = Theme().negative
        html = HTMLLayout().table_simple(
            ["Name", "QoQ"], [["A", "-3.1"], ["B", "−3.1"], ["C", "2.0"]]
        )
        assert html.count(f"color:{neg};") == 2   # both minus forms, not the positive


class TestBuiltInPalettes:
    def test_the_presets_are_theme_values_on_the_class(self):
        """`Theme.SLATE/DARK/PAPER` are ready-made `Theme` values (the `datetime.min`
        shape), so a caller picks one without a factory call."""
        assert isinstance(Theme.SLATE, Theme)
        assert isinstance(Theme.DARK, Theme)
        assert isinstance(Theme.PAPER, Theme)
        # DARK really inverts the surface; PAPER is a distinct light palette.
        assert Theme.DARK.bg != Theme.SLATE.bg
        assert Theme.PAPER.bg != Theme.DARK.bg

    def test_a_dark_card_carries_its_own_page_backdrop(self):
        """`render_page` with no `page_background` uses the theme's own `page`, so a
        dark card lands on its intended backdrop without the caller passing one."""
        layout = HTMLLayout(Theme.DARK)
        html = layout.render_page([layout.section("S")])
        assert f"background:{Theme.DARK.page};" in html

    def test_an_explicit_page_background_overrides_the_theme(self):
        """A caller can still force the backdrop; the explicit value wins over the
        theme's `page`."""
        layout = HTMLLayout(Theme.DARK)
        html = layout.render_page([layout.section("S")], page_background="#010203")
        assert "background:#010203;" in html
        assert f"background:{Theme.DARK.page};" not in html

    def test_dark_table_cells_carry_an_explicit_text_color(self):
        """Regression: an unstyled cell inherits the client's default black, which is
        invisible on a dark card -- every body cell must set `ink` explicitly."""
        layout = HTMLLayout(Theme.DARK)
        html = layout.table_simple(["Name", "Value"], [["A", "1"]])
        assert html.count(f"color:{Theme.DARK.ink};") >= 2  # label + number cell


class TestTheCardIsValidStandalone:
    def test_render_wraps_the_card_in_its_own_table(self):
        """Regression: `render` used to return a bare `<tr>` (no enclosing table),
        which a mail client's parser drops -- the fragment must be a whole table."""
        html = HTMLLayout().render([HTMLLayout().section("S")]).strip()
        assert html.startswith("<table") and html.endswith("</table>")

    def test_render_left_aligns_with_an_even_inset_by_default(self):
        """The card sits against the left edge with a 20px inset matching top/bottom."""
        html = HTMLLayout().render([HTMLLayout().section("S")])
        assert 'align="left"' in html
        assert "padding:20px;" in html

    def test_render_center_uses_narrower_side_gutters(self):
        """`center` is opt-in and keeps the tighter 12px side gutter."""
        html = HTMLLayout().render([HTMLLayout().section("S")], align="center")
        assert 'align="center"' in html
        assert "padding:20px 12px;" in html

    def test_align_is_keyword_only(self):
        """`align` must be passed by name, so it cannot be transposed with the
        positional `max_width`."""
        layout = HTMLLayout()
        with pytest.raises(TypeError):
            layout.render([layout.section("S")], 720, "center")  # type: ignore[misc]


class TestParagraphPlacement:
    def test_para_row_wraps_the_paragraph_in_a_table_row(self):
        """Regression: a top-level paragraph must be a `<tr><td>...<p>` row, not a bare
        `<p>` that the table parser foster-parents out of place."""
        html = HTMLLayout().para_row("Body.")
        assert html.startswith("<tr><td")
        assert "<p " in html

    def test_a_bare_paragraph_is_a_fragment_not_a_row(self):
        """`paragraph` is the inside-a-cell fragment: a `<p>` with no row wrapper."""
        html = HTMLLayout().paragraph("Body.")
        assert html.startswith("<p ")
        assert "<tr" not in html


class TestTableGrouped:
    def test_a_grouped_table_renders_a_row_per_group(self):
        """Each group gets a full-width heading row spanning every column, and its
        body rows follow."""
        layout = HTMLLayout()
        html = layout.table_grouped(
            ["Name", "Q1", "Q2"],
            [("North", [["A", "1", "2"]]), ("South", [["B", "3", "4"]])],
        )
        assert 'colspan="3"' in html
        assert "North" in html and "South" in html


class TestRenderPageIsAWholeDocument:
    def test_it_declares_color_scheme_so_apple_mail_keeps_our_colors(self):
        """Apple Mail auto-inverts a light-on-dark header unless the document declares
        dark-mode support; the `<head>` must carry the color-scheme metas."""
        html = HTMLLayout().render_page([HTMLLayout().section("S")])
        assert 'name="color-scheme"' in html
        assert 'name="supported-color-schemes"' in html

    def test_the_document_language_is_a_parameter_not_a_hardcoded_literal(self):
        """`lang` defaults to `en` and is overridable; it is not baked in."""
        assert 'lang="en"' in HTMLLayout().render_page([])
        assert 'lang="ko"' in HTMLLayout().render_page([], lang="ko")


class TestItHoldsNoStateBetweenDocuments:
    def test_one_builder_renders_independent_documents(self):
        """The theme is fixed at construction; nothing accumulates, so two renders
        from one builder do not bleed into each other."""
        layout = HTMLLayout()
        first = layout.render([layout.section("First")])
        second = layout.render([layout.section("Second")])
        assert "First" in first and "Second" not in first
        assert "Second" in second and "First" not in second
