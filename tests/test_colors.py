import pytest

from cram import termbg, ui

BACKGROUNDS = ["#ffffff", "#fdf6e3", "#c0c0c0", "#808080", "#3b4252", "#000000"]


@pytest.mark.parametrize("bg", BACKGROUNDS)
@pytest.mark.parametrize("name", list(ui.THEMES))
def test_every_color_reads_on_any_background(name, bg):
    ui.set_theme(name, background=bg)
    try:
        for role, need in ui.MIN_CONTRAST.items():
            v = getattr(ui.T, role)
            for c in v if isinstance(v, list) else [v]:
                assert ui.contrast(c, bg) >= need, (role, c)
        for color in (ui.T.ok, ui.T.dim):     # status chips: text on a colored block
            fg = max((ui.T.base, ui.T.text), key=lambda c: ui.contrast(c, color))
            assert ui.contrast(fg, color) >= 3
    finally:
        ui.set_theme("catppuccin")


def test_full_screen_app_keeps_theme_colors():
    ui.set_theme("nord")
    assert ui.T.text == ui.THEMES["nord"]["text"]


def test_background_setting(monkeypatch):
    monkeypatch.delenv("COLORFGBG", raising=False)
    assert termbg.background("light") == termbg.LIGHT
    assert termbg.background("dark") == termbg.DARK
    assert termbg.background("#FDF6E3") == "#fdf6e3"
    monkeypatch.setenv("COLORFGBG", "0;15")
    assert termbg.background("auto") == termbg.LIGHT       # not a terminal here, so COLORFGBG decides
    monkeypatch.setenv("COLORFGBG", "15;default;0")
    assert termbg.background("auto") == termbg.DARK


def test_osc11_reply_parsing():
    assert termbg._parse_osc11(b"\x1b]11;rgb:ffff/f6f6/e3e3\x1b\\\x1b[?62;22c") == "#fff6e3"
    assert termbg._parse_osc11(b"\x1b]11;rgb:1e/1e/2e\x07") == "#1e1e2e"
    assert termbg._parse_osc11(b"\x1b[?62;22c") is None
