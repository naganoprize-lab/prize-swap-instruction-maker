"""Validate a Kotlin-written package with the Windows importer before releasing APK."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prize_swap.mobile_package import read_package, products_from_entries

p = read_package(sys.argv[1])
d = p.data["instructions"]["documents"][0]
entry = products_from_entries(d["body"]["entries"])[0]
assert p.manifest["source_app"] == "android" and entry.notes == "Android往復テスト"
assert entry.name == "" and entry.included and entry.entry_id == "fixture-entry"
assert entry.booth_assignments[0]["assignment_id"] == "fixture-assignment"
assert "fixture-revision" in d["ancestors"]
print(
    "Kotlin → Windows Product round-trip: passed (blank product / IDs / revision / notes)."
)
