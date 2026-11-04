"""User config (TOML). Written with sensible defaults on first run."""
from __future__ import annotations

import tomllib

from .paths import config_file

DEFAULT_TOML = '''\
# cram config. Edit freely:  cram config --edit

[ui]
theme = "catppuccin"                    # catppuccin | nord | gruvbox | tokyonight | rosepine | leonida
banner = true                           # show the ¡CRAM! banner on the dashboard
mascot = true                           # show Dewey the dolphin next to the banner
'''


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            out[k] = _merge(base[k], v)
        else:
            out[k] = v
    return out


def defaults() -> dict:
    return tomllib.loads(DEFAULT_TOML)


def load() -> dict:
    path = config_file()
    if not path.exists():
        # Brand-new install: the app walks you through setup once (see OnboardingScreen).
        path.write_text(DEFAULT_TOML.replace("[ui]\n", "[ui]\nonboarded = false                       # set by the first-run setup\n", 1))
    defaults = tomllib.loads(DEFAULT_TOML)
    with path.open("rb") as f:
        user = tomllib.load(f)
    return _merge(defaults, user)


_VALUE = r'"(?:[^"\\]|\\.)*"|[^\s#]+'


def set_value(section: str, key: str, value) -> None:
    """Change one value in config.toml, keeping comments and layout."""
    import json
    import re

    path = config_file()
    load()  # make sure the file exists
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, (int, float)):
        rendered = str(int(value)) if float(value).is_integer() else str(value)
    else:
        rendered = json.dumps(str(value))
    lines = path.read_text().splitlines()
    head = next((i for i, ln in enumerate(lines) if ln.strip() == f"[{section}]"), None)
    if head is None:
        lines += ["", f"[{section}]", f"{key} = {rendered}"]
    else:
        end = next((i for i in range(head + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        pat = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)({_VALUE})(.*)$")
        for i in range(head + 1, end):
            m = pat.match(lines[i])
            if m:
                lines[i] = m[1] + rendered + m[3]
                break
        else:
            lines.insert(head + 1, f"{key} = {rendered}")
    path.write_text("\n".join(lines) + "\n")
    load()  # raises if we somehow broke it
