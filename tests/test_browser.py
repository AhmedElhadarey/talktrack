"""Fetching Chromium on first need, with Playwright's installer and the browser faked."""

import asyncio
import time

import pytest

from talktrack import browser, capture, doctor


class FakePlaywright:
    """`pw.chromium.launch()` fails like a missing browser until the faked installer has run."""

    def __init__(self, world):
        self.world = world
        self.chromium = self

    async def launch(self):
        if not self.world["on_disk"]:
            raise RuntimeError("BrowserType.launch: Executable doesn't exist at /x\nRun `playwright install`")
        return "browser"


@pytest.fixture
def world(monkeypatch):
    w = {"on_disk": False, "installs": 0, "exit": 0}

    def run_installer():
        w["installs"] += 1
        time.sleep(0.2)  # long enough for every concurrent launch to be waiting on the lock
        w["on_disk"] = w["on_disk"] or w["exit"] == 0
        return w["exit"], "" if w["exit"] == 0 else "getaddrinfo ENOTFOUND cdn.playwright.dev"

    monkeypatch.delenv("TALKTRACK_AUTO_BROWSER", raising=False)
    monkeypatch.setattr(browser, "_run_installer", run_installer)
    monkeypatch.setattr(browser, "installed", lambda: w["on_disk"])
    monkeypatch.setattr(browser, "_failed", None)
    monkeypatch.setattr(browser.status, "state", "idle")
    return w


async def test_four_concurrent_launches_share_one_download(world):
    pw = FakePlaywright(world)
    got = await asyncio.gather(*(capture.launch(pw) for _ in range(4)))
    assert got == ["browser"] * 4
    assert world["installs"] == 1
    assert browser.status.state == "ready"


async def test_a_browser_on_disk_is_not_fetched(world):
    world["on_disk"] = True
    assert await capture.launch(FakePlaywright(world)) == "browser"
    assert world["installs"] == 0


async def test_opting_out_keeps_the_hint_and_never_downloads(world, monkeypatch):
    monkeypatch.setenv("TALKTRACK_AUTO_BROWSER", "0")
    with pytest.raises(capture.CaptureUnavailable) as e:
        await capture.launch(FakePlaywright(world))
    assert world["installs"] == 0
    assert "TALKTRACK_AUTO_BROWSER=0" in str(e.value) and "playwright install chromium" in str(e.value)


async def test_a_failed_download_gives_the_manual_steps_and_waits_before_retrying(world):
    world["exit"] = 1
    with pytest.raises(capture.CaptureUnavailable) as e:
        await capture.launch(FakePlaywright(world))
    msg = str(e.value)
    assert "ENOTFOUND" in msg and "playwright install chromium" in msg and "--with-deps chromium" in msg
    assert browser.status.state == "error"
    with pytest.raises(capture.CaptureUnavailable):  # offline finishes do not each try a 100 MB download
        await capture.launch(FakePlaywright(world))
    assert world["installs"] == 1


async def test_after_the_wait_a_failed_download_is_tried_again(world, monkeypatch):
    world["exit"] = 1
    with pytest.raises(capture.CaptureUnavailable):
        await capture.launch(FakePlaywright(world))
    world["exit"] = 0
    monkeypatch.setattr(browser, "_failed", (time.monotonic() - browser.RETRY_AFTER_S - 1, "offline"))
    assert await capture.launch(FakePlaywright(world)) == "browser"
    assert world["installs"] == 2


async def test_other_launch_errors_are_not_mistaken_for_a_missing_browser(world):
    class Broken(FakePlaywright):
        async def launch(self):
            raise RuntimeError("sandbox failed")

    with pytest.raises(RuntimeError, match="sandbox failed"):
        await capture.launch(Broken(world))
    assert world["installs"] == 0


def test_doctor_says_the_browser_will_be_fetched(monkeypatch):
    monkeypatch.delenv("TALKTRACK_AUTO_BROWSER", raising=False)
    monkeypatch.setattr(browser, "installed", lambda: False)
    status, _, detail = doctor._browser_check()
    assert status == doctor.OK and "fetched on first need" in detail


def test_doctor_warns_when_fetching_is_off_and_the_browser_is_missing(monkeypatch):
    monkeypatch.setenv("TALKTRACK_AUTO_BROWSER", "0")
    monkeypatch.setattr(browser, "installed", lambda: False)
    status, _, detail = doctor._browser_check()
    assert status == doctor.WARN and "TALKTRACK_AUTO_BROWSER=0" in detail and doctor.INSTALL_BROWSER in detail
