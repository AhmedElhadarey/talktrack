"""Store and service: sessions, voicing, revising, finishing, persistence and change events."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from showntell.service import LocalService
from showntell.speech.fake import FakeBackend
from showntell.speech.narrator import Narrator
from showntell.store import Store

OPENING = "# How caching works\n> A read-through cache\n\nEvery request checks a small cache first.\n"
BODY = (
    "## The lookup {#lookup}\n\n```python\ndef get(self, key):\n    return self._data[key]\n```\n\n"
    "The method [returns the value](#L2).\n\nIt never touches the database.\n"
)


async def started(service: LocalService, **kw) -> dict:
    args = {"title": "Caching", "frame": "landscape", "voice": "", "speed": 1.0, "project_dir": None, **kw}
    return await service.start(**args)


# -- creating ---------------------------------------------------------------------------------------


async def test_start_returns_handle_and_url(service, store):
    r = await started(service)
    wid, _, key = r["handle"].partition(".")
    assert r["url"] == f"http://localhost:4799/w/{wid}" and len(wid) >= 16 and len(key) >= 16
    assert (store.walk_dir(wid) / "state.json").is_file()


async def test_start_resolves_project_dir(service, tmp_path):
    r = await started(service, project_dir=str(tmp_path))
    assert r["root"] == str(tmp_path.resolve())


async def test_start_rejects_missing_project_dir(service, tmp_path):
    with pytest.raises(ValueError, match="not a folder"):
        await started(service, project_dir=str(tmp_path / "nope"))


async def test_create_validates_frame_and_voice(store):
    with pytest.raises(ValueError, match="frame"):
        store.create("T", frame="wide")
    with pytest.raises(ValueError, match="unknown voice"):
        store.create("T", voice="robot")


async def test_create_clamps_speed_and_defaults_title(store):
    assert store.create("", speed=3).w.speed == 1.3
    assert store.create("T", speed=0.1).w.speed == 0.8
    assert store.create("").w.title == "Untitled walkthrough"


async def test_ids_and_keys_are_unique(store):
    a, b = store.create("A"), store.create("B")
    assert a.w.id != b.w.id and a.w.key != b.w.key and a.w.report_key != b.w.report_key


# -- the opening -------------------------------------------------------------------------------------


async def test_opening_is_playable_quickly(service):
    r = await started(service)
    a = await service.add(r["handle"], OPENING)
    assert (a["slides"], a["says"], a["total_slides"]) == (1, 1, 1)
    t0 = time.monotonic()
    p = await service.playable(r["handle"], 5)
    assert p["status"] == "ready" and p["url"] == r["url"]
    assert time.monotonic() - t0 < 1.0


async def test_playable_marks_link_shared_and_sends_the_guide_once(service, store):
    r = await started(service)
    await service.add(r["handle"], OPENING)
    first = await service.playable(r["handle"], 5)
    second = await service.playable(r["handle"], 5)
    assert first["guide"] is True and second["guide"] is False
    s = store.by_handle(r["handle"])
    assert s.w.link_shared and s.w.guide_sent
    assert (await service.add(r["handle"], BODY))["link_shared"] is True


async def test_playable_without_narration_is_empty(service):
    r = await started(service)
    await service.add(r["handle"], "# Title only\n> subtitle\n")
    p = await service.playable(r["handle"], 0.1)
    assert p["status"] == "empty" and "guide" not in p


async def test_playable_times_out_while_audio_is_pending(tmp_path, idle_narrator):
    store = Store(tmp_path / "data", "http://localhost:4799", idle_narrator)
    store.loop = asyncio.get_running_loop()
    service = LocalService(store)
    r = await started(service)
    await service.add(r["handle"], OPENING)
    p = await service.playable(r["handle"], 0.05)
    assert p["status"] == "pending" and not store.by_handle(r["handle"]).w.link_shared


async def test_audio_fills_in_duration_words_and_cue_times(service, store, voiced):
    r = await started(service)
    await service.add(r["handle"], OPENING + "\n" + BODY)
    s = store.by_handle(r["handle"])
    await voiced(s)
    say = s.w.slides[1].says[0]
    assert say.duration > 0 and say.words and say.key
    assert say.cues[0]["t"] > 0
    assert store.narrator.audio_path(say.key).is_file()


async def test_add_reports_progress_and_issues(service, store, voiced):
    r = await started(service)
    await service.add(r["handle"], OPENING)
    a = await service.add(r["handle"], BODY + "\nAlso [nowhere](#L99).\n")
    assert a["slides"] == 1 and a["total_slides"] == 2 and a["slide_ids"] == ["how-caching-works", "lookup"]
    assert any("points past the code" in i for i in a["issues"])
    assert a["narration_s"] > 0 and a["total_says"] == 4


# -- revising ----------------------------------------------------------------------------------------


async def test_revise_keeps_id_bumps_rev_and_revoices_only_changes(service, store, voiced):
    r = await started(service)
    await service.add(r["handle"], OPENING + "\n" + BODY)
    s = store.by_handle(r["handle"])
    await voiced(s)
    jobs = store.narrator.stats["jobs"]
    new = BODY.replace("It never touches the database.", "It only reads from memory.")
    v = await service.revise(r["handle"], "lookup", new)
    assert (v["slide"], v["rev"], v["says"], v["revoiced"]) == ("lookup", 2, 2, 1)
    slide = s.w.slides[1]
    assert slide.id == "lookup" and slide.rev == 2 and [x.id for x in slide.says] == ["lookup.1.2", "lookup.2.2"]
    assert slide.says[0].status == "ready"  # unchanged paragraph came from the cache at once
    await voiced(s)
    assert store.narrator.stats["jobs"] == jobs + 1


async def test_revise_without_heading_keeps_the_title(service, store):
    r = await started(service)
    await service.add(r["handle"], OPENING + "\n" + BODY)
    await service.revise(r["handle"], "lookup", "Only new words here.")
    slide = store.by_handle(r["handle"]).w.slides[1]
    assert (slide.title, slide.kind, slide.says[0].text) == ("The lookup", "text", "Only new words here.")


async def test_revise_without_heading_keeps_classes(service, store):
    r = await started(service)
    await service.add(r["handle"], OPENING + "\n## Tiers {#tiers .cards}\n\n- **Memory**: fast\n- **Disk**: slow\n\nTwo tiers.\n")
    await service.revise(r["handle"], "tiers", "- **Memory**: fast\n- **Disk**: slow\n\nTwo tiers, revised.\n")
    slide = store.by_handle(r["handle"]).w.slides[1]
    assert slide.classes == ["cards"] and slide.visual["style"] == "cards"


async def test_revise_unknown_slide_names_the_slides(service):
    r = await started(service)
    await service.add(r["handle"], OPENING)
    with pytest.raises(ValueError, match="how-caching-works"):
        await service.revise(r["handle"], "nope", "Words.")


async def test_revise_with_two_slides_keeps_the_first(service, store):
    r = await started(service)
    await service.add(r["handle"], OPENING + "\n" + BODY)
    v = await service.revise(r["handle"], "lookup", "## A\n\nOne.\n\n## B\n\nTwo.\n")
    assert any("kept the first of 2" in i for i in v["issues"])
    assert [s.id for s in store.by_handle(r["handle"]).w.slides] == ["how-caching-works", "lookup"]


# -- finishing ---------------------------------------------------------------------------------------


async def test_finish_sets_questions_and_status(service, store):
    r = await started(service)
    await service.add(r["handle"], OPENING)
    f = await service.finish(r["handle"], ["  Why a cache? ", "", "What about eviction?"])
    assert f["questions"] == ["Why a cache?", "What about eviction?"] and f["slides"] == 1
    assert store.by_handle(r["handle"]).w.status == "finished"


async def test_finish_keeps_questions_from_a_questions_slide(service):
    r = await started(service)
    await service.add(r["handle"], OPENING + "\n## Q {.questions}\n\n- One?\n- Two?\n")
    assert (await service.finish(r["handle"], None))["questions"] == ["One?", "Two?"]


async def test_finish_caps_questions_at_four(service):
    r = await started(service)
    await service.add(r["handle"], OPENING)
    assert len((await service.finish(r["handle"], [f"Q{i}?" for i in range(6)]))["questions"]) == 4


async def test_finish_reports_last_slide_problems(service):
    r = await started(service)
    await service.add(r["handle"], OPENING + "\n## Silent {#silent}\n\n```python\nx = 1\n```\n")
    f = await service.finish(r["handle"], None)
    assert any("`silent`" in i and "no narration" in i for i in f["issues"])


async def test_finish_reports_and_drops_an_unclosed_fence(service, store):
    r = await started(service)
    await service.add(r["handle"], OPENING + "\n## C\n\n```python\nx = 1\n")
    f = await service.finish(r["handle"], None)
    assert any("never closed" in i for i in f["issues"])
    assert store.by_handle(r["handle"]).builder.parser.pending == ""


async def test_adding_after_finish_reopens(service, store):
    r = await started(service)
    await service.add(r["handle"], OPENING)
    await service.finish(r["handle"], None)
    await service.add(r["handle"], BODY)
    assert store.by_handle(r["handle"]).w.status == "writing"


# -- handles -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("mangle", [
    lambda h: h.split(".")[0] + ".wrongkey",
    lambda h: h.split(".")[0],
    lambda h: "",
    lambda h: "short.key",
    lambda h: "../../etc." + h.split(".")[1],
])
async def test_by_handle_rejects_wrong_keys(store, mangle):
    s = store.create("T")
    with pytest.raises(ValueError, match="unknown handle"):
        store.by_handle(mangle(s.handle))


async def test_by_handle_accepts_the_right_key(store):
    s = store.create("T")
    assert store.by_handle(s.handle) is s and store.by_handle(f"  {s.handle}\n") is s


async def test_service_calls_with_bad_handle_raise(service):
    for call in (service.add("x.y", "a"), service.playable("x.y", 0), service.revise("x.y", "s", "a"),
                 service.finish("x.y", None)):
        with pytest.raises(ValueError):
            await call


# -- persistence --------------------------------------------------------------------------------------


async def test_state_is_saved_and_reloaded_by_a_fresh_store(service, store, tmp_path, voiced):
    r = await started(service)
    await service.add(r["handle"], OPENING + "\n" + BODY)
    s = store.by_handle(r["handle"])
    await voiced(s)
    s.save_now()
    fresh_narrator = Narrator(FakeBackend(), tmp_path / "audio")  # never started: nothing may need voicing
    fresh = Store(tmp_path / "data", "http://localhost:4799", fresh_narrator)
    fresh.loop = asyncio.get_running_loop()
    t = fresh.by_handle(r["handle"])
    assert t is not s
    assert [sl.id for sl in t.w.slides] == [sl.id for sl in s.w.slides]
    assert all(x.status == "ready" for sl in t.w.slides for x in sl.says if x.text)
    assert t.w.slides[1].says[0].cues == s.w.slides[1].says[0].cues
    assert fresh_narrator.backlog() == 0
    assert t.source == s.source and "## The lookup" in t.source
    assert (await LocalService(fresh).playable(r["handle"], 0.1))["status"] == "ready"


async def test_pending_narration_is_queued_again_after_reload(tmp_path, idle_narrator, narrator, voiced):
    first = Store(tmp_path / "data", "http://localhost:4799", idle_narrator)
    first.loop = asyncio.get_running_loop()
    s = first.create("T")
    s.add(OPENING)
    s.save_now()
    assert s.w.slides[0].says[0].status == "pending"
    second = Store(tmp_path / "data", "http://localhost:4799", narrator)
    second.loop = asyncio.get_running_loop()
    t = second.get(s.w.id)
    await voiced(t)
    assert t.w.slides[0].says[0].status == "ready"
    assert (await LocalService(second).playable(t.handle, 1))["status"] == "ready"


async def test_ready_narration_with_missing_audio_is_voiced_again(service, store, tmp_path, voiced):
    r = await started(service)
    await service.add(r["handle"], OPENING)
    s = store.by_handle(r["handle"])
    await voiced(s)
    s.save_now()
    store.narrator.audio_path(s.w.slides[0].says[0].key).unlink()
    fresh = Store(tmp_path / "data", "http://localhost:4799", store.narrator)
    fresh.loop = asyncio.get_running_loop()
    t = fresh.get(s.w.id)
    await voiced(t)
    assert store.narrator.audio_path(t.w.slides[0].says[0].key).is_file()


async def test_audio_cache_key_survives_a_reload(service, store, tmp_path, voiced):
    r = await started(service)
    await service.add(r["handle"], '# T\n\nCall `get_value` and [SQL]{say="sequel"} with `HF_HUB_OFFLINE`.\n')
    s = store.by_handle(r["handle"])
    await voiced(s)
    key = s.w.slides[0].says[0].key
    s.save_now()
    store.narrator.audio_path(key).unlink()  # force a re-voice after the reload
    fresh = Store(tmp_path / "data", "http://localhost:4799", store.narrator)
    fresh.loop = asyncio.get_running_loop()
    t = fresh.get(s.w.id)
    await voiced(t)
    assert t.w.slides[0].says[0].key == key  # JSON lists instead of tuples must not change the cache key


async def test_appending_after_reload_continues_the_walkthrough(service, store, tmp_path):
    r = await started(service)
    await service.add(r["handle"], OPENING)
    store.by_handle(r["handle"]).save_now()
    fresh = Store(tmp_path / "data", "http://localhost:4799", store.narrator)
    fresh.loop = asyncio.get_running_loop()
    a = await LocalService(fresh).add(r["handle"], BODY)
    assert a["slide_ids"] == ["how-caching-works", "lookup"]


async def test_changes_are_saved_after_a_debounce(service, store, wait_until):
    r = await started(service)
    await service.add(r["handle"], OPENING)
    state = store.walk_dir(r["handle"].split(".")[0]) / "state.json"
    await wait_until(lambda: len(json.loads(state.read_text())["slides"]) == 1, timeout=3)


async def test_unreadable_state_is_not_loaded(store):
    d = store.walk_dir("A" * 22)
    d.mkdir(parents=True)
    (d / "state.json").write_text("{not json")
    assert store.get("A" * 22) is None


@pytest.mark.parametrize("wid", ["", "short", "../../etc", "a/b" * 8, "x" * 65])
async def test_get_rejects_malformed_ids(store, wid):
    assert store.get(wid) is None and store.delete(wid) is False


# -- list and delete ---------------------------------------------------------------------------------


async def test_list_shows_recent_walkthroughs_first(service, store):
    a = await started(service, title="First")
    await asyncio.sleep(0.01)
    b = await started(service, title="Second")
    rows = await service.list()
    assert [x["title"] for x in rows] == ["Second", "First"]
    assert rows[1]["handle"] == a["handle"] and rows[0]["url"] == b["url"]
    assert set(rows[0]) >= {"id", "title", "handle", "created", "updated", "status", "frame", "slides", "duration", "url"}


async def test_list_counts_slides_and_duration(service, store, voiced):
    r = await started(service)
    await service.add(r["handle"], OPENING + "\n{pause=1}\n")
    s = store.by_handle(r["handle"])
    await voiced(s)
    s.save_now()
    (row,) = store.list()
    assert row["slides"] == 1 and row["duration"] > 1


async def test_delete_removes_walkthrough(store):
    s = store.create("T")
    assert store.delete(s.w.id) is True
    assert store.get(s.w.id) is None and store.list() == []
    assert store.delete(s.w.id) is False


# -- live updates --------------------------------------------------------------------------------------


async def test_subscribers_hear_about_changed_slides(store):
    s = store.create("T")
    q = s.subscribe()
    s.add(OPENING)
    item = q.get_nowait()
    assert item == {"slides": {"how-caching-works"}, "meta": False}
    s.unsubscribe(q)
    s.add(BODY)
    assert q.empty()


async def test_delta_holds_changed_slides_and_order(service, store):
    r = await started(service)
    await service.add(r["handle"], OPENING + "\n" + BODY)
    s = store.by_handle(r["handle"])
    d = s.delta({"lookup"}, False)
    assert d["type"] == "delta" and d["status"] == "writing" and d["title"] == "Caching"
    assert d["order"] == ["how-caching-works", "lookup"]
    assert [x["id"] for x in d["slides"]] == ["lookup"]
    slide = d["slides"][0]
    assert set(slide) >= {"id", "rev", "kind", "title", "classes", "shown", "visual", "says"}
    assert d["questions"] == [] and d["notes"] == {}


async def test_player_reports_are_deduplicated_and_taken_once(store):
    s = store.create("T")
    s.report("Mermaid  parse\nerror")
    s.report("Mermaid parse error")
    s.report("x" * 500)
    taken = s.take_player_issues()
    assert taken[0] == "Mermaid parse error" and len(taken) == 2 and len(taken[1]) == 300
    assert s.take_player_issues() == []


async def test_speech_jobs_follow_position_in_the_walkthrough(tmp_path, idle_narrator):
    store = Store(tmp_path / "data", "http://localhost:4799", idle_narrator)
    a = store.create("Backlog")
    a.add("# A\n\n" + "\n\n".join(f"Paragraph number {i} of the backlog." for i in range(5)) + "\n")
    b = store.create("New")
    b.add("# B\n\nThe opening of the new one.\n")
    queued = []
    while idle_narrator.backlog():
        priority, _, job = idle_narrator._q.get_nowait()
        queued.append((priority, job.spoken.text))
    assert [p for p, _ in queued][:2] == [0, 0]
    assert any("new one" in t for _, t in queued[:2])
