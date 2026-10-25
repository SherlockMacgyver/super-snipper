"""Fetch release inputs from pinned URLs, verifying every byte against the lock."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def fetch_inputs(record=False):
    lock = ROOT / "packaging" / "inputs.json"
    items = json.loads(lock.read_text())
    cache = ROOT / ".build" / "downloads"
    cache.mkdir(parents=True, exist_ok=True)

    def fetch(item):
        name, entry = item
        path = cache / name
        if not path.is_file():
            part = path.with_name(name + ".part")
            with urllib.request.urlopen(entry["url"], timeout=120) as response, part.open("wb") as out:
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
            part.rename(path)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if entry.get("sha256") != digest:
            if not record or entry.get("sha256"):
                raise ValueError(f"Checksum mismatch or missing pin: {name}")
            entry["sha256"] = digest
        print(f"Verified {name}: {digest}", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, items.items()))
    if record:
        lock.write_text(json.dumps(items, indent=2) + "\n", encoding="ascii")
    return cache


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-hashes", action="store_true", help="maintainer-only: pin newly added upstream inputs")
    fetch_inputs(parser.parse_args().record_hashes)
