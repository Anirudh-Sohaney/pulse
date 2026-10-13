#!/usr/bin/env python3
"""Download 3DLNews2 preprocessed Arkansas newspaper article files for 2013-2022."""
import gzip
import hashlib
import json
import os
import re
import subprocess
import concurrent.futures

BASE = "https://g-e840e1.746abe.8540.data.globus.org/1-Google/1-Newspaper/preprocessed_state/AR"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(HERE, "raw")
YEARS = list(range(2013, 2023))
MANIFEST = os.path.join(HERE, "source_manifest.json")

with open(MANIFEST, encoding="utf-8") as source:
    EXPECTED = json.load(source)["files"]


def valid_archive(path, name, pointer_hash=None):
    expected = EXPECTED[name]
    if not os.path.isfile(path) or os.path.getsize(path) != expected["bytes"]:
        return False
    if pointer_hash:
        digest = hashlib.sha256()
        with open(path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != pointer_hash:
            return False
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as source:
            if sum(1 for _ in source) != expected["rows"]:
                return False
    except (OSError, EOFError):
        return False
    return True


def fetch(year):
    fname = f"preprocessed_newspaper_articles_AR_{year}.jsonl.gz"
    dest = os.path.join(RAW, fname)
    pointer_hash = None
    if os.path.exists(dest):
        try:
            pointer = open(dest, "rt", encoding="ascii").read(2048)
            match = re.search(r"^oid sha256:([0-9a-f]{64})$", pointer, re.M)
            pointer_hash = match.group(1) if match else None
        except (OSError, UnicodeDecodeError):
            pass
        if valid_archive(dest, fname, pointer_hash):
            return fname, "verified existing archive"
    temporary = dest + ".download"
    r = subprocess.run(
        ["curl", "--fail", "--location", "--silent", "--show-error",
         "--retry", "2", "--retry-all-errors", "--retry-delay", "2",
         "--output", temporary, "--max-time", "300", f"{BASE}/{fname}"],
        capture_output=True,
    )
    if r.returncode == 0 and valid_archive(temporary, fname, pointer_hash):
        os.replace(temporary, dest)
        return fname, f"verified({os.path.getsize(dest)} bytes, {EXPECTED[fname]['rows']} rows)"
    if os.path.exists(temporary):
        os.remove(temporary)
    detail = r.stderr.decode("utf-8", "replace").strip()[-240:]
    return fname, f"FAIL rc={r.returncode} {detail}".strip()


os.makedirs(RAW, exist_ok=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
    for f in concurrent.futures.as_completed([ex.submit(fetch, y) for y in YEARS]):
        fname, status = f.result()
        print(f"{fname}: {status}", flush=True)
print("DONE")
