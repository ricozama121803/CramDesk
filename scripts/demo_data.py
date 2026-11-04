"""Made-up demo data (a contractor billing a fictional client) for screenshots and the demo video.

Import this only after CRAM_HOME points at a throwaway folder.
"""
from __future__ import annotations

from datetime import datetime


def seed_config() -> None:
    from cram import config

    config.load()
    config.set_value("ui", "onboarded", True)
    for key, value in [("name", "Alex Rivera"), ("email", "alex@example.com"), ("paypal", "@alexrivera")]:
        config.set_value("you", key, value)
    config.set_value("client", "name", "Acme Robotics")
    config.set_value("invoice", "doc_title", "Acme Robotics Logs + Invoice")
    config.set_value("invoice", "role", "Acme Frontend Contractor")


def seed_log(running: bool = True) -> None:
    """Three weeks of blocks ending Thursday 2026-10-29; `running` leaves one open since 13:48."""
    from cram.store import Store

    st = Store.load()
    d = lambda s: datetime.fromisoformat(s)  # noqa: E731
    blocks = [
        # two weeks ago (approved + exported)
        ("2026-10-13 16:00", "2026-10-13 17:03", ["Kickoff meeting"]),
        ("2026-10-14 23:00", "2026-10-15 00:12", ["Set up the Next.js repo and preview deploys"]),
        ("2026-10-15 11:00", "2026-10-15 13:26", ["Design review", "Built the nav + page layout components"]),
        ("2026-10-15 15:00", "2026-10-15 16:30", ["Login and signup forms"]),
        ("2026-10-18 20:00", "2026-10-18 23:09", ["Responsive fixes for tablet", "Updated Storybook"]),
        # last week (approved)
        ("2026-10-19 10:00", "2026-10-19 12:15", ["Sprint planning", "Ticket grooming"]),
        ("2026-10-21 13:30", "2026-10-21 17:45", ["Built the settings page"]),
        ("2026-10-23 09:15", "2026-10-23 11:00", ["Code review", "Fixed flaky UI tests"]),
        # this week
        ("2026-10-26 09:00", "2026-10-26 11:30", ["Standup", "Reviewed PRs"]),
        ("2026-10-26 13:00", "2026-10-26 17:15", ["Dashboard empty states"]),
        ("2026-10-27 10:00", "2026-10-27 12:00", ["Accessibility pass on forms"]),
        ("2026-10-27 19:00", "2026-10-27 21:40", ["Dark mode color tokens"]),
        ("2026-10-28 09:30", "2026-10-28 12:45", ["Dashboard charts"]),
        ("2026-10-28 14:00", "2026-10-28 18:00", ["Dashboard filters", "Wrote component tests"]),
        ("2026-10-29 09:00", "2026-10-29 12:10", ["Client call", "Spec for the invoices page"]),
    ]
    for start, end, notes in blocks:
        st.add(d(start), d(end), notes)
    if running:
        st.add(d("2026-10-29 13:48"), None, ["Date picker fix for mobile Safari", "Checked the iOS layout"])
    for monday, number, billed, summary, exported in [
        ("2026-10-12", 1007, 540, "Repo setup, layout, auth forms, responsive fixes", True),
        ("2026-10-19", 1008, 480, "Planning, settings page and code review", False),
    ]:
        st.weeks[monday] = {
            "approved_at": "2026-10-20T09:00:00", "total_minutes": billed, "billed_minutes": billed, "rate": 25.0,
            "invoice_number": number, "summary": summary, "issued": "2026-10-21", "due": "2026-11-10",
            "exports": [{"path": "~/Documents/cram/x.pdf", "at": "2026-10-21T09:00:00"}] if exported else [],
        }
    st.save()
