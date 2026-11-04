"""User config (TOML). Written with sensible defaults on first run."""
from __future__ import annotations

import tomllib

from .paths import config_file

DEFAULT_TOML = '''\
# cram config. Edit freely:  cram config --edit

[you]
name = "Your Name"
title = "Contractor"             # log page header reads "Name-Title"
address = ["123 Main St", "Springfield, ST 00000", "UNITED STATES"]
email = "you@example.com"
paypal = "@yourhandle"           # set to "" to hide the PayPal line

[client]
name = "Client Co"
address = ["Accounts Payable", "456 Market St", "Springfield, ST 00000", "UNITED STATES"]
email = "billing@example.com"

[invoice]
doc_title = "Work Logs + Invoice"       # top-right text on the log page
role = "Software Contractor"            # first line of the invoice item
rate = 25                               # $ per hour
first_number = 1001                     # used until you've approved your first week here
due_business_days = 14
billing = "down"                        # how hours become billed hours: down | nearest | up | quarter | exact
footer = [
  "Payment is required within {days} business days of invoice date.",
  "Thank you for your business.",
]

[export]
dir = "~/Documents/cram"
filename = "logsweek{start}-{end}.pdf"  # pick a style in /config, or use: {start} {end} (10_12)
                                        # {start_iso} {end_iso} (2026-10-12) {number} {name} {year}

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
