#!/usr/bin/env python3
"""Archive media returned by Grok's Imagine asset listing via curl and exported cookies."""
import argparse, contextlib, json, os, plistlib, shutil, subprocess, sys, tempfile, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.expanduser("~")
ROOT = os.path.dirname(os.path.abspath(__file__))  # project root: the cookie and output/ live here
COOKIES = os.path.join(ROOT, "grok.com_cookies.txt")
DEST = os.path.join(ROOT, "output")
WORKERS = 6
PROMPT_WORKERS = 3  # --prompts lookups stay gentle on the chat API
UA = ""  # set in main(): --ua, else built from the local Chrome version
GEN_KEYS = ("genType", "prompt", "model", "aspectRatio", "duration", "resolution")

EXT = {"video/mp4": ".mp4", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png"}
RETRY_CODES = ("429", "500", "502", "503", "504")
MANIFEST_BACKOFF = (5, 10, 20, 40, 60)  # seconds between manifest retries after curl's own quick retries
PAGE_DELAY = 0.5  # pause between manifest pages to stay under Grok's rate limit
IMAGINE_KINDS = ("video", "image", "uploads")  # output subdirectories plan() picks for the Imagine listing
CHAT_KIND = "chat"  # files uploaded in chats: a separate listing without workspaceKind
KINDS = IMAGINE_KINDS + (CHAT_KIND,)  # --only filters on these


def parse_kinds(value):
    """argparse type for --only: comma-separated subset of KINDS."""
    kinds = {k.strip() for k in value.split(",") if k.strip()}
    bad = kinds - set(KINDS)
    if not kinds or bad:
        raise argparse.ArgumentTypeError(
            f"invalid kind(s): {', '.join(sorted(bad)) or '(empty)'}; choose from {', '.join(KINDS)}")
    return kinds

# Chrome's reduced UA: only the major version changes, the platform part is frozen.
UA_FMT = "Mozilla/5.0 ({}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{}.0.0.0 Safari/537.36"
UA_OS = {"darwin": "Macintosh; Intel Mac OS X 10_15_7", "win32": "Windows NT 10.0; Win64; x64"}
CHROME_DATA = {"darwin": os.path.join(HOME, "Library", "Application Support", "Google", "Chrome"),
               "win32": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")}
MAC_CHROME_PLIST = "/Applications/Google Chrome.app/Contents/Info.plist"


def chrome_ua():
    """UA of the local Chrome, from the version it last ran as; None if not found."""
    if sys.platform not in UA_OS:
        return None
    ver = ""
    with contextlib.suppress(OSError):
        with open(os.path.join(CHROME_DATA[sys.platform], "Last Version"), encoding="utf-8") as f:
            ver = f.read()
    if not ver and sys.platform == "darwin":  # fall back to the installed app bundle
        with contextlib.suppress(OSError, ValueError):
            with open(MAC_CHROME_PLIST, "rb") as f:
                ver = plistlib.load(f).get("CFBundleShortVersionString", "")
    major = ver.strip().split(".")[0]
    return UA_FMT.format(UA_OS[sys.platform], major) if major.isdigit() else None


def curl(url, out=None, timeout=180, data=None, retry=2):
    # -f: no body on HTTP errors, so a retried 429/5xx can't leave an error page in front of the real output.
    # No --compressed: older Windows 10 curl.exe is built without zlib and rejects that option.
    cmd = ["curl", "-fsS", "-b", COOKIES, "-A", UA, "-H", "referer: https://grok.com/",
           "--max-time", str(timeout), "--retry", str(retry), "--retry-delay", "2",
           "-w", "%{http_code}", url]
    if data is not None:  # JSON body via stdin (no Windows command-line length limit) -> POST
        cmd += ["-H", "content-type: application/json", "--data-binary", "@-"]
    cmd += ["-o", out] if out else ["-o", "-"]
    r = subprocess.run(cmd, input=None if data is None else json.dumps(data).encode(), capture_output=True)
    err = r.stderr.decode("utf-8", "replace")
    if r.returncode:
        # A server can send HTTP 200 before a transfer fails. Never accept a partial body.
        # Keep HTTP errors so callers can distinguish expired cookies and rate limits.
        code = r.stdout[-3:]
        status = code if code.isdigit() and int(code) >= 400 else b"000"
        return status, f"curl exited with code {r.returncode}: {err}"
    return r.stdout, err


def fetch_manifest(workspace_kind="WORKSPACE_KIND_IMAGINE_ALL", allow_empty_pages=False):
    """Page through /rest/assets until nextPageToken runs out.

    workspace_kind=None lists the chat file library (grok.com/files). That listing returns
    empty pages that still carry a next page token, so allow_empty_pages accepts them."""
    all_assets, token, page, seen_tokens = [], "", 0, set()
    while True:
        url = "https://grok.com/rest/assets?pageSize=100&orderBy=ORDER_BY_CREATE_TIME"
        if workspace_kind:
            url += "&workspaceKind=" + workspace_kind
        if token:
            url += "&pageToken=" + urllib.parse.quote(token, safe="")
        # curl can truncate an output file on retry, but cannot rewind stdout.
        with tempfile.TemporaryDirectory() as tmp:
            response_path = os.path.join(tmp, "manifest.json")
            for wait in MANIFEST_BACKOFF + (None,):
                body, err = curl(url, out=response_path)
                code = body[-3:].decode()
                if code not in RETRY_CODES or wait is None:
                    break
                # Large libraries need 100+ pages; Grok answers bursts with 429. Back off instead of aborting.
                print(f"  manifest page {page+1}: HTTP {code}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
            if code != "200":
                print(f"  manifest page {page+1}: HTTP {code} {err}", file=sys.stderr)
                if code in ("401", "403"):
                    print(f"  cookie 已失效，或 UA 与导出 cookie 的浏览器不一致（当前 UA: {UA}）。\n"
                          "  重新导出 cookie；Chrome 刚升级过就先重启 Chrome 再导出；"
                          "不是 Chrome 的话用 --ua 传入浏览器控制台里 navigator.userAgent 的值。", file=sys.stderr)
                elif code == "000":
                    print("  请求未完成或传输失败，请查看上面的 curl 错误。需要代理时先设置 HTTPS_PROXY 环境变量。",
                          file=sys.stderr)
                sys.exit(1)
            with open(response_path, "rb") as f:
                data = json.load(f)
        batch = data.get("assets", [])
        all_assets += batch
        page += 1
        print(f"  page {page}: +{len(batch)} (total {len(all_assets)})")
        token = data.get("nextPageToken") or ""
        if not token:
            break
        if not batch and not allow_empty_pages:
            raise RuntimeError("Asset listing returned an empty page with a next page token; listing is incomplete")
        if token in seen_tokens:
            raise RuntimeError("Asset listing repeated a page token; listing is incomplete")
        seen_tokens.add(token)
        time.sleep(PAGE_DELAY)
    return all_assets


def gen_info(a):
    """Generation params; mediaGenInput is nested as {genType: {prompt, modelName, ...}}."""
    mgi = a.get("mediaGenInput")
    if not isinstance(mgi, dict) or not mgi:
        return {}
    kind, inner = next(iter(mgi.items()))
    if not isinstance(inner, dict):
        return {"genType": kind}
    return {
        "genType": kind,
        "prompt": inner.get("prompt") or None,
        "model": inner.get("modelName") or None,
        "aspectRatio": inner.get("aspectRatio") or None,
        "duration": inner.get("duration") or None,
        "resolution": inner.get("resolutionName") or None,
    }


def plan(assets, sub=None):
    """Map each asset to a destination path; dedupe filename collisions.
    sub forces one output subdirectory; by default it follows fileSource and mimeType."""
    used, seen, items = set(), set(), []
    for a in assets:
        aid = a["assetId"]
        if a.get("isDeleted") or aid in seen:
            continue
        seen.add(aid)
        mime = a["mimeType"]
        ext = EXT.get(mime) or os.path.splitext(a.get("name", ""))[1] or ".bin"
        gen = a.get("fileSource") == "IMAGINE_GENERATED_FILE_SOURCE"
        kind = sub or ("uploads" if not gen else ("video" if mime == "video/mp4" else "image"))
        ts = a["createTime"].replace("-", "").replace(":", "").replace("T", "-")[:15]
        n = 8
        while True:
            name = f"{ts}_{aid[:n]}{ext}"
            rel = f"{kind}/{name}"  # always "/", so metadata.json is the same on Mac and Windows
            if rel not in used or n >= len(aid):
                break
            n += 4
        used.add(rel)
        items.append({
            "url": "https://assets.grok.com/" + a["key"],
            "path": os.path.join(DEST, kind, name),
            "rel": rel,
            "size": int(a.get("sizeBytes") or 0),
            "id": aid,
            "mime": mime,
            "created": a["createTime"],
            "w": a.get("width"), "h": a.get("height"),
            "gen": gen_info(a),
            "conv": a.get("sourceConversationId"), "resp": a.get("responseId"),  # for --prompts
        })
    return items


def load_metadata():
    """Load the prompt cache; refuse to overwrite an existing file that cannot be read."""
    try:
        with open(os.path.join(DEST, "metadata.json"), encoding="utf-8-sig") as f:  # -sig: tolerate a BOM
            rows = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        raise RuntimeError("Cannot read metadata.json; restore or back it up before rebuilding it") from e
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("metadata.json must contain a list of objects; existing file was not changed")
    return rows


def chat_api(url, data=None):
    """One request to a grok.com chat endpoint, no retries; returns (http_code, json or None)."""
    body, _ = curl(url, timeout=60, data=data, retry=0)
    code = body[-3:].decode()
    try:
        return code, json.loads(body[:-3]) if code == "200" else None
    except ValueError:
        return code, None


def fetch_prompts(items):
    """--prompts: when the asset list omits mediaGenInput, try the message that started each
    generation. Per source conversation: response-node (who replied to what), then
    load-responses for the parent human messages. Stops on 401/403/429; downloads are unaffected."""
    todo = {}
    for it in items:
        if not it["gen"] and it["conv"] and it["resp"] and not it["rel"].startswith("uploads/") and complete(it):
            todo.setdefault(it["conv"], []).append(it)
    if not todo:
        return
    total = sum(len(v) for v in todo.values())
    print(f"\nLooking up prompts: {total} files in {len(todo)} conversations...")
    stopped = []

    def lookup(conv):
        if stopped:
            return 0
        base = f"https://grok.com/rest/app-chat/conversations/{conv}"
        code, nodes = chat_api(base + "/response-node?includeThreads=true")
        if code in ("401", "403", "429"):
            stopped.append(code)
        if stopped or not nodes:
            return 0
        try:
            parent = {n.get("responseId"): n.get("parentResponseId") for n in nodes.get("responseNodes", [])}
            want = sorted({parent.get(it["resp"]) for it in todo[conv]} - {None})
            if not want:
                return 0
            code, resp = chat_api(base + "/load-responses", {"responseIds": want})
            if code in ("401", "403", "429"):
                stopped.append(code)
            msgs = {r.get("responseId"): r for r in (resp or {}).get("responses", [])}
            found = 0
            for it in todo[conv]:
                it["gen"] = gen_info(msgs.get(parent.get(it["resp"]), {}))
                found += bool(it["gen"].get("prompt"))
            return found
        except (AttributeError, TypeError):  # unexpected response shape: skip this conversation
            return 0

    with ThreadPoolExecutor(max_workers=PROMPT_WORKERS) as ex:
        found = sum(ex.map(lookup, todo))
    print(f"  prompts: found {found}/{total}")
    if stopped:
        print(f"  查询提示词时遇到 HTTP {stopped[0]}，已停止查询。已查到的会保存，稍后再加 --prompts 运行即可补上。",
              file=sys.stderr)


def complete(it):
    """File is on disk with the byte count the API reported (0 = unknown)."""
    p = it["path"]
    if not os.path.isfile(p):
        return False
    size = os.path.getsize(p)
    return size == it["size"] if it["size"] else size > 0


def download(it):
    """Fetch one asset, verifying byte count. Returns (status, item, detail)."""
    p, want = it["path"], it["size"]
    if complete(it):
        return ("skip", it, os.path.getsize(p))
    tmp = p + ".part"
    for attempt in range(3):
        if attempt:
            time.sleep(1.5 * attempt)
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            body, err = curl(it["url"], out=tmp)
            code = body.decode(errors="replace")[-3:]
            got = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            if code == "200" and (got == want if want else got > 0):
                os.replace(tmp, p)
                return ("ok", it, got)
            detail = f"http={code} got={got} want={want} {err[:120]}".strip()
        except OSError as e:  # e.g. on Windows an antivirus scan can briefly lock the .part file
            detail = f"{type(e).__name__}: {e}"
    with contextlib.suppress(OSError):
        os.remove(tmp)
    return ("fail", it, detail)


def download_all(items, workers):
    total = sum(i["size"] for i in items)
    print(f"\n{len(items)} assets, {total/1048576:.1f} MB -> {DEST}\n")
    done = {"ok": 0, "skip": 0, "fail": 0}
    fails, got_bytes, t0 = [], 0, time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for n, (st, it, detail) in enumerate(ex.map(download, items), 1):
            done[st] += 1
            if st == "fail":
                fails.append((it["rel"], detail))
            else:
                got_bytes += detail
            if n % 25 == 0 or n == len(items):
                el = time.time() - t0
                print(f"  [{n}/{len(items)}] ok={done['ok']} skip={done['skip']} "
                      f"fail={done['fail']}  {got_bytes/1048576:.0f} MB  {el:.0f}s")

    print(f"\nDone: ok={done['ok']} skip={done['skip']} fail={done['fail']}")
    if fails:
        print("\nFailures:")
        for rel, d in fails:
            print(f"  {rel}: {d}")
    return done["fail"]


def write_json(path, data):
    """UTF-8 + LF regardless of OS locale; written to a temp file first so it is never half-written."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def write_metadata(items, old):
    """Sidecar metadata (prompts + dimensions) for the files on disk."""
    out = [{"file": i["rel"], "id": i["id"], "mime": i["mime"], "created": i["created"],
            "size": i["size"], "w": i["w"], "h": i["h"], **i["gen"]}
           for i in items if complete(i)]
    ids = {r["id"] for r in out}
    # Keep earlier rows for complete local files, including assets no longer listed by Grok.
    out += [r for r in old if r.get("id") not in ids and r.get("file")
            and complete({"path": os.path.join(DEST, r["file"]), "size": r.get("size") or 0})]
    os.makedirs(DEST, exist_ok=True)
    p = os.path.join(DEST, "metadata.json")
    write_json(p, out)

    kinds = {}
    for r in out:
        sub = r["file"].split("/")[0]
        kinds[sub] = kinds.get(sub, 0) + 1
    withp = sum(1 for r in out if r.get("prompt"))
    print(f"\nmetadata.json: {len(out)} 条, 带 prompt {withp} 条 -> {p}")
    print("分类:", json.dumps(kinds, ensure_ascii=False))


def main(argv=None):
    global COOKIES, DEST, UA
    if hasattr(sys.stdout, "reconfigure"):  # Windows files/pipes use the ANSI code page: print '?' instead of crashing
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="下载 Grok Imagine 资产列表返回的媒体并保存元数据（macOS / Windows）")
    ap.add_argument("--cookies", default=COOKIES, help="Netscape 格式的 cookie 文件（默认 %(default)s）")
    ap.add_argument("--dest", default=DEST, help="下载目录（默认 %(default)s）")
    ap.add_argument("--ua", help="导出 cookie 的浏览器的 UA（默认按本机 Chrome 版本生成）")
    ap.add_argument("--workers", type=int, default=WORKERS, help="并发数（默认 %(default)s）")
    ap.add_argument("--prompts", action="store_true",
                    help="到生成文件的来源对话里查提示词等参数（每段对话最多 2 个请求，复用已保存参数）")
    ap.add_argument("--only", type=parse_kinds, metavar="KINDS",
                    help="Download only these kinds, comma-separated: " + ", ".join(KINDS)
                         + " (e.g. --only video). Default: all")
    args = ap.parse_args(argv)
    if args.workers < 1:
        ap.error("--workers must be at least 1")
    COOKIES, DEST = os.path.expanduser(args.cookies), os.path.expanduser(args.dest)  # "--dest=~/x" is not expanded by shells
    UA = args.ua or chrome_ua()

    if not shutil.which("curl"):
        sys.exit("curl not found (built into macOS and Windows 10+)")
    if not os.path.exists(COOKIES):
        sys.exit(f"cookie file missing: {COOKIES}\n把导出的 cookie 存成这个文件，或用 --cookies 指定路径")
    if not UA:
        sys.exit("找不到本机 Chrome：用 --ua 传入导出 cookie 的浏览器的 UA（浏览器控制台运行 navigator.userAgent）")
    print(f"UA: {UA}")
    kinds = args.only or set(KINDS)
    items = []
    if kinds & set(IMAGINE_KINDS):  # skip each listing that --only does not need
        print("Fetching manifest...")
        items = plan(fetch_manifest())
    if CHAT_KIND in kinds:
        print("Fetching chat file manifest...")
        listed = {it["id"] for it in items}
        items += [it for it in plan(fetch_manifest(workspace_kind=None, allow_empty_pages=True), sub=CHAT_KIND)
                  if it["id"] not in listed]
    if args.only:
        total = len(items)
        items = [it for it in items if it["rel"].split("/")[0] in args.only]
        print(f"--only {','.join(sorted(args.only))}: {len(items)} / {total} assets")
    old = load_metadata()
    cache = {r["id"]: {k: r[k] for k in GEN_KEYS if k in r} for r in old if r.get("genType")}
    for it in items:
        it["gen"] = {**cache.get(it["id"], {}),
                     **{k: v for k, v in it["gen"].items() if v is not None and v != ""}}
    try:
        failures = download_all(items, args.workers)
        if args.prompts:
            fetch_prompts(items)
    finally:  # also on Ctrl-C or an error: keep what this run already found
        write_metadata(items, old)
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
