<h1 align="center">¡CRAM!</h1>

<p align="center">
  <b>A study clock for your terminal.</b><br>
  Start a session, jot down what you're studying, stop.<br>
  A sibling of <a href="https://github.com/ricozama121803/pnch">pnch</a>, with Dewey the dolphin in place of Bucky.
</p>

---

> **Work in progress.** crm is a fresh copy of pnch with the name and mascot swapped.
> The invoice/billing parts are still there and will be replaced with per-topic study totals.

## Install

Needs **Python 3.11+**.

```sh
pipx install -e ./CramDesk
crm
```

## From the shell

```sh
crm in linear algebra              # start studying (the topic is optional)
crm add "eigenvectors, ch. 5"      # note what you're covering
crm out --at 5:15pm                # forgot to stop
crm v-today                        # today's timeline and sessions
crm v-week                         # hours per day, every session, totals (-w last)
crm edit                           # fix a session's times or notes
crm undo                           # crm redo puts it back
crm status                         # studying or not, hours today, the week
```

See `man crm` for everything.

## Files

| Path | What's in it |
| --- | --- |
| `~/.config/crm/config.toml` | Your settings and theme |
| `~/.local/share/crm/data.json` | The log (plus a `.bak` and undo history) |

crm keeps its own files, separate from pnch. Set `CRM_HOME=/some/folder` to keep everything in one place.

## Development

```sh
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

## License

[MIT](LICENSE) © Enrico Zamarripa
