"""Regenerate the README screenshots from the real app, using made-up demo data.

    .venv/bin/pip install -e '.[dev]'
    .venv/bin/python scripts/screenshots.py

Everything happens in a throwaway folder with the clock frozen on a Thursday afternoon,
so your own log and config are never touched.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
NOW = datetime(2026, 10, 29, 15, 12, 30)   # a Thursday afternoon

os.environ["CRAM_HOME"] = tempfile.mkdtemp(prefix="cram-shots-")

import freezegun  # noqa: E402

freezegun.configure(extend_ignore_list=["textual", "asyncio", "rich"])


def seed() -> None:
    from demo_data import seed_config, seed_log

    seed_config()
    seed_log()


async def shoot() -> None:
    from cram import mascot
    from cram.tui import BlockEditor, ConflictScreen, DayScreen, LogScreen, CramApp, WeekPicker

    OUT.mkdir(parents=True, exist_ok=True)
    app = CramApp()
    async with app.run_test(size=(132, 44)) as pilot:
        main = app.main

        async def snap(name: str, mode: str | None = None, frame: int | None = None) -> None:
            await pilot.pause(0.3)
            if mode is not None:
                main._mode, main._frame = mode, frame - 1
                main._mascot_tick()
                await pilot.pause(0.05)
            app.save_screenshot(f"{name}.svg", path=str(OUT))
            print("  saved", name)

        await pilot.pause(0.5)
        main.query_one("#new").focus()
        await snap("main", "fight", 4)                      # mid-punch
        app.apply_theme("leonida")
        await snap("theme-leonida", "fight", 12)
        app.apply_theme("catppuccin")

        main.query_one("#cmd").focus()
        await pilot.press("/", "down", "down")
        await snap("slash-menu", "fight", 26)
        await pilot.press("escape")
        main.query_one("#cmd").value = ""

        await pilot.press(*"out", "enter")
        await pilot.press(*"Fixed the sticky header on iOS")
        await snap("punch-out")
        main.query_one("#new").value = ""
        app.screen.query_one("#extra").value = ""
        await pilot.press("escape")

        app.push_screen(DayScreen(date(2026, 10, 28)))
        await snap("day")
        await pilot.press("escape")

        app.push_screen(LogScreen(date(2026, 10, 26)))
        await snap("week")
        await pilot.press("escape")

        app.push_screen(WeekPicker())
        await pilot.pause(0.2)
        await pilot.press("up")
        await snap("weeks")
        await pilot.press("escape")

        app.push_screen(DayScreen(date(2026, 10, 27)))
        await pilot.pause(0.2)
        await pilot.press("down", "down", "enter")
        await pilot.press(*"9:30am", "enter", *"10:30am", "enter", *"Quick sync", "enter")
        await snap("overlap")
        await pilot.press("escape", "escape", "escape")

        await pilot.press(*"/config", "enter")
        await snap("config")
        await pilot.press("escape")

    # off the clock: Dewey asleep
    from cram.store import Store
    st = Store.load()
    st.running().end = datetime(2026, 10, 29, 15, 10)
    st.save(record=False)
    app = CramApp()
    async with app.run_test(size=(132, 44)) as pilot:
        await pilot.pause(0.5)
        app.main._mode, app.main._frame = "sleep", 3
        app.main._mascot_tick()
        await pilot.pause(0.1)
        app.save_screenshot("off-the-clock.svg", path=str(OUT))
        print("  saved off-the-clock")


def pdf_pages() -> None:
    import pypdfium2 as pdfium

    from cram import config, pdf, report
    from cram.store import Store

    r = report.build(Store.load(), config.load(), date(2026, 10, 12))
    path = pdf.render(r, config.load(), Path(os.environ["CRAM_HOME"]) / "demo.pdf")
    doc = pdfium.PdfDocument(str(path))
    for i, name in enumerate(["pdf-log", "pdf-invoice"]):
        doc[i].render(scale=1.4).to_pil().save(OUT / f"{name}.png")
        print("  saved", name)


if __name__ == "__main__":
    with freezegun.freeze_time(NOW, tick=True):
        seed()
        asyncio.run(shoot())
        pdf_pages()
    print(f"screenshots in {OUT}")
    sys.exit(0)
