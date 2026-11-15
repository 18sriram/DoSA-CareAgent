#!/usr/bin/env python3
"""Administration tool.

  python manage.py init-db                       create / upgrade the database
  python manage.py create-user                   create a staff account (asks for the password; nothing is hard-coded)
  python manage.py list-users
  python manage.py demo-data                     DEMO ONLY: synthetic staff and patients with a published password
  python manage.py check                         find unsafe settings before going live
  python manage.py reset-db                      delete everything (asks you to type DELETE)
"""
import argparse
import getpass
import os
import sys

from app import config, db
from app.security import hash_password, password_problems


def cmd_init(_):
    db.init_db()
    print(f"Database ready at {config.DB_PATH}")


def cmd_create_user(a):
    db.init_db()
    username = a.username or input("Username: ").strip()
    name = a.full_name or input("Full name: ").strip()
    role = a.role or input("Role (admin / doctor / front_desk): ").strip()
    if role not in ("admin", "doctor", "front_desk"):
        sys.exit("Role must be admin, doctor or front_desk.")
    if db.get_user_by_username(username):
        sys.exit("That username already exists.")
    pw = getpass.getpass("Password: ")
    problems = password_problems(pw, username)
    if problems:
        sys.exit("Password not accepted: " + " ".join(problems))
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("The passwords do not match.")
    spec = (a.specialty or "orthopaedics") if role == "doctor" else None
    db.create_user(username, name, role, hash_password(pw), spec, must_change=not a.keep_password)
    print(f"Created {role} '{username}'." + ("" if a.keep_password else " They must change the password at first sign-in."))


def cmd_list(_):
    db.init_db()
    for u in db.list_users():
        print(f"{u['id']:>3}  {u['username']:<14} {u['role']:<11} {'active' if u['active'] else 'DISABLED':<9} {u['full_name']}")


def cmd_demo(a):
    from app import demo_data
    if a.reset:
        cmd_reset(argparse.Namespace(yes=True))
    db.init_db()
    print("Loading SYNTHETIC demo data (never use in production)...")
    links = demo_data.load()
    print("\nStaff accounts (password for all: %s)" % demo_data.DEMO_PASSWORD)
    for username, name, role, _ in demo_data.USERS:
        print(f"  {username:<10} {role:<11} {name}")
    print(f"\nHelp line: open /helpline and enter a patient's phone number with the PIN {demo_data.DEMO_PIN}")
    print("  e.g. Ramu 9000000001, Lakshmi 9000000002, Venkat 9000000003, Suresh 9000000004")
    print("\nPatients waiting for the intake call (open in a browser, valid 24 hours):")
    for name, url in links:
        print(f"  {name}: {url}")


def cmd_check(_):
    from app import healthcheck
    problems, warnings = healthcheck.run()
    for p in problems:
        print("PROBLEM  ", p)
    for w in warnings:
        print("warning  ", w)
    print("\nReady to go live." if not problems else f"\n{len(problems)} problem(s) to fix before real patients use this.")
    sys.exit(1 if problems else 0)


def cmd_reset(a):
    if not a.yes and input("This deletes ALL data. Type DELETE to continue: ") != "DELETE":
        sys.exit("Cancelled.")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(config.DB_PATH + suffix):
            os.remove(config.DB_PATH + suffix)
    print("Database deleted.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db").set_defaults(fn=cmd_init)
    c = sub.add_parser("create-user")
    for flag in ("--username", "--full-name", "--role", "--specialty"):
        c.add_argument(flag)
    c.add_argument("--keep-password", action="store_true", help="do not force a password change at first sign-in")
    c.set_defaults(fn=cmd_create_user)
    sub.add_parser("list-users").set_defaults(fn=cmd_list)
    d = sub.add_parser("demo-data")
    d.add_argument("--reset", action="store_true", help="delete the database first")
    d.set_defaults(fn=cmd_demo)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    r = sub.add_parser("reset-db")
    r.add_argument("--yes", action="store_true")
    r.set_defaults(fn=cmd_reset)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
