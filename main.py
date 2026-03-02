#!/usr/bin/env python3
"""CLI for interacting with Regybox — list classes, enroll, cancel."""

import sys
import datetime
from regybox import RegyboxClient


def print_classes(classes: list[dict]):
    if not classes:
        print("No classes found.")
        return

    print(f"\n{'#':<4} {'Time':<14} {'Name':<25} {'Cap':>7} {'Status':<12}")
    print("-" * 66)
    for i, c in enumerate(classes):
        cap = f"{c['capacity']}/{c['max_capacity']}" if c["max_capacity"] else f"{c['capacity']}/∞"
        if c["is_enrolled"]:
            status = "ENROLLED"
        elif c["is_over"]:
            status = "OVER"
        elif c["is_open"]:
            status = "OPEN"
        else:
            status = "-"
        print(f"{i:<4} {c['time']:<14} {c['name']:<25} {cap:>7} {status:<12}")
    print()


def main():
    client = RegyboxClient()

    print("Logging in to Regybox...")
    token = client.login()
    print(f"Logged in successfully (token: {token[:12]}...)")

    # Parse date argument or default to today
    if len(sys.argv) > 1 and sys.argv[1] == "tomorrow":
        date = datetime.date.today() + datetime.timedelta(days=1)
    elif len(sys.argv) > 1:
        try:
            date = datetime.date.fromisoformat(sys.argv[1])
        except ValueError:
            print(f"Usage: python main.py [YYYY-MM-DD | tomorrow]")
            sys.exit(1)
    else:
        date = datetime.date.today()

    print(f"\nClasses for {date}:")
    classes = client.get_classes(date)
    print_classes(classes)

    # Interactive menu
    while True:
        action = input("Action? [e]nroll #, [c]ancel #, [d]etails #, [n]ext day, [p]rev day, [q]uit: ").strip().lower()

        if action == "q":
            break
        elif action == "n":
            date += datetime.timedelta(days=1)
            classes = client.get_classes(date)
            print(f"\nClasses for {date}:")
            print_classes(classes)
        elif action == "p":
            date -= datetime.timedelta(days=1)
            classes = client.get_classes(date)
            print(f"\nClasses for {date}:")
            print_classes(classes)
        elif action.startswith("e"):
            try:
                idx = int(action.split()[1]) if " " in action else int(input("Class #: "))
                c = classes[idx]
                if not c["enroll_url"]:
                    print("Cannot enroll in this class (not open or already enrolled).")
                    continue
                msg = client.enroll(c["enroll_url"])
                print(f"Result: {msg}")
                classes = client.get_classes(date)
                print_classes(classes)
            except (ValueError, IndexError):
                print("Invalid class number.")
        elif action.startswith("c"):
            try:
                idx = int(action.split()[1]) if " " in action else int(input("Class #: "))
                c = classes[idx]
                if not c["unenroll_url"]:
                    print("Cannot cancel — not enrolled or cancellation unavailable.")
                    continue
                msg = client.unenroll(c["unenroll_url"])
                print(f"Result: {msg}")
                classes = client.get_classes(date)
                print_classes(classes)
            except (ValueError, IndexError):
                print("Invalid class number.")
        elif action.startswith("d"):
            try:
                idx = int(action.split()[1]) if " " in action else int(input("Class #: "))
                c = classes[idx]
                if not c["class_id"]:
                    print("No class ID available.")
                    continue
                details = client.get_class_details(c["class_id"], str(date))
                print(f"\n  Date: {details['date']}")
                print(f"  Time: {details['time']}")
                print(f"  Enrolled: {details['enrolled_count']}")
                for p in details["enrolled_people"]:
                    print(f"    - {p}")
                print()
            except (ValueError, IndexError):
                print("Invalid class number.")
        else:
            print("Unknown action.")


if __name__ == "__main__":
    main()
