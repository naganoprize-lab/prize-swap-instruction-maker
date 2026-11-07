"""Expose bounded JUnit diagnostics as Actions annotations without masking failures."""

from pathlib import Path
import xml.etree.ElementTree as ET


def main():
    count = 0
    for path in Path("build-tests").glob("*.xml"):
        for case in ET.parse(path).getroot().iter("testcase"):
            for tag in ("failure", "error"):
                failure = case.find(tag)
                if failure is None:
                    continue
                count += 1
                if count > 10:
                    continue
                text = f'{case.get("classname","")}::{case.get("name","")}\n' + (
                    failure.text or failure.get("message", "")
                )
                text = text[:12000].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                print("::error::" + text)
    print(f"JUnit failure entries: {count} (full XML retained in Pytest-results)")


if __name__ == "__main__":
    main()
