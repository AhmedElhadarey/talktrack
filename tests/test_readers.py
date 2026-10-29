"""Reading sources to explain: web pages to Markdown, PDFs, images, text, and which links are refused."""

from __future__ import annotations

import io

import httpx
import pytest

from talktrack import readers
from talktrack.readers import ReadError, html_to_markdown

PAGE = """<!doctype html><html><head><title>Bloom filters - Site</title>
<meta property="og:title" content="Bloom filters"><meta name="author" content="Ada Byron">
<meta property="article:published_time" content="2024-03-01T10:00:00Z"><script>var tracking = 1</script></head>
<body><nav><a href="/">Home</a></nav><div class="cookie-banner">We use cookies</div>
<main><article><h1>Bloom filters</h1>
<p>A <b>Bloom filter</b> answers <i>maybe</i> or <code>no</code>.<br>It never says yes for sure.
<p>It uses k hash functions.
<h2>How it works</h2><ul><li>Set bits<li>Check bits<ul><li>all set: maybe</ul></ul>
<ol><li>one<li>two</ol>
<pre><code>def add(x):
    for h in hs: bits[h(x)] = 1</code></pre>
<table><tr><th>m<th>k<tr><td>1000<td>7<tr><td>2000<td>14</table>
<blockquote><p>Quoted wisdom.</blockquote>
<p>The error rate is <math alttext="{\\displaystyle p=(1-e^{-kn/m})^{k}}"><mi>p</mi></math> at most.</p>
<img src="/img/bloom.png" alt="A bit array"><img src="data:image/png;base64,xx" alt="inline"><img src="/px.gif" width="1">
<div class="share-buttons">Share on X</div></article><aside>Related posts</aside></main>
<footer>Site footer</footer></body></html>"""


def md() -> str:
    return html_to_markdown(PAGE, "https://example.com/posts/bloom")[1]


# -- web pages --------------------------------------------------------------------------------


def test_page_keeps_the_article_and_its_title_and_byline():
    title, text = html_to_markdown(PAGE, "https://example.com/posts/bloom")
    assert title == "Bloom filters"
    assert text.startswith("# Bloom filters\n\nAda Byron · 2024-03-01\n\n")
    assert text.count("# Bloom filters") == 1  # the article's own h1 is not repeated
    assert "A Bloom filter answers maybe or `no`.\nIt never says yes for sure." in text


@pytest.mark.parametrize("furniture", ["Home", "cookies", "tracking", "Share on X", "Related posts", "Site footer"])
def test_page_drops_menus_banners_scripts_and_asides(furniture):
    assert furniture not in md()


def test_page_keeps_headings_lists_code_tables_quotes_and_formulas():
    text = md()
    assert "## How it works" in text
    assert "- Set bits\n- Check bits\n  - all set: maybe" in text
    assert "1. one\n2. two" in text
    assert "```\ndef add(x):\n    for h in hs: bits[h(x)] = 1\n```" in text
    assert "| m | k |\n| --- | --- |\n| 1000 | 7 |\n| 2000 | 14 |" in text
    assert "> Quoted wisdom." in text
    assert "$p=(1-e^{-kn/m})^{k}$" in text


def test_page_lists_images_as_absolute_links_without_inline_data_or_pixels():
    text = md()
    assert "![A bit array](https://example.com/img/bloom.png)" in text
    assert "data:" not in text and "px.gif" not in text


def test_page_without_article_uses_main_then_body():
    title, text = html_to_markdown("<html><body><nav>Menu</nav><p>Only text.</p></body></html>")
    assert text == "Only text." and title == ""
    _, text = html_to_markdown("<body><p>outside</p><main><p>inside main</p></main></body>")
    assert text == "inside main"


def test_page_prefers_main_over_a_small_article_such_as_a_comment():
    html = "<main><p>" + "Long body text. " * 50 + "</p><article><p>A short comment.</p></article></main>"
    assert html_to_markdown(html)[1].startswith("Long body text.")


def test_deeply_nested_page_does_not_overflow():
    html = "<div>" * 5000 + "deep text" + "</div>" * 5000
    assert "deep text" in html_to_markdown(html)[1]


def test_table_cells_with_pipes_are_escaped():
    text = html_to_markdown("<table><tr><td>a|b</td><td>c</td></tr></table>")[1]
    assert "| a\\|b | c |" in text


# -- from bytes -----------------------------------------------------------------------------------


def test_long_text_comes_in_parts(monkeypatch):
    monkeypatch.setattr(readers, "MAX_CHARS", 100)
    body = "\n\n".join(f"Paragraph {i} " + "word " * 10 for i in range(10))
    first = readers.from_bytes(body.encode(), "text/plain", "notes.txt")
    assert first.kind == "text" and first.parts > 1 and first.part == 1
    assert first.text.startswith("Paragraph 0")
    last = readers.from_bytes(body.encode(), "text/plain", "notes.txt", part=first.parts)
    assert "Paragraph 9" in last.text
    with pytest.raises(ReadError, match="part=99 does not exist"):
        readers.from_bytes(body.encode(), "text/plain", "notes.txt", part=99)


def test_split_text_cuts_between_paragraphs_and_inside_a_huge_one():
    assert readers.split_text("a\n\nb", 10) == ["a\n\nb"]
    assert readers.split_text("aaaa\n\nbbbb", 6) == ["aaaa", "bbbb"]
    assert readers.split_text("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5]


def test_charset_from_the_content_type_is_used():
    m = readers.from_bytes("café".encode("latin-1"), "text/plain; charset=latin-1", "a.txt")
    assert m.text == "café"


def test_html_without_a_content_type_is_still_a_page():
    m = readers.from_bytes(b"<!DOCTYPE html><html><body><p>Hi</p></body></html>", "", "page")
    assert m.kind == "page" and m.text == "Hi"


def test_images_are_recognised_by_their_bytes():
    m = readers.from_bytes(PNG, "application/octet-stream", "https://x.org/a.png", slide_src="https://x.org/a.png")
    assert (m.kind, m.mime, m.slide_src) == ("image", "image/png", "https://x.org/a.png")


def test_too_large_image_is_refused(monkeypatch):
    monkeypatch.setattr(readers, "MAX_IMAGE_BYTES", 10)
    with pytest.raises(ReadError, match="images up to"):
        readers.from_bytes(PNG, "image/png", "a.png")


@pytest.mark.parametrize("ctype", ["video/mp4", "audio/mpeg", "application/zip"])
def test_video_audio_and_binaries_are_refused(ctype):
    with pytest.raises(ReadError, match="can be read"):
        readers.from_bytes(b"\0\0\0 ftypisom", ctype, "clip")


# -- PDF ------------------------------------------------------------------------------------------


def make_pdf(pages: list[str], outline: list[tuple[str, int]] = (), title: str = "") -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    w = PdfWriter()
    font = w._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )  # fmt: skip
    )
    for text in pages:
        page = w.add_blank_page(612, 792)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 18 Tf 72 700 Td ({text}) Tj ET".encode() if text else b"")
        page[NameObject("/Contents")] = w._add_object(stream)
    for name, n in outline:
        w.add_outline_item(name, n)
    if title:
        w.add_metadata({"/Title": title})
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def test_pdf_gives_outline_and_text_page_by_page():
    data = make_pdf(["Hashing basics", "False positives", "Sizing the array"], [("Intro", 0), ("Sizing", 2)], title="Bloom notes")
    m = readers.from_bytes(data, "application/pdf", "docs/bloom.pdf")
    assert (m.kind, m.title, m.pages, m.shown) == ("pdf", "Bloom notes", 3, (1, 3))
    assert m.outline == [(0, 1, "Intro"), (0, 3, "Sizing")]
    assert "## Page 1\n\nHashing basics" in m.text and "## Page 3\n\nSizing the array" in m.text


def test_pdf_page_range_and_a_title_from_the_file_name():
    m = readers.read_pdf(make_pdf(["one", "two", "three"]), "https://x.org/papers/attention.pdf", "2-3")
    assert m.title == "attention" and m.shown == (2, 3)
    assert "one" not in m.text and "three" in m.text


def test_pdf_stops_at_the_text_budget_and_says_where(monkeypatch):
    monkeypatch.setattr(readers, "MAX_CHARS", 40)
    m = readers.read_pdf(make_pdf(["first page text", "second page text", "third page text"]), "a.pdf")
    assert m.shown == (1, 1)


def test_pdf_page_without_text_is_marked():
    m = readers.read_pdf(make_pdf([""]), "scan.pdf")
    assert "(No text on this page.)" in m.text


@pytest.mark.parametrize(("pages", "match"), [("9-12", "past the end"), ("x", "not a page range")])
def test_pdf_bad_page_ranges(pages, match):
    with pytest.raises(ReadError, match=match):
        readers.read_pdf(make_pdf(["one"]), "a.pdf", pages)


def test_damaged_pdf_is_an_error():
    with pytest.raises(ReadError, match="not a PDF that can be read"):
        readers.read_pdf(b"%PDF-1.7\nnot really", "broken.pdf")


# -- local files ----------------------------------------------------------------------------------


async def test_local_files_are_read_from_the_project(project):
    (project / "docs" / "paper.pdf").write_bytes(make_pdf(["inside"]))
    assert (await readers.read("docs/notes.txt", project)).text == "plain text"
    assert (await readers.read("docs/paper.pdf", project)).kind == "pdf"
    img = await readers.read("docs/timeline.png", project)
    assert (img.kind, img.slide_src) == ("image", "docs/timeline.png")


async def test_local_files_outside_the_project_are_refused(project, tmp_path):
    (tmp_path / "secret.txt").write_text("key")
    with pytest.raises(ReadError, match="outside the project.*Copy it into the project folder"):
        await readers.read("../secret.txt", project)
    with pytest.raises(ReadError, match="no project folder"):
        await readers.read("docs/notes.txt", None)


async def test_empty_source_is_an_error(project):
    with pytest.raises(ReadError, match="empty"):
        await readers.read("  ", project)


# -- links --------------------------------------------------------------------------------------


@pytest.fixture
def public(monkeypatch):
    """Every host name resolves to a public address, unless the test says otherwise."""
    table: dict[str, list[str]] = {}

    async def addresses(host, port):
        return table.get(host, ["93.184.215.14"])

    monkeypatch.setattr(readers, "_addresses", addresses)
    return table


def transport(routes: dict[str, httpx.Response], seen: list | None = None) -> httpx.MockTransport:
    """Answers by the address the request names, `scheme://Host/path`: the URL itself carries
    the resolved IP address."""

    def answer(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        key = f"{req.url.scheme}://{req.headers['host']}{req.url.raw_path.decode()}"
        return routes.get(key, httpx.Response(404))

    return httpx.MockTransport(answer)


async def test_link_to_a_page(public):
    t = transport({"https://example.com/posts/bloom": httpx.Response(200, html=PAGE)})
    m = await readers.read("https://example.com/posts/bloom", None, transport=t)
    assert m.kind == "page" and m.title == "Bloom filters" and "## How it works" in m.text


async def test_link_to_a_pdf_and_an_image(public):
    t = transport({
        "https://x.org/a.pdf": httpx.Response(200, content=make_pdf(["paper"]), headers={"content-type": "application/pdf"}),
        "https://x.org/a.png": httpx.Response(200, content=PNG, headers={"content-type": "image/png"}),
    })  # fmt: skip
    assert (await readers.read("https://x.org/a.pdf", None, transport=t)).kind == "pdf"
    img = await readers.read("https://x.org/a.png", None, transport=t)
    assert img.kind == "image" and img.slide_src == "https://x.org/a.png"


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254", "::1", "fe80::1", "::ffff:127.0.0.1", "0.0.0.0"]
)
async def test_links_to_local_or_private_addresses_are_refused(public, address):
    public["intranet.example"] = [address]
    with pytest.raises(ReadError, match="private or local"):
        await readers.read("http://intranet.example/admin", None, transport=transport({}))


async def test_a_redirect_to_a_private_address_is_refused(public):
    public["evil.example"] = ["127.0.0.1"]
    t = transport({"https://ok.example/go": httpx.Response(302, headers={"location": "http://evil.example/secret"})})
    with pytest.raises(ReadError, match="evil.example is a private or local address"):
        await readers.read("https://ok.example/go", None, transport=t)


async def test_redirects_are_followed_up_to_a_limit(public):
    t = transport({
        "https://a.example/1": httpx.Response(301, headers={"location": "/2"}),
        "https://a.example/2": httpx.Response(200, text="arrived"),
    })  # fmt: skip
    m = await readers.read("https://a.example/1", None, transport=t)
    assert m.text == "arrived" and m.origin == "https://a.example/2"
    loop = transport({"https://a.example/loop": httpx.Response(302, headers={"location": "/loop"})})
    with pytest.raises(ReadError, match="redirects"):
        await readers.read("https://a.example/loop", None, transport=loop)


@pytest.mark.parametrize(("url", "match"), [
    ("file:///etc/passwd", "only http and https"),
    ("ftp://x.org/a", "only http and https"),
    ("https://user:pw@x.org/", "user name or password"),
])  # fmt: skip
async def test_other_schemes_and_credentials_are_refused(public, url, match):
    with pytest.raises(ReadError, match=match):
        await readers.read(url, None, transport=transport({}))


async def test_http_errors_and_oversized_bodies(public, monkeypatch):
    t = transport({
        "https://x.org/gone": httpx.Response(404),
        "https://x.org/big": httpx.Response(200, content=b"a" * 100),
    })  # fmt: skip
    with pytest.raises(ReadError, match="answered 404"):
        await readers.read("https://x.org/gone", None, transport=t)
    monkeypatch.setattr(readers, "MAX_BYTES", 50)
    with pytest.raises(ReadError, match="MB"):
        await readers.read("https://x.org/big", None, transport=t)


async def test_network_failure_is_a_read_error(public):
    def fail(req):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ReadError, match="could not fetch"):
        await readers.read("https://x.org/", None, transport=httpx.MockTransport(fail))


async def test_unknown_host_is_a_read_error():
    with pytest.raises(ReadError, match="cannot find"):
        await readers.check_public("https://no-such-host.invalid/")


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000000000200015e2ad4f40000000049454e44ae426082"
)


# -- the connection goes to the address that was checked ----------------------------------------


async def test_the_request_goes_to_the_checked_address_with_the_name_for_host_and_tls(public):
    public["example.com"] = ["93.184.215.14"]
    seen: list = []
    t = transport({"https://example.com/a": httpx.Response(200, text="ok")}, seen)
    assert (await readers.read("https://example.com/a", None, transport=t)).text == "ok"
    (req,) = seen
    assert req.url.host == "93.184.215.14" and req.headers["host"] == "example.com"
    assert req.extensions["sni_hostname"] == "example.com"


async def test_a_name_that_changes_its_answer_after_the_check_is_not_followed(monkeypatch):
    answers = iter([["93.184.215.14"], ["127.0.0.1"]])

    async def addresses(host, port):
        return next(answers)

    monkeypatch.setattr(readers, "_addresses", addresses)
    seen: list = []
    t = transport({"http://rebind.example/": httpx.Response(200, text="public")}, seen)
    await readers.read("http://rebind.example/", None, transport=t)
    assert [r.url.host for r in seen] == ["93.184.215.14"]  # resolved once; 127.0.0.1 never asked for


async def test_international_names_are_checked_in_the_form_that_is_connected_to(public):
    public["xn--fa-hia.de"] = ["127.0.0.1"]  # faß.de under IDNA 2008, as httpx encodes it
    public["fass.de"] = ["93.184.215.14"]  # the IDNA 2003 form, which a naive check would resolve
    with pytest.raises(ReadError, match="xn--fa-hia.de is a private or local address"):
        await readers.read("https://faß.de/", None, transport=transport({}))


async def test_ip_literals_and_ports_keep_their_host_header(public):
    seen: list = []
    t = transport({"http://[2606:4700::1111]:8080/x": httpx.Response(200, text="v6")}, seen)
    public["2606:4700::1111"] = ["2606:4700::1111"]
    assert (await readers.read("http://[2606:4700::1111]:8080/x", None, transport=t)).text == "v6"
    assert seen[0].headers["host"] == "[2606:4700::1111]:8080" and "sni_hostname" not in seen[0].extensions


@pytest.mark.parametrize(
    ("address", "public_"),
    [
        ("93.184.215.14", True),
        ("2606:4700::1111", True),
        ("64:ff9b::a00:1", False),  # NAT64 to 10.0.0.1
        ("64:ff9b::5db8:d70e", True),  # NAT64 to a public address
        ("::7f00:1", False),  # IPv4-compatible 127.0.0.1
        ("2002:7f00:1::", False),  # 6to4 of 127.0.0.1
        ("::ffff:10.1.2.3", False),
    ],
)
def test_ipv6_forms_that_carry_an_ipv4_address(address, public_):
    assert readers.is_public(address) is public_


async def test_a_malformed_link_is_a_read_error(public):
    with pytest.raises(ReadError, match="not a valid link"):
        await readers.read("http://exa\tmple.com/", None, transport=transport({}))


# -- limits and edge cases --------------------------------------------------------------------------


def test_one_huge_pdf_page_is_cut_to_the_budget(monkeypatch):
    monkeypatch.setattr(readers, "MAX_CHARS", 30)
    m = readers.read_pdf(make_pdf(["x" * 200, "next"]), "a.pdf")
    assert m.shown == (1, 1) and m.asked_end == 2
    assert len(m.text) < 30 + 100 and "cut here" in m.text


def test_pdf_pages_start_at_one():
    with pytest.raises(ReadError, match="numbered from 1"):
        readers.read_pdf(make_pdf(["one"]), "a.pdf", "0-0")


def test_pdf_remembers_the_end_asked_for():
    m = readers.read_pdf(make_pdf(["a", "b", "c", "d"]), "a.pdf", "2-3")
    assert (m.shown, m.asked_end) == ((2, 3), 3)


def test_scanned_pdf_has_no_text_but_an_unreadable_page_is_not_that():
    assert readers.read_pdf(make_pdf(["", ""]), "scan.pdf").no_text
    assert not readers.read_pdf(make_pdf(["words", ""]), "mixed.pdf").no_text


def test_pages_and_part_each_belong_to_one_kind_of_source():
    with pytest.raises(ReadError, match="pages= is for PDFs"):
        readers.from_bytes(b"text", "text/plain", "a.txt", pages="1-2")
    with pytest.raises(ReadError, match="part= is for web pages"):
        readers.from_bytes(make_pdf(["one"]), "application/pdf", "a.pdf", part=2)


def test_a_code_block_cut_between_parts_is_closed_and_reopened(monkeypatch):
    monkeypatch.setattr(readers, "MAX_CHARS", 60)
    text = "intro\n\n```\n" + "\n".join(f"line {i}" for i in range(20)) + "\n```"
    first = readers.from_bytes(text.encode(), "text/markdown", "a.md")
    parts = [readers.from_bytes(text.encode(), "text/markdown", "a.md", part=n).text for n in range(1, first.parts + 1)]
    assert len(parts) > 2 and all(p.count("```") % 2 == 0 for p in parts)
    assert parts[2].startswith("```\n")  # a piece in the middle of the code block is still code


def test_an_unusable_charset_falls_back_to_utf8():
    assert readers.from_bytes(b"hello", "text/plain; charset=idna", "a.txt").text == "hello"


def test_titles_are_one_line_and_capped():
    title, _ = html_to_markdown("<title>" + "Long\ntitle " * 100 + "</title><p>x</p>")
    assert "\n" not in title
    m = readers.from_bytes(("<title>" + "word " * 100 + "</title><p>x</p>").encode(), "text/html", "a.html")
    assert len(m.title) <= readers.MAX_TITLE


def test_many_nested_articles_parse_quickly():
    import time

    html = "<article>" * 255 + "<b>x</b>" * 20000 + "</article>" * 255
    t0 = time.monotonic()
    html_to_markdown(html)
    assert time.monotonic() - t0 < 5


async def test_parsing_runs_off_the_event_loop(project, monkeypatch):
    import threading

    where = []
    real = readers.from_bytes

    def spy(*a, **kw):
        where.append(threading.current_thread() is threading.main_thread())
        return real(*a, **kw)

    monkeypatch.setattr(readers, "from_bytes", spy)
    await readers.read("docs/notes.txt", project)
    assert where == [False]
