#!/usr/bin/env python3
"""Unit tests for AGY Mod Updater & GitHub Release integration."""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agy_modules.infrastruktur.updater import (
    check_for_updates,
    fetch_latest_release,
    get_current_version,
    parse_semver,
)


class TestUpdater(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.cache_file = os.path.join(self.test_dir, "update_cache.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_parse_semver(self):
        self.assertEqual(parse_semver("1.5.0"), (1, 5, 0))
        self.assertEqual(parse_semver("v1.5.1"), (1, 5, 1))
        self.assertEqual(parse_semver("v2.0.0-rc1"), (2, 0, 0, 1))
        self.assertTrue(parse_semver("1.5.1") > parse_semver("1.5.0"))
        self.assertTrue(parse_semver("v2.0.0") > parse_semver("v1.9.9"))
        self.assertFalse(parse_semver("1.5.0") > parse_semver("1.5.0"))
        self.assertFalse(parse_semver("1.4.9") > parse_semver("1.5.0"))

    def test_get_current_version(self):
        version_file = os.path.join(self.test_dir, "VERSION")
        with open(version_file, "w") as fh:
            fh.write("2.1.0\n")
        v = get_current_version(self.test_dir)
        self.assertEqual(v, "2.1.0")

    @patch("agy_modules.infrastruktur.updater.fetch_latest_release")
    def test_check_for_updates_detects_newer_version(self, mock_fetch):
        mock_cache_path = os.path.join(self.test_dir, "cache.json")
        mock_fetch.return_value = {
            "tag_name": "v1.6.0",
            "html_url": "https://github.com/bartman081523/agy/releases/tag/v1.6.0",
            "assets": [
                {
                    "name": "agy-release.tar.gz",
                    "browser_download_url": "https://github.com/bartman081523/agy/releases/download/v1.6.0/agy-release.tar.gz",
                }
            ],
            "body": "Test release notes",
        }

        with patch("agy_modules.infrastruktur.updater.CACHE_FILE", mock_cache_path):
            with patch("agy_modules.infrastruktur.updater.get_current_version", return_value="1.5.0"):
                res = check_for_updates(force=True)
                self.assertTrue(res["has_update"])
                self.assertEqual(res["latest_version"], "1.6.0")
                self.assertEqual(res["current_version"], "1.5.0")
                self.assertIn("agy-release.tar.gz", res["download_url"])

    @patch("agy_modules.infrastruktur.updater.fetch_latest_release")
    def test_check_for_updates_no_newer_version(self, mock_fetch):
        mock_cache_path = os.path.join(self.test_dir, "cache.json")
        mock_fetch.return_value = {
            "tag_name": "v1.5.0",
            "html_url": "https://github.com/bartman081523/agy/releases/tag/v1.5.0",
            "assets": [],
            "body": "",
        }

        with patch("agy_modules.infrastruktur.updater.CACHE_FILE", mock_cache_path):
            with patch("agy_modules.infrastruktur.updater.get_current_version", return_value="1.5.0"):
                res = check_for_updates(force=True)
                self.assertFalse(res["has_update"])
                self.assertEqual(res["latest_version"], "1.5.0")

    def test_cache_hit_prevents_network_call(self):
        cache_data = {
            "has_update": True,
            "current_version": "1.5.0",
            "latest_version": "1.5.9",
            "tag_name": "v1.5.9",
            "download_url": "https://example.com/agy.tar.gz",
            "timestamp": 9999999999.0,  # far future
        }
        with open(self.cache_file, "w") as cf:
            json.dump(cache_data, cf)

        with patch("agy_modules.infrastruktur.updater.CACHE_FILE", self.cache_file):
            with patch("agy_modules.infrastruktur.updater.fetch_latest_release") as mock_fetch:
                with patch("agy_modules.infrastruktur.updater.get_current_version", return_value="1.5.0"):
                    res = check_for_updates(force=False)
                    mock_fetch.assert_not_called()
                    self.assertTrue(res["has_update"])
                    self.assertEqual(res["latest_version"], "1.5.9")


if __name__ == "__main__":
    unittest.main()
