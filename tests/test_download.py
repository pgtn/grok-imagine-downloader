"""Offline regression tests: python -m unittest discover -s tests -v."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import download as downloader


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.dest = self.root / "output"
        self.dest.mkdir()
        cookies = self.root / "offline-cookies.txt"
        cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        self.patch(downloader, "DEST", str(self.dest))
        self.patch(downloader, "COOKIES", str(cookies))
        self.patch(downloader, "UA", "offline-test-UA")
        self.patch(downloader, "chrome_ua", return_value="offline-test-UA")
        self.patch(downloader.shutil, "which", return_value="curl")
        self.patch(downloader.time, "sleep")
        # Any test that accidentally reaches a real command fails immediately.
        self.process = self.patch(
            downloader.subprocess, "run", side_effect=AssertionError("Unexpected subprocess")
        )
        for stream in ("stdout", "stderr"):
            self.patch(sys, stream, io.StringIO())

    def patch(self, target, name, *args, **kwargs):
        patcher = mock.patch.object(target, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def asset(self, asset_id="example-asset", size=4):
        return {
            "assetId": asset_id,
            "mimeType": "video/mp4",
            "fileSource": "IMAGINE_GENERATED_FILE_SOURCE",
            "createTime": "2026-01-02T03:04:05Z",
            "key": asset_id + ".mp4",
            "sizeBytes": size,
            "sourceConversationId": "conversation-1",
            "responseId": asset_id + "-response",
        }

    def item(self, asset_id="example-asset", size=4, payload=None):
        item = downloader.plan([self.asset(asset_id, size)])[0]
        if payload is not None:
            path = Path(item["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        return item

    def metadata(self):
        return json.loads((self.dest / "metadata.json").read_text(encoding="utf-8"))

    @staticmethod
    def page(assets, token=None):
        return json.dumps({"assets": assets, "nextPageToken": token}).encode() + b"200", ""

    def test_curl_rejects_partial_http_200_on_transport_failure(self):
        self.process.side_effect = None
        for exit_code in (18, 28):
            with self.subTest(exit_code=exit_code):
                self.process.return_value = subprocess.CompletedProcess(
                    [], exit_code, b"partial body200", b"transfer interrupted"
                )
                body, error = downloader.curl("https://example.invalid/asset")
                self.assertEqual(body[-3:], b"000")
                self.assertIn(str(exit_code), error)
                self.assertIn("transfer interrupted", error)

    def test_curl_preserves_auth_and_rate_limit_statuses(self):
        self.process.side_effect = None
        for status in (b"401", b"403", b"429"):
            with self.subTest(status=status):
                self.process.return_value = subprocess.CompletedProcess([], 22, status, b"HTTP error")
                body, _ = downloader.curl("https://example.invalid/asset")
                self.assertEqual(body[-3:], status)

    def test_curl_preserves_success_body(self):
        self.process.side_effect = None
        self.process.return_value = subprocess.CompletedProcess([], 0, b'{"ok": true}200', b"")
        self.assertEqual(downloader.curl("https://example.invalid/asset"), (b'{"ok": true}200', ""))

    def test_manifest_fetches_beyond_two_hundred_pages(self):
        pages = [self.page([{"assetId": str(n)}], str(n + 1) if n < 201 else None)
                 for n in range(202)]
        request = self.patch(downloader, "curl", side_effect=pages)
        assets = downloader.fetch_manifest()
        self.assertEqual(len(assets), 202)
        self.assertEqual(assets[-1]["assetId"], "201")
        self.assertEqual(request.call_count, 202)

    def test_manifest_rejects_repeated_tokens_and_empty_intermediate_pages(self):
        cases = {
            "repeated": [self.page([{}], "a"), self.page([{}], "a")],
            "cycle": [self.page([{}], "a"), self.page([{}], "b"), self.page([{}], "a")],
            "empty": [self.page([], "next")],
        }
        for name, pages in cases.items():
            with self.subTest(name=name), mock.patch.object(downloader, "curl", side_effect=pages):
                with self.assertRaises(RuntimeError):
                    downloader.fetch_manifest()

    def test_complete_requires_a_regular_file_and_valid_size(self):
        directory = self.dest / "directory"
        directory.mkdir()
        cases = [
            ({"path": str(directory), "size": 0}, False),
            (self.item("missing", size=0), False),
            (self.item("empty", size=0, payload=b""), False),
            (self.item("unknown", size=0, payload=b"data"), True),
            (self.item("matching", size=4, payload=b"data"), True),
            (self.item("truncated", size=4, payload=b"x"), False),
        ]
        for item, expected in cases:
            with self.subTest(path=item["path"]):
                self.assertEqual(downloader.complete(item), expected)

    def test_download_all_counts_failures_and_keeps_only_complete_files(self):
        existing = self.item("existing", payload=b"data")
        good = self.item("good")
        bad = self.item("bad")

        def transfer(url, out):
            self.assertNotEqual(url, existing["url"])
            Path(out).write_bytes(b"data" if url == good["url"] else b"x")
            return b"200", ""

        self.patch(downloader, "curl", side_effect=transfer)
        self.assertEqual(downloader.download_all([existing, good, bad], workers=2), 1)
        self.assertEqual(Path(good["path"]).read_bytes(), b"data")
        self.assertFalse(Path(bad["path"]).exists())
        self.assertFalse(Path(bad["path"] + ".part").exists())
        self.assertEqual(downloader.download_all([existing, good], workers=2), 0)

    def test_download_does_not_accept_an_empty_unknown_size_file(self):
        item = self.item(size=0)

        def transfer(url, out):
            Path(out).write_bytes(b"")
            return b"200", ""

        self.patch(downloader, "curl", side_effect=transfer)
        self.assertEqual(downloader.download(item)[0], "fail")
        self.assertFalse(Path(item["path"]).exists())

    def test_metadata_keeps_valid_old_files_and_replaces_current_rows(self):
        current = self.item("current", payload=b"data")
        current["gen"] = {"genType": "videoGen", "prompt": "新的提示词"}
        old = []
        for asset_id, size, payload in (
            ("current", 4, b"data"), ("removed", 4, b"data"),
            ("unknown", 0, b"data"), ("truncated", 4, b"x"),
            ("empty", 0, b""), ("missing", 4, None),
        ):
            item = self.item(asset_id, size, payload)
            old.append({"id": asset_id, "file": item["rel"], "size": size, "prompt": "cached"})
        (self.dest / "directory").mkdir()
        old.append({"id": "directory", "file": "directory", "size": 0})
        downloader.write_metadata([current], old)
        rows = {row["id"]: row for row in self.metadata()}
        self.assertEqual(set(rows), {"current", "removed", "unknown"})
        self.assertEqual(rows["current"]["prompt"], "新的提示词")
        self.assertEqual(rows["removed"]["prompt"], "cached")

    def test_prompt_lookup_groups_assets_from_the_same_conversation(self):
        first = self.item("first", payload=b"data")
        second = self.item("second", payload=b"data")
        api = self.patch(downloader, "chat_api", side_effect=[
            ("200", {"responseNodes": [
                {"responseId": first["resp"], "parentResponseId": "human"},
                {"responseId": second["resp"], "parentResponseId": "human"},
            ]}),
            ("200", {"responses": [{"responseId": "human", "mediaGenInput": {
                "videoGen": {"prompt": "A quiet sea", "modelName": "example-model"}
            }}]}),
        ])
        downloader.fetch_prompts([first, second])
        self.assertEqual(api.call_count, 2)
        self.assertEqual(api.call_args_list[1].args[1], {"responseIds": ["human"]})
        self.assertEqual(first["gen"]["prompt"], "A quiet sea")
        self.assertEqual(second["gen"], first["gen"])

    def test_main_reuses_prompt_cache_in_metadata_only_mode(self):
        item = self.item(payload=b"data")
        downloader.write_metadata([], [{
            "id": item["id"], "file": item["rel"], "size": 4,
            "genType": "videoGen", "prompt": "Cached prompt", "model": "example-model",
        }])
        self.patch(downloader, "fetch_manifest", return_value=[self.asset()])
        downloads = self.patch(downloader, "download_all")
        lookup = self.patch(downloader, "chat_api", side_effect=AssertionError("Cache was ignored"))
        downloader.main(["--meta-only", "--prompts"])
        downloads.assert_not_called()
        lookup.assert_not_called()
        self.assertEqual(self.metadata()[0]["prompt"], "Cached prompt")
        self.assertEqual(self.metadata()[0]["model"], "example-model")

    def test_main_saves_successful_and_previous_metadata_before_failure_exit(self):
        previous = self.item("previous", payload=b"data")
        downloader.write_metadata([previous], [])
        self.patch(downloader, "fetch_manifest", return_value=[self.asset("good"), self.asset("bad")])

        def transfer(url, out):
            if url.endswith("good.mp4"):
                Path(out).write_bytes(b"data")
                return b"200", ""
            return b"500", "simulated failure"

        self.patch(downloader, "curl", side_effect=transfer)
        with self.assertRaises(SystemExit) as result:
            downloader.main(["--workers", "2"])
        self.assertEqual(result.exception.code, 1)
        self.assertEqual({row["id"] for row in self.metadata()}, {"previous", "good"})

    def test_main_merges_partial_generation_metadata_without_losing_cached_fields(self):
        item = self.item(payload=b"data")
        downloader.write_metadata([], [{
            "id": item["id"], "file": item["rel"], "size": 4,
            "genType": "videoGen", "prompt": "Cached prompt", "model": "old-model",
            "aspectRatio": "16:9", "duration": 6, "resolution": "720p",
        }])
        asset = self.asset()
        asset["mediaGenInput"] = {"videoGen": {
            "modelName": "new-model", "prompt": None, "aspectRatio": "",
        }}
        self.patch(downloader, "fetch_manifest", return_value=[asset])
        downloads = self.patch(downloader, "download_all")

        downloader.main(["--meta-only"])

        downloads.assert_not_called()
        self.assertEqual(self.metadata(), [{
            "file": item["rel"], "id": item["id"], "mime": "video/mp4",
            "created": "2026-01-02T03:04:05Z", "size": 4, "w": None, "h": None,
            "genType": "videoGen", "prompt": "Cached prompt", "model": "new-model",
            "aspectRatio": "16:9", "duration": 6, "resolution": "720p",
        }])

    def test_missing_metadata_loads_as_empty(self):
        self.assertEqual(downloader.load_metadata(), [])

    def test_main_does_not_overwrite_invalid_metadata(self):
        self.patch(downloader, "fetch_manifest", return_value=[])
        writer = self.patch(downloader, "write_metadata")
        path = self.dest / "metadata.json"
        for original in ('{"broken":', '{}', '[null]'):
            with self.subTest(original=original):
                path.write_text(original, encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    downloader.main(["--meta-only"])
                self.assertEqual(path.read_text(encoding="utf-8"), original)
        writer.assert_not_called()

    def test_main_rejects_nonpositive_workers_before_fetching(self):
        listing = self.patch(downloader, "fetch_manifest")
        for workers in ("0", "-2"):
            with self.subTest(workers=workers), self.assertRaises(SystemExit) as result:
                downloader.main(["--workers", workers])
            self.assertEqual(result.exception.code, 2)
        listing.assert_not_called()


if __name__ == "__main__":
    unittest.main()
