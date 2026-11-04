<h1 align="center">¡CRAM!</h1>

<p align="center">
  <b>A study clock for your terminal.</b><br>
  Start a session, jot down what you're studying, stop.<br>
  A sibling of <a href="https://github.com/ricozama121803/pnch">pnch</a>, with Dewey the dolphin in place of Bucky.
</p>

---

> **Work in progress.** cram is a copy of pnch with invoices and billing taken out.
> Per-topic study totals are next.

## Install

Needs **Python 3.11+**.

```sh
pipx install -e ./CramDesk
cram
```

## From the shell

```sh
cram in linear algebra             # start studying (the topic is optional)
cram add "eigenvectors, ch. 5"     # note what you're covering
cram out --at 5:15pm               # forgot to stop
cram v-today                       # today's timeline and sessions
cram v-week                        # hours per day, every session, totals (-w last)
cram edit                          # fix a session's times or notes
cram undo                          # cram redo puts it back
cram status                        # studying or not, hours today, the week
cram weeks                         # hours studied in each recent week
```

See `man cram` for everything.

## Files

| Path | What's in it |
| --- | --- |
| `~/.config/cram/config.toml` | Your settings and theme |
| `~/.local/share/cram/data.json` | The log (plus a `.bak` and undo history) |

cram keeps its own files, separate from pnch. Set `CRAM_HOME=/some/folder` to keep everything in one place.

## Development

```sh
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

## License

[MIT](LICENSE) © Enrico Zamarripa
