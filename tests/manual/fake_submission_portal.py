#!/usr/bin/env python3
"""Fake NCBI Submission Portal - dev-only test helper. NOT part of the shipped
library, NOT imported by anything under src/.

Simulates the one piece of NCBI's real UI-less Submission Protocol that
automated unit tests can't exercise: periodically scans a submission upload
directory tree for finished uploads (a `submit.ready` file present, no
`report.1.xml` yet), and writes back a synthetic `report.1.xml` with
Processed-ok status and a fake accession per BioSample action - so
submit_and_wait() can be validated end-to-end against a local SFTP server
(e.g. `atmoz/sftp`) without touching any real NCBI infrastructure.

This operates directly on the local filesystem path that the SFTP server's
upload directory is bind-mounted to - it does not itself speak SFTP. Only
useful for local Docker-based testing, where that host path is known.

Happy-path only: every BioSample action is always reported Processed-ok.
Error-path behavior already has full unit test coverage against hand-crafted
report XML in tests/test_submission.py, where it's easier to control precisely.

Usage:
    python tests/manual/fake_submission_portal.py <watch_dir> [--interval SECONDS] [--once]

Example (matching the atmoz/sftp container set up for this feature):
    python tests/manual/fake_submission_portal.py /tmp/ncbi_sftp_test/uploads --interval 2
"""

from __future__ import annotations

import argparse
import itertools
import time
from pathlib import Path
from xml.etree import ElementTree

_accession_counter = itertools.count(1)


def _fake_accession() -> str:
    return f"SAMN{next(_accession_counter):08d}"


def _find_pending_submissions(watch_dir: Path):
    for submit_ready in watch_dir.rglob("submit.ready"):
        folder = submit_ready.parent
        if not (folder / "report.1.xml").exists():
            yield folder


def _build_fake_report(submission_xml_path: Path) -> bytes:
    root = ElementTree.parse(submission_xml_path).getroot()

    report_root = ElementTree.Element("SubmissionStatus", {"status": "Processed-ok", "submission_id": "FAKE-SUB-1"})
    for action in root.findall("Action"):
        add_data = action.find("AddData")
        if add_data is None or add_data.get("target_db") != "BioSample":
            continue
        spuid_el = add_data.find("Identifier/SPUID")

        action_el = ElementTree.SubElement(report_root, "Action", {"status": "Processed-ok", "target_db": "BioSample"})
        response_el = ElementTree.SubElement(action_el, "Response")
        ElementTree.SubElement(
            response_el,
            "Object",
            {
                "accession": _fake_accession(),
                "spuid": spuid_el.text if spuid_el is not None else "",
                "spuid_namespace": spuid_el.get("spuid_namespace", "") if spuid_el is not None else "",
            },
        )

    ElementTree.indent(report_root)
    return ElementTree.tostring(report_root, encoding="UTF-8", xml_declaration=True)


def _process_once(watch_dir: Path) -> int:
    processed = 0
    for folder in _find_pending_submissions(watch_dir):
        submission_xml = folder / "submission.xml"
        if not submission_xml.exists():
            print(f"[fake portal] {folder}: submit.ready present but no submission.xml, skipping")
            continue
        report = _build_fake_report(submission_xml)
        (folder / "report.1.xml").write_bytes(report)
        print(f"[fake portal] wrote {folder / 'report.1.xml'}")
        processed += 1
    return processed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("watch_dir", type=Path, help="Host directory bind-mounted to the SFTP server's upload tree")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between scans (default: 2.0)")
    parser.add_argument("--once", action="store_true", help="Scan once and exit, instead of looping")
    args = parser.parse_args()

    if not args.watch_dir.is_dir():
        raise SystemExit(f"{args.watch_dir} is not a directory")

    print(f"[fake portal] watching {args.watch_dir} (Ctrl+C to stop)")
    if args.once:
        _process_once(args.watch_dir)
        return

    try:
        while True:
            _process_once(args.watch_dir)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
