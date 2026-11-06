# Grok Imagine Downloader

English | [简体中文](README.zh-CN.md)

A lightweight command-line tool to archive your Grok Imagine media locally, with optional prompt and generation metadata. Designed for macOS and Windows; uses Python's standard library and the system `curl`.

This is an unofficial tool using Grok's web endpoints. The available assets, response formats, and authentication requirements can change.

## Features

- Paginate through the Imagine asset listing and download generated videos, images, and uploaded references.
- Download files uploaded in chats (the Files page) from a separate listing into `chat/`.
- Download concurrently, retry failed transfers, and check file sizes when the API provides them.
- Skip files already present with the expected size, so you can rerun the command after an interruption.
- Write a UTF-8 `metadata.json` index and preserve generation metadata for valid local files.
- Optionally retrieve prompts and generation settings from the source conversations.

## Scope

The download scope is the assets returned for your account by `/rest/assets` with `workspaceKind=WORKSPACE_KIND_IMAGINE_ALL`, plus the chat file listing that `/rest/assets` returns without `workspaceKind` (the files shown at <https://grok.com/files>). The tool follows pagination, skips entries marked deleted, and downloads the listed generated media and uploaded references.

- This asset listing is not a precise export of Saved or Liked posts. It does not guarantee access to every historical asset, variant, HD version, or step in a video extension chain. Deleted or unlisted assets cannot be newly retrieved by this tool.
- `--prompts` makes a best-effort lookup in source conversations for eligible complete local files. It does not guarantee a prompt for every file.
- The project focuses on local downloads and metadata. It does not provide a gallery UI, generate media, modify or delete cloud content, or automate browser sign-in.

## Requirements

- [uv](https://docs.astral.sh/uv/) **or** Python 3.8+.
- `curl` available on your `PATH`.
- A signed-in Grok account and an exported cookie file in Netscape format.

With uv, `.python-version` selects Python 3.13. The project has no third-party Python dependencies.

## Setup

1. Open <https://grok.com/imagine/saved> in your signed-in browser.
2. Use a cookie export tool you trust to export cookies for `grok.com` in Netscape format.
3. Save them as `grok.com_cookies.txt` beside `main.py`, or use `--cookies` to choose another path.

The cookie file contains login credentials. Keep it private and remove the exported copy when you no longer need it.

The script tries to construct a User-Agent from the local Chrome version on macOS and Windows, and prints it at startup. If detection fails, you use another browser, or authentication fails, pass the exporting browser's exact User-Agent with `--ua`. You can obtain it by running `navigator.userAgent` in that browser's developer console.

## Usage

Run these commands from the project directory:

~~~bash
uv run main.py
uv run main.py --prompts
uv run main.py --help
~~~

| Command option | Behavior |
| --- | --- |
| No options | Fetch the asset list, download media, and write metadata. |
| `--prompts` | Also look up generation settings for eligible downloaded files. |

Every run downloads missing or incomplete media, skips complete local files, and updates `metadata.json`.

Without uv, use `python3 main.py` on macOS or `python main.py` on Windows with the same options. Some help and status messages are currently in Chinese.

### Options

| Option | Default | Description |
| --- | --- | --- |
| `--cookies PATH` | `grok.com_cookies.txt` in the project directory | Netscape cookie file. |
| `--dest PATH` | `output/` in the project directory | Destination directory. |
| `--ua TEXT` | Detected local Chrome User-Agent | User-Agent of the browser that exported the cookies. |
| `--workers N` | `6` | Positive number of concurrent media downloads. |
| `--prompts` | Disabled | Look up generation metadata when neither the asset list nor the local cache provides any. |
| `--only KINDS` | All | Download only the given kinds, comma-separated: `video`, `image`, `uploads`, `chat` (e.g. `--only video`). A listing that no selected kind needs is not fetched. |

The default paths are relative to the script, not the current working directory. Custom paths support `~`.

~~~bash
uv run main.py --cookies "/path/to/grok.com_cookies.txt" --dest "/path/to/archive" --workers 3
uv run main.py --ua "YOUR_BROWSER_USER_AGENT"
~~~

## Output

~~~text
output/
├── video/          Generated videos
├── image/          Generated images
├── uploads/        Uploaded reference media
├── chat/           Files uploaded in chats
└── metadata.json   Index of complete local files
~~~

Files use `YYYYMMDD-HHMMSS_<asset-ID-prefix>.<extension>`. The ID prefix starts at eight characters and is extended if a filename collision occurs within the listing.

Each metadata record contains:

| Fields | Meaning |
| --- | --- |
| `file` | Relative path, using `/` on every platform. |
| `id`, `mime`, `created` | Asset ID, MIME type, and creation timestamp. |
| `size`, `w`, `h` | API-reported byte size, width, and height when available. |
| `genType`, `prompt`, `model`, `aspectRatio`, `duration`, `resolution` | Generation metadata, when available. |

Previously indexed assets that are no longer listed by Grok are retained in metadata when their local files still pass the stored size check. Cached generation metadata is reused on later runs while its file remains eligible for indexing; records for missing or incomplete files are not retained. Prompts are stored as plain text in `metadata.json`; keep this in mind when sharing your archive.

If migrating from a version that used `~/Downloads/grok-imagine`, move that directory to `output/` beside the script, or keep using it with `--dest`.

## Prompt lookup

The asset listing may omit generation settings. With `--prompts`, the script looks up the originating user message through the source conversation's `response-node` and `load-responses` endpoints.

- Only complete local generated files with source conversation/response IDs and no generation metadata from either the asset list or the local cache are eligible. Files with any generation metadata are skipped even if their prompt is missing. Uploaded references are excluded.
- Lookups are grouped by conversation, with up to two read requests per conversation and at most three conversations processed concurrently.
- HTTP 401, 403, or 429 stops further lookups without retrying those requests. Requests already in flight may finish. Downloaded files and generation metadata already found are retained.
- Deleted or inaccessible conversations, or changes to Grok's response format, can leave generation metadata unavailable.

## Reliability and limitations

- Downloads go to a `.part` file and are renamed after a successful transfer and size check. Failed files are retried; rerunning skips complete files and restarts incomplete transfers. HTTP Range resume is not implemented.
- Validation compares byte counts, not hashes. If the API reports no size or zero, the script only checks that the file is nonempty; it cannot verify completeness.
- Duplicate asset IDs are skipped within a listing; identical content under different IDs is not deduplicated.
- Once the asset list has been loaded, the download/prompt phase writes metadata on completion and in its cleanup path after an error or Ctrl+C. A forced process termination may prevent cleanup.
- Media download failures produce a nonzero exit status. Prompt lookup failures can leave a successful media archive with incomplete generation metadata.
- The tool follows pagination until no next-page token remains and rejects repeated tokens to avoid looping indefinitely. The archive reflects what the endpoint returns for your account.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| HTTP 401 / 403 | Re-export cookies and check `--ua`. If Chrome was recently updated, restart it before exporting. Access controls can also reject a request. |
| HTTP 429 | Allow time before another run and reduce `--workers` if media requests are being limited. Prompt lookup concurrency is separately capped at three. |
| HTTP 000 or a curl error | Check connectivity, curl availability, and proxy settings. Set `HTTPS_PROXY` explicitly if your connection requires a proxy. |
| Cannot read metadata.json | Restore or back up the existing file before rebuilding it. The script stops rather than overwriting an unreadable or invalid metadata file. |
| Missing prompts | Use `--prompts`; the file must already be complete locally and its source conversation must be accessible. |
| `?` characters in redirected Windows console output | Set `PYTHONUTF8=1` before running. Metadata is always written as UTF-8. |

Proxy examples (replace the port with your local proxy's port):

~~~bash
# macOS
export HTTPS_PROXY="http://127.0.0.1:7890"
uv run main.py
~~~

~~~powershell
# Windows PowerShell
$env:HTTPS_PROXY = "http://127.0.0.1:7890"
$env:PYTHONUTF8 = "1"
uv run main.py
~~~

## Development

`main.py` is the entry point; `download.py` contains the implementation.

The current project version is **0.1.0**. Offline tests cover downloader behavior with simulated responses; they do not establish current Grok compatibility or complete account coverage.

On 2026-10-31, a live-account smoke test on Windows successfully fetched one asset-list page and downloaded one generated MP4. Its byte count matched the API-reported size, and its metadata was saved. The same video's prompt and generation settings were also retrieved through two conversation-read requests and saved to `metadata.json`. Full pagination, full-account downloads, and end-to-end CLI checks on both target platforms remain unverified with a live account.

Run the offline regression tests without Grok credentials:

~~~bash
uv run python -B -m unittest discover -s tests -v
~~~

GitHub Actions runs these offline checks on Windows and macOS with Python 3.13,
plus a Python 3.8 compatibility job. It does not access Grok or require account cookies.

Before publishing, review the staged files. The repository ignores the default `output/` directory, legacy `downloads/` and manifest caches, `*.part`, and `*cookie*.txt`; custom destinations and cookie filenames may need additional ignore rules. Do not commit login cookies or private media.

## License

[MIT](LICENSE).
