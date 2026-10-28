"""`talktrack doctor` rows that need no models or network."""

from talktrack import __version__, doctor


def test_slide_checks_row_names_what_is_off_and_how_to_add_the_browser(monkeypatch):
    monkeypatch.setattr(doctor, "_chromium", lambda: "")
    status, name, detail = doctor._browser_check()
    assert (status, name) == (doctor.WARN, "slide checks")
    for part in ("not draw the slides", "layout findings", "pictures of alike slides", "poster", "MP4"):
        assert part in detail
    assert doctor.INSTALL_VIDEO in detail and doctor.INSTALL_BROWSER in detail
    # the install commands are pinned to this release, like the plain one
    assert f"@v{__version__}" in doctor.INSTALL_BROWSER and f"@v{__version__}" in doctor.INSTALL


def test_slide_checks_row_is_ok_with_the_browser(monkeypatch):
    monkeypatch.setattr(doctor, "_chromium", lambda: "/browsers/chrome")
    assert doctor._browser_check()[:2] == (doctor.OK, "slide checks")


def test_mp4_row_names_the_missing_tool(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda x: None if x == "ffprobe" else "/usr/bin/" + x)
    status, name, detail = doctor._ffmpeg_check()
    assert (status, name) == (doctor.WARN, "mp4 export") and "ffprobe" in detail and "ffmpeg (" not in detail
