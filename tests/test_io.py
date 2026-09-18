from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mtgwiki import read_jsonl, write_json, write_jsonl


class IOTests(unittest.TestCase):
    def test_write_json(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = write_json(Path(tempdir) / "data.json", {"x": "åäö"})
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["x"], "åäö")

    def test_jsonl_roundtrip(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = write_jsonl(Path(tempdir) / "data.jsonl", [{"x": 1}, {"x": 2}])
            self.assertEqual(read_jsonl(path), [{"x": 1}, {"x": 2}])


if __name__ == "__main__":
    unittest.main()
