#!/usr/bin/env python3
"""Delete Grok assets that main.py already saved, to free Grok file storage.

Targets come from metadata.json in the archive, and only rows whose file is on disk with the
size Grok reported. Without --yes nothing is sent: the run only prints what would be deleted.
Every deletion is checked with GET /rest/assets/{id} (404 = gone) and appended to deleted.jsonl,
so an interrupted run can be started again and skips what is done."""
import argparse, json, os, shutil, sys, time, uuid

import download as dl

LOG = "deleted.jsonl"
# The Files page sends DELETE /rest/assets-metadata/{id} (SoftDelete in Grok's client); the Imagine
# page sends DELETE /rest/assets/{id} (DeleteAsset). Grok's client has no way to restore either.
ENDPOINTS = {"soft": "https://grok.com/rest/assets-metadata/", "hard": "https://grok.com/rest/assets/"}
DELAY = 0.3  # pause between assets to stay under Grok's rate limit


class Stop(Exception):
    """A response that makes going on unsafe or pointless (expired cookie, asset still there)."""


def request(url, method=None):
    """One request with backoff on 429/5xx; returns the final HTTP code as a string."""
    for wait in dl.MANIFEST_BACKOFF + (None,):
        body, err = dl.curl(url, timeout=60, retry=0, method=method,
                            headers=("origin: https://grok.com", f"x-xai-request-id: {uuid.uuid4()}"))
        code = body[-3:].decode()
        if code not in dl.RETRY_CODES or wait is None:
            return code
        print(f"  HTTP {code}, retrying in {wait}s", file=sys.stderr)
        time.sleep(wait)
    return code


def delete_one(aid, mode):
    """Delete one asset and confirm it is gone. Returns "deleted" or "gone" (already absent)."""
    code = request(ENDPOINTS[mode] + aid, method="DELETE")
    if code == "404":
        return "gone"
    if code in ("401", "403"):
        raise Stop(f"HTTP {code}: cookie expired or UA does not match the browser that exported it")
    if code != "200":
        raise Stop(f"DELETE {aid}: HTTP {code}")
    check = request("https://grok.com/rest/assets/" + aid)
    if check != "404":
        raise Stop(f"DELETE {aid} returned 200 but GET still answers HTTP {check}")
    return "deleted"


def load_done(path):
    done = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    done.add(json.loads(line)["id"])
    return done


def targets(rows, kinds, done):
    """Rows of the selected kinds whose file is complete on disk and not yet deleted."""
    out, seen = [], set()
    for r in rows:
        aid, rel = r.get("id"), r.get("file") or ""
        if not aid or aid in seen or aid in done or rel.split("/")[0] not in kinds:
            continue
        if dl.complete({"path": os.path.join(dl.DEST, rel), "size": r.get("size") or 0}):
            seen.add(aid)
            out.append(r)
    return out


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Delete Grok assets that are already saved in the archive")
    ap.add_argument("--only", type=dl.parse_kinds, metavar="KINDS", required=True,
                    help="Kinds to delete, comma-separated: " + ", ".join(dl.KINDS))
    ap.add_argument("--yes", action="store_true", help="Actually delete. Without it, only list the targets")
    ap.add_argument("--hard", action="store_true",
                    help="Use DELETE /rest/assets/{id} (Imagine page) instead of /rest/assets-metadata/{id} (Files page)")
    ap.add_argument("--limit", type=int, help="Delete at most this many assets")
    ap.add_argument("--cookies", default=dl.COOKIES, help="Netscape cookie file (default %(default)s)")
    ap.add_argument("--dest", default=dl.DEST, help="Archive directory with metadata.json (default %(default)s)")
    ap.add_argument("--ua", help="UA of the browser that exported the cookie (default: local Chrome)")
    args = ap.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        ap.error("--limit must be at least 1")
    dl.COOKIES, dl.DEST = os.path.expanduser(args.cookies), os.path.expanduser(args.dest)
    dl.UA = args.ua or dl.chrome_ua()

    rows = dl.load_metadata()
    if not rows:
        sys.exit(f"no metadata.json rows in {dl.DEST}: run main.py first")
    log_path = os.path.join(dl.DEST, LOG)
    todo = targets(rows, args.only, load_done(log_path))
    if args.limit:
        todo = todo[:args.limit]
    mode = "hard" if args.hard else "soft"
    size = sum(r.get("size") or 0 for r in todo)
    by_kind = {}
    for r in todo:
        k = r["file"].split("/")[0]
        by_kind[k] = by_kind.get(k, 0) + 1
    print(f"Targets: {len(todo)} assets, {size / 1024**3:.2f} GiB, {json.dumps(by_kind)}; mode: {mode}")
    if not args.yes:
        for r in todo[:5]:
            print("  " + r["file"])
        print("Dry run: nothing was deleted. Add --yes to delete these assets on Grok.")
        return
    if not todo:
        return
    if not shutil.which("curl"):
        sys.exit("curl not found")
    if not os.path.exists(dl.COOKIES):
        sys.exit(f"cookie file missing: {dl.COOKIES}")
    if not dl.UA:
        sys.exit("local Chrome not found: pass --ua")

    counts = {"deleted": 0, "gone": 0}
    with open(log_path, "a", encoding="utf-8", newline="\n") as log:
        try:
            for i, r in enumerate(todo, 1):
                result = delete_one(r["id"], mode)
                counts[result] += 1
                log.write(json.dumps({"id": r["id"], "file": r["file"], "mode": mode, "result": result,
                                      "time": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
                log.flush()
                if i % 25 == 0 or i == len(todo):
                    print(f"[{i}/{len(todo)}] deleted={counts['deleted']} already_gone={counts['gone']}")
                time.sleep(DELAY)
        except Stop as e:
            print(f"Stopped: {e}", file=sys.stderr)
            print(f"Done before stopping: deleted={counts['deleted']} already_gone={counts['gone']}")
            sys.exit(1)
    print(f"Done: deleted={counts['deleted']} already_gone={counts['gone']} -> {log_path}")


if __name__ == "__main__":
    main()
