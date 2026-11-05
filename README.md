<h1 align="center">¡CRAM!</h1>

<p align="center">
  <b>A study clock for your terminal.</b><br>
  Start a block for a class, add details, stop. See your hours per day, week and class.<br>
  A sibling of <a href="https://github.com/ricozama121803/pnch">pnch</a>, with Dewey the dolphin.
</p>

---

## Install

Needs **Python 3.11+**.

```sh
pipx install -e ./CramDesk
cram --install-completion      # optional: tab completion (open a new terminal after)
```

## Commands

```sh
cram                           # the app: everything below, plus views, editing and settings

cram create class Statics      # several at once: cram create class Statics, Calc III
cram ls-class                  # your classes, hours this week and in total

cram start statics             # start a block (no class given: it asks which)
cram add details "ch 5 problems"   # details for the block you're in
cram stop
cram add block                 # log a past block you forgot (asks class, day, start, end)

cram log statics "finished ch 5, moments still shaky"   # what you did (class optional while studying)
cram ls-log [statics]          # one class's log, or every class's

cram add memo "email the TA"   # the memo board, no reminders
cram add memo "statics: review the slides"    # 'class:' in front tags it (or: -c statics)
cram ls-memo
```

Class names can be shortened (`cram start stat`), and `tab` completes them.

## The app

Run `cram`. The bottom bar takes the same words: `start statics`, `stop`, `add details …`,
`log …`, `add memo …`, `add block`, `create class …`, `undo`.

- **details** under the clock: type what you're covering as you go.
- **memo** panel: the memo board. Press `enter` on it to check memos off.
- **today** and **week** panels: hours per day and per class. `enter` opens either one.
- `/` lists the views (`/day`, `/week`, `/log`, `/memo`, `/classes`, `/config`), and `f1` shows every key.

## Files

| Path | What's in it |
| --- | --- |
| `~/.config/cram/config.toml` | Theme and display settings |
| `~/.local/share/cram/data.json` | Your blocks, classes, log and memos (plus a `.bak` and undo history) |

cram keeps its own files, separate from pnch. Set `CRAM_HOME=/some/folder` to keep everything in one place.

## Development

```sh
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

## License

[MIT](LICENSE) © Enrico Zamarripa
