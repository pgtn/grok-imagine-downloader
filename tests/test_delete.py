"""Offline tests for delete.py: python -m unittest discover -s tests -v."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import delete
import download as dl


class DeleteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dest = Path(temp.name) / "archive"
        (self.dest / "chat").mkdir(parents=True)
        (self.dest / "image").mkdir()
        self.cookies = Path(temp.name) / "cookies.txt"
        self.cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        for name, data in (("chat/a.png", b"aaa"), ("chat/b.png", b"bb"), ("image/c.jpg", b"c")):
            (self.dest / name).write_bytes(data)
        rows = [
            {"file": "chat/a.png", "id": "a", "size": 3},
            {"file": "chat/b.png", "id": "b", "size": 99},  # partial file: never a target
            {"file": "chat/missing.png", "id": "m", "size": 1},  # not on disk: never a target
            {"file": "image/c.jpg", "id": "c", "size": 1},
        ]
        (self.dest / "metadata.json").write_text(json.dumps(rows), encoding="utf-8")
        for name in ("DEST", "COOKIES", "UA"):
            self.patch(dl, name, getattr(dl, name))
        self.patch(dl, "chrome_ua", return_value="offline-test-UA")
        self.patch(dl.shutil, "which", return_value="curl")
        self.patch(delete.time, "sleep")
        self.patch(dl.time, "sleep")
        self.calls = []
        self.responses = {}
        self.patch(dl, "curl", side_effect=self.fake_curl)
        for stream in ("stdout", "stderr"):
            self.patch(sys, stream, io.StringIO())

    def patch(self, target, name, *args, **kwargs):
        patcher = mock.patch.object(target, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def fake_curl(self, url, method=None, **kwargs):
        self.calls.append((method or "GET", url))
        code = self.responses.get((method or "GET", url), "404" if method is None else "200")
        return code.encode(), ""

    def run_delete(self, *args):
        return delete.main(["--dest", str(self.dest), "--cookies", str(self.cookies), *args])

    def log(self):
        path = self.dest / "deleted.jsonl"
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_dry_run_sends_nothing_and_lists_only_complete_files_of_the_kind(self):
        self.run_delete("--only", "chat")
        self.assertEqual(self.calls, [])
        out = sys.stdout.getvalue()
        self.assertIn("Targets: 1 assets", out)
        self.assertIn("chat/a.png", out)
        self.assertNotIn("chat/b.png", out)
        self.assertEqual(self.log(), [])

    def test_only_is_required(self):
        with self.assertRaises(SystemExit):
            self.run_delete("--yes")
        self.assertEqual(self.calls, [])

    def test_soft_delete_checks_the_asset_is_gone_and_logs_it(self):
        self.run_delete("--only", "chat,image", "--yes")
        self.assertEqual(self.calls, [
            ("DELETE", "https://grok.com/rest/assets-metadata/a"), ("GET", "https://grok.com/rest/assets/a"),
            ("DELETE", "https://grok.com/rest/assets-metadata/c"), ("GET", "https://grok.com/rest/assets/c"),
        ])
        self.assertEqual([(r["id"], r["mode"], r["result"]) for r in self.log()],
                         [("a", "soft", "deleted"), ("c", "soft", "deleted")])

    def test_second_run_skips_logged_assets(self):
        self.run_delete("--only", "chat", "--yes")
        self.calls.clear()
        self.run_delete("--only", "chat", "--yes")
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.log()), 1)

    def test_hard_mode_uses_the_asset_endpoint(self):
        self.run_delete("--only", "image", "--yes", "--hard")
        self.assertEqual(self.calls[0], ("DELETE", "https://grok.com/rest/assets/c"))
        self.assertEqual(self.log()[0]["mode"], "hard")

    def test_limit_caps_the_number_of_deletions(self):
        self.run_delete("--only", "chat,image", "--yes", "--limit", "1")
        self.assertEqual([r["id"] for r in self.log()], ["a"])

    def test_asset_already_absent_is_logged_as_gone(self):
        self.responses[("DELETE", "https://grok.com/rest/assets-metadata/a")] = "404"
        self.run_delete("--only", "chat", "--yes")
        self.assertEqual(self.calls, [("DELETE", "https://grok.com/rest/assets-metadata/a")])
        self.assertEqual(self.log()[0]["result"], "gone")

    def test_stops_when_the_asset_is_still_there_after_delete(self):
        self.responses[("GET", "https://grok.com/rest/assets/a")] = "200"
        with self.assertRaises(SystemExit) as cm:
            self.run_delete("--only", "chat,image", "--yes")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(len(self.calls), 2)  # never moved on to asset c
        self.assertEqual(self.log(), [])

    def test_stops_on_expired_cookie(self):
        self.responses[("DELETE", "https://grok.com/rest/assets-metadata/a")] = "401"
        with self.assertRaises(SystemExit):
            self.run_delete("--only", "chat,image", "--yes")
        self.assertEqual(len(self.calls), 1)
        self.assertIn("cookie", sys.stderr.getvalue())

    def test_rate_limit_is_retried(self):
        seq = iter(["429", "200"])
        self.patch(dl, "curl", side_effect=lambda url, method=None, **kw:
                   ((next(seq) if method else "404").encode(), ""))
        self.run_delete("--only", "chat", "--yes")
        self.assertEqual(self.log()[0]["result"], "deleted")


class CurlTests(unittest.TestCase):
    def test_curl_sends_the_method_and_extra_headers(self):
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout=b"{}200", stderr=b""))
        with mock.patch.object(dl.subprocess, "run", run):
            dl.curl("https://grok.com/rest/assets-metadata/a", method="DELETE", headers=("origin: https://grok.com",))
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("-X") + 1], "DELETE")
        self.assertIn("origin: https://grok.com", cmd)


if __name__ == "__main__":
    unittest.main()
