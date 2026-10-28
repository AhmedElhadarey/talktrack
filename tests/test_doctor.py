"""`talktrack doctor` rows that need no models or network."""

from talktrack import __version__, doctor


def test_install_commands_are_pinned_to_this_release():
    assert f"@v{__version__}" in doctor.INSTALL_BROWSER and f"@v{__version__}" in doctor.INSTALL
    assert "--with-deps" in doctor.INSTALL_BROWSER_LINUX and "video" not in doctor.INSTALL_BROWSER


def test_slide_checks_row_is_ok_with_the_browser(monkeypatch):
    monkeypatch.setattr(doctor, "_chromium", lambda: True)
    assert doctor._browser_check()[:2] == (doctor.OK, "slide checks")


def test_mp4_row_names_the_missing_tool(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda x: None if x == "ffprobe" else "/usr/bin/" + x)
    status, name, detail = doctor._ffmpeg_check()
    assert (status, name) == (doctor.WARN, "mp4 export") and "ffprobe" in detail and "ffmpeg (" not in detail
