#!/usr/bin/env python3
"""Run every test suite and print a one-line summary for each. Costs no API credits."""
import re
import subprocess
import sys

SUITES = ["tests.test_logic", "tests.test_security", "tests.test_flow", "tests.test_helpline", "tests.test_schedule", "tests.test_questions", "tests.test_ops"]
failed = 0
for s in SUITES:
    p = subprocess.run([sys.executable, "-m", s], capture_output=True, text=True)
    m = re.search(r"ALL (\d+) ([\w-]+) CHECKS PASSED", p.stdout)
    if p.returncode == 0 and m:
        print(f"OK    {s:<22} {m.group(1)} checks")
    else:
        failed += 1
        print(f"FAIL  {s}")
        print("\n".join(l for l in (p.stdout + p.stderr).splitlines() if l.startswith("FAIL") or "Error" in l)[-1500:])
print("\nAll test suites passed." if not failed else f"\n{failed} suite(s) failed.")
sys.exit(1 if failed else 0)
