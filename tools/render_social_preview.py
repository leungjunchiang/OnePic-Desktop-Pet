"""Render local-only previews of the study room and two-pet visit UI."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.social_ui import BuddyVisitWindow, SocialHubDialog


class PreviewClient:
    signed_in = True

    @staticmethod
    def dashboard():
        return {
            "me": {"nickname": "小梁", "invite_code": "72648C6D", "show_exact_time": True, "allow_visits": True},
            "buddies": [
                {"user_id": "1", "nickname": "胡老师", "online": True, "working": True, "today_seconds": 2520, "outfit_key": "hour-04"},
                {"user_id": "2", "nickname": "绵绵", "online": True, "working": False, "today_seconds": 6580, "outfit_key": "hour-02"},
            ],
            "room_people": [], "requests": [], "visits": [],
            "rooms": [{"name": "安静工作间", "members": 2, "invite_code": "28B6DA84"}],
            "active_visits": [],
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])

    hub = SocialHubDialog(PreviewClient())
    hub.refresh(); hub.show(); app.processEvents()
    hub.grab().save(str(args.output / "study-room.png"))

    visit = BuddyVisitWindow()
    visit.show_peer(
        {"nickname": "胡老师", "today_seconds": 2520, "outfit_key": "hour-04"},
        "hour-02",
        5180,
    )
    app.processEvents()
    visit.grab().save(str(args.output / "buddy-visit.png"))


if __name__ == "__main__":
    main()
