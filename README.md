<h1 align="center">¡CRAM!</h1>

<p align="center">
  <b>A study clock for your terminal.</b><br>
  Start a session, jot down what you're studying, stop.<br>
  A sibling of <a href="https://github.com/ricozama121803/pnch">pnch</a>, with Dewey the dolphin in place of Bucky.
</p>

---

## Install

Needs **Python 3.11+**.

```sh
pipx install -e ./CramDesk
cram
```

The first launch asks for your classes and a color theme. Press `esc` to skip.

**Tab completion** (bash, zsh, fish): run this once, then open a new terminal.

```sh
cram --install-completion
```

`tab` then completes commands, class names one word at a time (`cram in lin<tab>` → `linear`,
`al<tab>` → `algebra`), to-do numbers (`cram todo done <tab>`), weeks (`-w <tab>`) and days.

## Classes

Add the classes you're taking, then say which one you're studying when you start:

```sh
cram class add Linear Algebra, Chemistry, US History
cram in linear algebra             # full name
cram in linear                     # or the start of it
cram in la ch 5 review             # or its initials; the rest becomes a note
```

If what you type after `in` isn't a class, cram asks whether to add it. A short name that fits two
classes (`chem` for Chemistry and Chem Lab) is refused, so type a bit more.

```sh
cram classes                       # every class: hours this week, total, last studied
cram class rename "US History" "American History"
cram class rm chemistry            # past sessions keep their class
cram edit 3 -c chemistry           # change a session's class
```

In the app, type `in la` in the bottom bar, and use `/classes` to add or remove classes.
The week panel and `cram v-week` show hours per class.

## To-do

Notes to self for the week: no reminders, just a list of what to do later.

```sh
cram todo add finish problem set 4
cram todo add chem read ch 7        # starts with a class: tagged Chemistry
cram todo add -w next study for midterm
cram todo                           # this week's list (numbered)
cram todo done 2                    # check it off (cram todo undo 2 un-checks it)
cram todo rm 3
cram todo clear                     # delete this week's checked-off ones
```

Anything you didn't finish shows up under "still open from earlier weeks", so nothing gets lost.
In the app, type `/todo`. `enter` adds, `tab` moves to the list, `enter` or `space` checks off, `delete` removes.

## From the shell

```sh
cram in linear algebra             # start studying
cram add "eigenvectors, ch. 5"     # note what you're covering
cram out --at 5:15pm               # forgot to stop
cram v-today                       # today's timeline, sessions and hours per class
cram v-week                        # hours per day and per class, every session (-w last)
cram edit                          # fix a session's times, notes or class
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
