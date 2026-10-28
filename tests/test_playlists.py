"""Playlists: storage, the tool and resource, the page route, and the single-file export's structure."""

from __future__ import annotations

import asyncio
import json
import re
import time

import httpx
import pytest
from mcp.client import Client
from test_http import BASE, RPC_TOKEN

from talktrack import playlists
from talktrack.config import Config
from talktrack.export import export_playlist_html
from talktrack.server import add_routes, create_mcp
from talktrack.service import LocalService
from talktrack.speech.fake import FakeBackend
from talktrack.speech.narrator import Narrator
from talktrack.store import Store

OPENING = "# {t}\n\nEvery request checks a small cache first.\n"


@pytest.fixture
async def shared(tmp_path):
    """A store whose audio cache sits in the data folder, where the exporters read it."""
    n = Narrator(FakeBackend(), tmp_path / "data" / "audio")
    n.start()
    assert n.wait_ready(5)
    s = Store(tmp_path / "data", BASE, n)
    s.loop = asyncio.get_running_loop()
    return s


async def walk(service, store, voiced, title: str) -> dict:
    r = await service.start(title, "landscape", "", 1.0, None)
    await service.add(r["handle"], OPENING.format(t=title))
    s = store.by_handle(r["handle"])
    await voiced(s)
    s.save_now()
    return r


@pytest.fixture
async def two(shared, voiced):
    service = LocalService(shared)
    return service, [await walk(service, shared, voiced, "Part one"), await walk(service, shared, voiced, "Part two")]


# -- storage ------------------------------------------------------------------------------------------


async def test_a_playlist_round_trips_through_the_data_folder(two, shared):
    service, (a, b) = two
    pl = playlists.create(shared.data_dir, "Caching course", [b["url"], a["handle"].split(".")[0]], "  How\ncaching works ")
    assert (shared.data_dir / "playlists" / f"{pl['id']}.json").is_file()
    back = playlists.get(shared.data_dir, pl["id"])  # read from disk, as after a restart
    assert back == pl and back["description"] == "How caching works"
    ids = [a["handle"].split(".")[0], b["handle"].split(".")[0]]
    assert back["walkthroughs"] == [ids[1], ids[0]]  # a link and an id both resolve, in the order given
    rows = playlists.entries(shared.data_dir, back)
    assert [r["title"] for r in rows] == ["Part two", "Part one"] and all(r["duration"] > 0 for r in rows)
    assert [p["id"] for p in playlists.list_all(shared.data_dir)] == [pl["id"]]


async def test_every_call_makes_a_new_playlist(two, shared):
    _, (a, _) = two
    p1 = playlists.create(shared.data_dir, "Same", [a["url"]])
    p2 = playlists.create(shared.data_dir, "Same", [a["url"]])
    assert p1["id"] != p2["id"] and len(playlists.list_all(shared.data_dir)) == 2


async def test_a_deleted_walkthrough_drops_out_of_the_playlist(two, shared):
    _, (a, b) = two
    pl = playlists.create(shared.data_dir, "Course", [a["url"], b["url"]])
    shared.delete(a["handle"].split(".")[0])
    assert [r["title"] for r in playlists.entries(shared.data_dir, pl)] == ["Part two"]


@pytest.mark.parametrize("bad", [[], ["x"] * 51])
async def test_a_playlist_needs_one_to_fifty_walkthroughs(shared, bad):
    with pytest.raises(ValueError, match="at least one|at most"):
        playlists.create(shared.data_dir, "T", bad)


# -- the tool and the resource -------------------------------------------------------------------------


async def test_the_tool_makes_a_playlist_and_the_resource_lists_it(two):
    service, (a, b) = two
    async with Client(create_mcp(service)) as client:
        r = await client.call_tool(
            "create_playlist", {"title": "Course", "walkthroughs": [a["handle"], b["url"]], "description": "Two parts"}
        )
        assert not r.is_error, r.content
        data = r.structured_content
        assert data["url"].startswith(f"{BASE}/p/{data['id']}") and data["url"] in r.content[0].text
        assert [e["title"] for e in data["entries"]] == ["Part one", "Part two"] and data["entries"][1]["url"].endswith("?n=2")
        listed = json.loads((await client.read_resource("talktrack://playlists")).contents[0].text)
        assert [p["id"] for p in listed] == [data["id"]] and listed[0]["entries"][0]["n"] == 1


async def test_unknown_walkthroughs_are_an_error_naming_them(two):
    service, (a, _) = two
    async with Client(create_mcp(service)) as client:
        r = await client.call_tool("create_playlist", {"title": "T", "walkthroughs": [a["handle"], "nope", "A" * 22]})
        assert r.is_error
        msg = r.content[0].text
        assert "nope" in msg and "A" * 22 in msg and a["handle"] not in msg
        assert json.loads((await client.read_resource("talktrack://playlists")).contents[0].text) == []


async def test_file_delivery_tells_the_agent_to_export_not_to_share_a_link(two):
    service, (a, _) = two
    async with Client(create_mcp(service, deliver="file")) as client:
        r = await client.call_tool("create_playlist", {"title": "T", "walkthroughs": [a["url"]]})
        t = r.content[0].text
        assert "export_walkthrough" in t and "Link:" not in t


# -- the page ------------------------------------------------------------------------------------------


@pytest.fixture
def page(two, shared, tmp_path):
    service, ws = two
    cfg = Config(host="127.0.0.1", port=4799, data_dir=tmp_path / "data", public_url="", access_token="")
    mcp = create_mcp(service)
    add_routes(mcp, service, RPC_TOKEN, time.time())
    from talktrack.app import Guard

    app = Guard(mcp.streamable_http_app(streamable_http_path="/mcp", host=cfg.host), cfg)
    pl = playlists.create(shared.data_dir, "Caching <course>", [w["url"] for w in ws], "All about caches")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)), base_url=BASE), pl, ws


async def test_the_route_renders_the_contents_and_points_the_frame_at_each_walkthrough(page):
    client, pl, ws = page
    async with client:
        assert (await client.get(f"/p/{pl['id']}")).status_code == 308
        r = await client.get(f"/p/{pl['id']}/")
        assert r.status_code == 200
        html = r.text
        assert "Caching &lt;course&gt;" in html and "All about caches" in html and "2 walkthroughs" in html
        assert re.findall(r'<span class="t">([^<]+)</span><span class="d">(\d+:\d\d)</span>', html)[0][0] == "Part one"
        assert html.count('<span class="n">') == 2
        data = json.loads(re.search(r'id="playlist-data">(.*?)</script>', html, re.S).group(1))
        assert [e["src"] for e in data["entries"]] == [f"/w/{w['handle'].split('.')[0]}/" for w in ws]
        assert "{{" not in html
        assert (await client.get("/p/" + "x" * 22 + "/")).status_code == 404


# -- the single file -----------------------------------------------------------------------------------


async def test_the_export_is_one_page_carrying_every_walkthrough(two, shared, tmp_path):
    _, (a, b) = two
    pl = playlists.create(shared.data_dir, "Course", [a["url"], b["url"]])
    out = export_playlist_html(shared.data_dir, pl, tmp_path / "course.html")
    html = out.read_text()
    assert "__STATIC__" not in html and 'href="http' not in html and 'src="http' not in html and "<link" in html
    data = json.loads(re.search(r'id="playlist-data">(.*?)</script>', html, re.S).group(1))
    docs = [e["doc"] for e in data["entries"]]
    assert len(docs) == 2 and all("src" not in e for e in data["entries"])
    for doc, title in zip(docs, ("Part one", "Part two"), strict=True):
        assert 'id="talktrack-data"' in doc and 'type="importmap"' in doc and f"<title>{title}</title>" in doc
        assert 'src="static' not in doc and "data:audio/mpeg;base64," in doc  # the narration travels inside it
    assert len(html) < 1_500_000


async def test_export_walkthrough_takes_a_playlist_id(two, shared, tmp_path):
    service, (a, b) = two
    pl = playlists.create(shared.data_dir, "Course", [a["url"], b["url"]])
    async with Client(create_mcp(service)) as client:
        r = await client.call_tool("export_walkthrough", {"handle": pl["id"], "format": "html", "path": str(tmp_path / "c.html")})
        assert not r.is_error, r.content
        assert (tmp_path / "c.html").is_file() and r.structured_content["duration_s"] > 0
        r = await client.call_tool("export_walkthrough", {"handle": pl["id"], "format": "mp4", "path": str(tmp_path / "c.mp4")})
        assert r.is_error and "html only" in r.content[0].text


async def test_the_command_line_exports_a_playlist_from_its_link(two, shared, tmp_path, capsys):
    from types import SimpleNamespace

    from talktrack.export import export_cli

    _, (a, b) = two
    pl = playlists.create(shared.data_dir, "Course", [a["url"], b["url"]])
    cfg = Config(host="127.0.0.1", port=4799, data_dir=shared.data_dir, public_url="", access_token="")
    export_cli(cfg, SimpleNamespace(source=f"{BASE}/p/{pl['id']}/?n=2", out=str(tmp_path / "c.html"), mp3=False))
    assert 'id="playlist-data"' in (tmp_path / "c.html").read_text() and "plays the playlist offline" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="one .html file"):
        export_cli(cfg, SimpleNamespace(source=pl["id"], out=str(tmp_path / "c.mp4"), mp3=False))
