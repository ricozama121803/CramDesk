"""Record the demo video from the real app, using made-up data.

    .venv/bin/pip install -e '.[dev]'
    pip install playwright            # any Python works for the render step; it uses your Chrome
    .venv/bin/python scripts/demo_video.py record /tmp/cram-demo
    python scripts/demo_video.py render /tmp/cram-demo [music.mp3]   # -> docs/demo.mp4 + docs/demo.gif

`record` drives cram with scripted key presses in a throwaway CRAM_HOME (your own log and
config are never touched) and saves an SVG per frame plus how long each one stays on screen.
It covers the full-screen app and then the `cram <command>` shell commands.
`render` turns those into PNGs with headless Chrome and stitches them with ffmpeg. Give it an
audio file and that becomes the soundtrack of the mp4 (fading out at the end); the gif stays silent.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 29, 15, 12, 0)   # a Thursday afternoon
SIZE = (120, 40)
W, H = 1920, 1080
TYPE = 0.06                                # seconds per typed character


# ── record ───────────────────────────────────────────────────────────────────

class Tape:
    """Collects frames: an SVG of the screen (or a title card) and how long it shows."""

    def __init__(self, out: Path):
        self.out = out
        self.frames: list[dict] = []
        self.app = self.pilot = None

    def card(self, title: str, sub: str = "", secs: float = 2.2) -> None:
        self.frames.append({"card": [title, sub], "secs": secs})

    async def snap(self, secs: float) -> None:
        await self.pilot.pause()
        svg = self.app.export_screenshot(title="cram")
        name = hashlib.sha1(svg.encode()).hexdigest()[:16] + ".svg"
        (self.out / name).write_text(svg)
        if self.frames and self.frames[-1].get("svg") == name:
            self.frames[-1]["secs"] += secs
        else:
            self.frames.append({"svg": name, "secs": secs})

    async def wait(self, secs: float, step: float = 0.5) -> None:
        """Hold on screen while the app keeps living (clock ticks, Dewey swims)."""
        left = secs
        while left > 0:
            await self.pilot.pause(min(step, left))
            await self.snap(min(step, left))
            left -= step

    async def keys(self, *keys: str, secs: float = 0.6) -> None:
        for k in keys:
            await self.pilot.press(k)
            await self.snap(secs)

    async def type(self, text: str, enter: bool = True, hold: float = 0.5) -> None:
        for ch in text:
            await self.pilot.press(ch)
            await self.snap(TYPE)
        await self.snap(hold)
        if enter:
            await self.keys("enter", secs=0.7)

    def save(self) -> None:
        (self.out / "frames.json").write_text(json.dumps(self.frames, indent=1))


async def first_run(tape: Tape) -> None:
    from cram.tui import CramApp

    app = CramApp()
    async with app.run_test(size=SIZE, notifications=True) as pilot:
        tape.app, tape.pilot = app, pilot
        await pilot.pause(0.4)
        await tape.wait(2.5)                                   # welcome
        await tape.keys("enter", secs=0.8)
        await tape.type("Alex Rivera")
        await tape.type("Frontend Contractor")
        await tape.type("alex@example.com")
        await tape.type("@alexrivera")
        await tape.type("Acme Robotics")
        await tape.type("ap@acme.example")
        await tape.type("Acme Frontend Contractor")
        app.screen.query_one("#ob-invoice-rate").value = ""
        await tape.type("45")
        await tape.keys("down", "down", "down", "down", secs=0.8)   # theme previews
        await tape.keys("down", secs=1.6)                      # leonida
        await tape.keys("enter", secs=0.4)
        await tape.wait(4)                                     # the tour
        await tape.keys("enter", secs=0.3)
        await tape.wait(2.5)


async def a_few_days_in(tape: Tape, clock) -> None:
    from cram.tui import CramApp

    app = CramApp()
    # PDF previews leave the app for your PDF viewer; here we render the same PDF and show its pages.
    app.run_cli = lambda *args, wait=True: show_pdf(tape, args)
    async with app.run_test(size=SIZE, notifications=True) as pilot:
        tape.app, tape.pilot = app, pilot
        await pilot.pause(0.4)
        await tape.wait(1.5)
        await tape.type("in")                                  # punch in
        await tape.wait(1.2)
        await tape.type("Date picker fix for mobile Safari")
        await tape.type("Checked the iOS layout")
        await tape.wait(1.2)

        # tab flips between the bottom bar and the top; arrows move around up there
        await tape.keys("tab", secs=1.0)
        await tape.keys("tab", secs=1.0)
        await tape.keys("up", "up", secs=0.8)                  # into the items
        await tape.keys("right", secs=1.0)                     # today
        await tape.keys("enter", secs=0.5)                     # open today
        await tape.wait(1)
        await tape.keys("down", "down", secs=0.8)
        await tape.keys("escape", secs=0.8)
        await tape.keys("down", secs=1.0)                      # week
        await tape.keys("enter", secs=0.5)                     # open the week
        await tape.wait(1)
        await tape.keys("down", "down", "down", "down", secs=0.6)
        await tape.keys("left_square_bracket", secs=2.0)       # last week
        await tape.keys("right_square_bracket", secs=1.0)
        await tape.keys("escape", secs=0.8)
        await tape.keys("left", secs=0.8)
        await tape.keys("tab", secs=0.8)

        # forgot to log yesterday's overtime: it overlaps a block, so merge them
        await tape.type("/", enter=False, hold=0.8)            # the slash menu
        await tape.keys("down", "down", secs=0.5)
        await tape.keys("up", "up", secs=0.5)
        await tape.keys("tab", secs=0.6)                       # complete /vday
        await tape.type("yesterday", hold=0.3)
        await tape.wait(1)
        await tape.keys("down", "down", secs=0.6)              # "+ add a block"
        await tape.keys("enter", secs=0.8)
        await tape.type("12pm")
        await tape.type("1:30pm")
        await tape.type("Chart tooltips", enter=False)
        await tape.keys("enter", secs=0.5)
        await tape.wait(3.5)                                   # times overlap: options
        await tape.keys("m", secs=0.5)                         # merge them
        await tape.wait(2.5)
        await tape.keys("escape", secs=2.5)                    # the day, merged
        await tape.keys("escape", secs=0.8)

        tape.card("two hours later", secs=1.6)
        clock.move_to(NOW + timedelta(hours=2, minutes=4))
        await pilot.pause(1.2)                                 # let the clock catch up
        await tape.wait(1.5)
        await tape.type("out")                                 # punch out
        await tape.wait(1)
        await tape.type("Fixed the sticky header on iOS")
        await tape.keys("enter", secs=0.4)                     # empty enter saves
        await tape.wait(2.5)

        tape.card("the PDF", "last week is approved: preview it, then export", secs=2)
        await tape.type("/previewLogs last", enter=False, hold=0.6)
        await tape.keys("enter", secs=1.0)
        await tape.type("/exportLogs last")
        await tape.wait(3.5)


# ── the shell commands ───────────────────────────────────────────────────────

class Shell:
    """A fake terminal: runs `cram <command>` in-process and snaps what a real terminal would show.

    Prompts read from a queue of answers that get "typed" on screen. Lines that cram shows already
    filled in (cram edit) take "+text" to type onto the end, or "" to keep them as they are."""

    COLS, ROWS = SIZE

    def __init__(self, tape: Tape):
        import builtins
        import io

        import rich
        from rich.console import Console

        from cram import cli, ui

        self.tape, self.ui = tape, ui
        self.con = Console(record=True, width=self.COLS, file=io.StringIO(), force_terminal=True,
                           color_system="truecolor", highlight=False)
        ui.console = self.con
        rich._console = self.con            # rich prompts (Confirm, IntPrompt) print here too
        cli._tty = lambda: True
        self.answers: list[str] = []
        self.prefill: str | None = None
        builtins.input = self._input
        try:
            import readline                  # catch what cram edit pre-types into a line
            readline.set_startup_hook = lambda hook=None: setattr(self, "hook", hook)
            readline.insert_text = lambda text: setattr(self, "prefill", text)
        except ImportError:
            pass
        self.hook = None

    def snap(self, secs: float) -> None:
        from rich.console import Console
        from rich.terminal_theme import SVG_EXPORT_THEME
        from rich.color import Color
        from rich.text import Text
        import copy
        import io

        lines = self.con.export_text(clear=False, styles=True).split("\n")[-self.ROWS:]
        lines += [""] * (self.ROWS - len(lines))
        out = Console(record=True, width=self.COLS, file=io.StringIO(), force_terminal=True,
                      color_system="truecolor")
        for line in lines:
            out.print(Text.from_ansi(line, no_wrap=True, overflow="crop"))
        theme = copy.deepcopy(SVG_EXPORT_THEME)
        theme.background_color = Color.parse(self.ui.T.base).triplet
        theme.foreground_color = Color.parse(self.ui.T.text).triplet
        svg = out.export_svg(title="cram · shell", theme=theme)
        name = hashlib.sha1(svg.encode()).hexdigest()[:16] + ".svg"
        (self.tape.out / name).write_text(svg)
        frames = self.tape.frames
        if frames and frames[-1].get("svg") == name:
            frames[-1]["secs"] += secs
        else:
            frames.append({"svg": name, "secs": secs})

    def typed(self, text: str, style: str | None = None) -> None:
        for ch in text:
            self.con.print(ch, end="", style=style or self.ui.T.text)
            self.snap(TYPE)

    def _input(self, prompt: str = "") -> str:
        if self.hook:                        # a line cram shows already filled in
            self.hook()
            self.hook = None
        start, self.prefill = self.prefill or "", None
        if start:
            self.con.print(start, end="")
        self.snap(0.7)
        if not self.answers:
            raise RuntimeError("ran out of answers at:\n" + self.con.export_text(clear=False)[-1500:])
        answer = self.answers.pop(0)
        if start:
            answer = start + answer[1:] if answer.startswith("+") else start
            self.typed(answer[len(start):])
        else:
            self.typed(answer)
        self.snap(0.35)
        self.con.print()
        return answer

    def run(self, cmd: str, *answers: str, hold: float = 2.5, clear: bool = True) -> None:
        import shlex

        from cram.cli import app

        if clear:
            self.con.export_text(clear=True)   # a fresh screen per command reads better on video
        self.con.print(f"~/work ", end="", style=self.ui.T.dim)
        self.con.print("❯ ", end="", style=f"bold {self.ui.T.accent}")
        self.snap(0.5)
        self.typed(cmd)
        self.snap(0.4)
        self.con.print()
        self.answers = list(answers)
        try:
            app(shlex.split(cmd)[1:], standalone_mode=False)
        except SystemExit:
            pass
        assert not self.answers, f"unused answers for {cmd!r}: {self.answers}"
        self.snap(hold)


def shell_commands(tape: Tape, clock) -> None:
    sh = Shell(tape)
    sh.run("cram status", hold=3.5)
    sh.run('cram in "Reviewing PR feedback"', hold=2)
    sh.run('cram add "Answered the review comments"', hold=1.5)
    sh.run("cram v-block", hold=3)

    tape.card("forty minutes later", secs=1.4)
    clock.move_to(NOW + timedelta(hours=2, minutes=50))
    sh.run('cram out "Merged the PR"', hold=2.5)

    # off the clock: cram add asks which block from today or this week
    sh.run("cram add", "y", "1", "1", "Synced with design on the chart colors", "", hold=2.5)
    sh.run("cram v-today", hold=4)

    # edit what you did, in place
    sh.run("cram edit", "1", "1", "2", "", "+ and its empty states", "", "", "n", hold=3)
    sh.run("cram v-week", hold=4.5)
    sh.run("cram cycle-theme", hold=1.2)
    sh.run("cram v-today", hold=3)


def show_pdf(tape: Tape, args: tuple) -> None:
    """Stand-in for `cram preview`: build the same PDF and put its pages on screen."""
    if not args or args[0] != "preview":
        return
    import pypdfium2 as pdfium

    from cram import config, pdf, report
    from cram.paths import cache_dir
    from cram.store import Store
    from cram.timeutil import parse_week

    week = args[args.index("-w") + 1] if "-w" in args else None
    cfg = config.load()
    r = report.build(Store.load(), cfg, parse_week(week))
    path = pdf.render(r, cfg, cache_dir() / "preview.pdf", preview=not r.approved)
    pages = []
    for i, page in enumerate(pdfium.PdfDocument(str(path))):
        png = tape.out / f"pdf-{r.monday}-{i}.png"
        page.render(scale=2.2).to_pil().save(png)
        pages.append(png.name)
    tape.frames.append({"pdf": pages, "name": report.export_filename(cfg, r), "secs": 6.5})


def record(out: Path) -> None:
    os.environ["CRAM_HOME"] = tempfile.mkdtemp(prefix="cram-demo-")
    os.environ["HOME"] = os.environ["CRAM_HOME"]   # exports land in the throwaway folder too
    sys.path.insert(0, str(ROOT / "scripts"))
    import freezegun

    freezegun.configure(extend_ignore_list=["textual", "asyncio", "rich"])
    from demo_data import seed_log

    out.mkdir(parents=True, exist_ok=True)
    tape = Tape(out)
    with freezegun.freeze_time(NOW, tick=True) as clock:
        tape.card("cram", "a time clock for your terminal")
        tape.card("first run", "a minute of setup")
        asyncio.run(first_run(tape))
        seed_log(running=False)
        from cram.tui import write_heartbeat
        write_heartbeat(clean=True)                            # as if you'd quit with ctrl+q
        tape.card("a few days in", "punch in, log what you do, punch out")
        asyncio.run(a_few_days_in(tape, clock))
        tape.card("from the shell", "every action is also a cram command", secs=2.2)
        shell_commands(tape, clock)
        tape.card("cram", "every week becomes a work log + invoice PDF", secs=3.5)
    tape.save()
    print(f"{len(tape.frames)} frames, {sum(f['secs'] for f in tape.frames):.0f}s → {out}")


# ── render ───────────────────────────────────────────────────────────────────

PAGE = """<!doctype html><meta charset="utf-8"><style>
html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden}}
body{{background:radial-gradient(ellipse at 70% 110%,#ff4fa355,transparent 55%),
radial-gradient(ellipse at 15% -10%,#2ee6d633,transparent 50%),#140a24;display:grid;place-items:center;
font-family:'Fira Code',ui-monospace,monospace}}
.term svg{{height:{th}px;width:auto;display:block;filter:drop-shadow(0 18px 40px #0009)}}
h1{{margin:0;font-size:120px;letter-spacing:4px;text-align:center;
background:linear-gradient(90deg,#ff9a3c,#ff4fa3,#c04dff,#3fc8f2,#2ee6d6);
-webkit-background-clip:text;background-clip:text;color:transparent}}
p{{margin:16px 0 0;font-size:40px;color:#e8d4f5;text-align:center}}
.pdf{{display:flex;flex-direction:column;align-items:center;gap:18px}}
.bar{{font-size:24px;color:#fdeaff;display:flex;gap:28px}}.bar span{{color:#9d82b8}}
.pages{{display:flex;gap:40px}}.pages img{{height:{ph}px;box-shadow:0 18px 40px #000a;border-radius:4px}}
</style>{body}"""


def render(src: Path, music: Path | None = None) -> None:
    from playwright.sync_api import sync_playwright

    frames = json.loads((src / "frames.json").read_text())
    pngs = src / "png"
    pngs.mkdir(exist_ok=True)
    (src / "blank.html").write_text("<!doctype html>")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": W, "height": H})
        for i, f in enumerate(frames):
            png = pngs / f"{i:04d}.png"
            f["png"] = str(png)
            if png.exists():
                continue
            if "pdf" in f:
                pages = "".join(f'<img src="file://{src / p}">' for p in f["pdf"])
                body = (f'<div class="pdf"><div class="bar">{f["name"]}<span>preview · f5 or /previewLogs'
                        f'</span></div><div class="pages">{pages}</div></div>')
            elif "card" in f:
                title, sub = f["card"]
                body = f"<div><h1>{title}</h1><p>{sub}</p></div>"
            else:
                body = f'<div class="term">{(src / f["svg"]).read_text()}</div>'
            page.goto(f"file://{src}/blank.html")    # file:// page so PDF page images can load
            page.set_content(PAGE.format(w=W, h=H, th=H - 80, ph=H - 140, body=body), wait_until="networkidle")
            page.screenshot(path=str(png))
            if i % 25 == 0:
                print(f"  {i}/{len(frames)}")
        browser.close()

    concat = src / "concat.txt"
    lines = []
    for f in frames:
        lines += [f"file '{f['png']}'", f"duration {f['secs']:.3f}"]
    lines.append(f"file '{frames[-1]['png']}'")   # ffmpeg needs the last frame repeated
    concat.write_text("\n".join(lines) + "\n")

    docs = ROOT / "docs"
    mp4, gif = docs / "demo.mp4", docs / "demo.gif"
    ff = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat)]
    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    # Fedora's stock ffmpeg ships OpenH264 instead of x264.
    codec = (["-c:v", "libx264", "-crf", "20", "-preset", "slow"] if " libx264 " in encoders
             else ["-c:v", "libopenh264", "-b:v", "8M", "-maxrate", "12M", "-g", "30"])
    # (a low bitrate leaves faint ghosts of the previous screen in dark, flat areas)
    if music:
        # the track starts with the video and fades out over the last 3 seconds
        length = sum(f["secs"] for f in frames)
        aac = "libfdk_aac" if " libfdk_aac " in encoders else "aac"
        subprocess.run([*ff, "-i", str(music), "-map", "0:v:0", "-map", "1:a:0",
                        "-vf", "fps=30,format=yuv420p", *codec,
                        "-af", f"asetpts=PTS-STARTPTS,afade=t=out:st={length - 3:.2f}:d=3",
                        "-c:a", aac, "-b:a", "192k", "-t", f"{length:.2f}", "-movflags", "+faststart", str(mp4)],
                       check=True)
    else:
        subprocess.run([*ff, "-vf", "fps=30,format=yuv420p", *codec, "-movflags", "+faststart", str(mp4)], check=True)
    subprocess.run([*ff, "-vf", "fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];"
                    "[b][p]paletteuse=dither=bayer:bayer_scale=4", str(gif)], check=True)
    for p in (mp4, gif):
        print(f"  {p.relative_to(ROOT)}  {p.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("record", "render") or (sys.argv[1] == "record" and len(sys.argv) != 3):
        sys.exit(__doc__)
    if sys.argv[1] == "record":
        record(Path(sys.argv[2]))
    else:
        render(Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv) > 3 else None)
