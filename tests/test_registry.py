"""Tests for tools/registry.py that need no network: python3 -m unittest"""
import hashlib
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import registry  # noqa: E402

SHA = "a" * 64
PNG = b"\x89PNG\r\n\x1a\n a picture"
PHONE = b"\x89PNG\r\n\x1a\n another"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def entry(**over):
    e = {
        "id": "trunkamp",
        "name": "Trunkamp",
        "description": "A Winamp-style player.",
        "repository": "https://github.com/someone/trunkamp",
        "license": "MIT",
        "tier": "community",
        "version": "1.2.0",
        "api": 1,
        "access": "listen",
        "tag": "v1.2.0",
        "commit": "0123456789abcdef0123456789abcdef01234567",
    }
    e["asset"] = {"url": registry.asset_url(e), "sha256": SHA}
    e.update(over)
    return e


def manifest(**over):
    m = {"id": "trunkamp", "name": "Trunkamp", "version": "1.2.0", "description": "A Winamp-style player.", "api": 1, "access": "listen", "license": "MIT"}
    m.update(over)
    return m


def shots_manifest(**over):
    """interface.json with two screenshots."""
    return manifest(screenshots=[{"path": "screenshots/player.png", "caption": "The player"}, {"path": "screenshots/phone.png"}], **over)


def shots_entry(**over):
    """The entry for shots_release(): its screenshots pinned, as add-release writes them."""
    e = entry(**over)
    e["screenshots"] = [{"url": registry.release_url(e, "trunkamp-1.2.0-player.png"), "sha256": sha(PNG), "caption": "The player"},
                        {"url": registry.release_url(e, "trunkamp-1.2.0-phone.png"), "sha256": sha(PHONE)}]
    return e


def zip_of(files, links=()):
    """A zip holding `files` ({name: bytes or a dict, written as JSON}) and symbolic links."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, json.dumps(data) if isinstance(data, dict) else data)
        for name in links:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            z.writestr(info, "/etc/passwd")
    return buf.getvalue()


def release(**over):
    """A release's zip, as the template's workflow builds it."""
    files = {"trunkamp-1.2.0/index.html": b"<!doctype html>", "trunkamp-1.2.0/interface.json": manifest(), "trunkamp-1.2.0/skin/style.css": b"body{}"}
    files.update(over)
    return zip_of({k: v for k, v in files.items() if v is not None})


def shots_release(**over):
    files = {"trunkamp-1.2.0/interface.json": shots_manifest(), "trunkamp-1.2.0/screenshots/player.png": PNG, "trunkamp-1.2.0/screenshots/phone.png": PHONE}
    files.update(over)
    return release(**files)


class Validate(unittest.TestCase):
    def test_a_good_entry(self):
        self.assertEqual(registry.validate(entry(), "trunkamp"), [])
        self.assertEqual(registry.validate(entry(homepage="https://example.com"), "trunkamp"), [])
        self.assertEqual(registry.validate(entry(access="control")), [])

    def test_the_file_name_is_the_id(self):
        self.assertIn("id: 'trunkamp', but the file is interfaces/other.json", registry.validate(entry(), "other"))

    def test_ids_as_the_recorder_takes_them(self):
        for good in ("a", "7", "wall-2", "x" * 64):
            e = entry(id=good)
            e["asset"]["url"] = registry.asset_url(e)
            self.assertEqual(registry.validate(e), [], good)
        for bad in ("", "-wall", "Wall", "wall_2", "../x", "x" * 65, 7):
            self.assertTrue(any(p.startswith("id:") for p in registry.validate(entry(id=bad))), bad)

    def test_official_only_in_the_organization(self):
        self.assertTrue(any("only interfaces in" in p for p in registry.validate(entry(tier="official"))))
        e = entry(repository="https://github.com/TrunkRecorder/trunkamp", tier="official")
        e["asset"]["url"] = registry.asset_url(e)
        self.assertEqual(registry.validate(e), [])
        self.assertTrue(registry.validate(entry(tier="unlisted")))

    def test_the_url_must_be_the_releases_own(self):
        for url in ["https://evil.example/trunkamp-1.2.0.zip",
                    "https://github.com/other/trunkamp/releases/download/v1.2.0/trunkamp-1.2.0.zip",
                    "https://github.com/someone/trunkamp/releases/download/v1.1.0/trunkamp-1.2.0.zip",
                    "https://github.com/someone/trunkamp/releases/download/v1.2.0/trunkamp.zip"]:
            self.assertTrue(any(p.startswith("asset.url") for p in registry.validate(entry(asset={"url": url, "sha256": SHA}))), url)
        # Also when something else is wrong too.
        e = entry(tier="official", asset={"url": "https://evil.example/x.zip", "sha256": SHA})
        self.assertTrue(any(p.startswith("asset.url") for p in registry.validate(e)))

    def test_tag_version_api_access_commit_sha(self):
        self.assertTrue(registry.validate(entry(tag="1.2.0")))
        self.assertTrue(registry.validate(entry(api=2)))
        self.assertTrue(registry.validate(entry(access="admin")))
        self.assertTrue(registry.validate(entry(commit="abc123")))
        self.assertTrue(registry.validate(entry(version="1.2")))
        e = entry()
        e["asset"]["sha256"] = "A" * 64
        self.assertTrue(registry.validate(e))

    def test_unknown_and_missing_fields(self):
        e = entry(extra=1, homepage="http://example.com")
        del e["license"], e["access"]
        p = registry.validate(e)
        self.assertIn("extra: not a field the registry knows", p)
        self.assertIn("license: missing", p)
        self.assertIn("access: missing", p)
        self.assertTrue(any(x.startswith("homepage:") for x in p))

    def test_written_in_the_listings_order(self):
        e = shots_entry(homepage="https://example.com")
        e["screenshots"][0] = dict(reversed(list(e["screenshots"][0].items())))
        written = json.loads(registry.dump(dict(reversed(list(e.items())))))
        self.assertEqual(list(written), registry.FIELDS)
        self.assertEqual(list(written["screenshots"][0]), registry.SHOT_FIELDS)
        self.assertEqual(list(written["screenshots"][1]), ["url", "sha256"])
        self.assertNotIn("homepage", json.loads(registry.dump(entry())))
        self.assertNotIn("screenshots", json.loads(registry.dump(entry())))


class Screenshots(unittest.TestCase):
    def problems(self, e):
        return [p for p in registry.validate(e) if p.startswith("screenshots")]

    def test_good_ones(self):
        self.assertEqual(registry.validate(shots_entry(), "trunkamp"), [])
        for name in ("trunkamp-1.2.0-a.jpg", "trunkamp-1.2.0-b.JPEG", "trunkamp-1.2.0-c.webp"):
            e = entry()
            e["screenshots"] = [{"url": registry.release_url(e, name), "sha256": SHA}]
            self.assertEqual(registry.validate(e), [], name)

    def test_from_the_entrys_own_release_with_the_expected_name(self):
        e = entry()
        for url in ["https://evil.example/trunkamp-1.2.0-player.png",
                    "https://github.com/other/trunkamp/releases/download/v1.2.0/trunkamp-1.2.0-player.png",
                    "https://github.com/someone/trunkamp/releases/download/v1.1.0/trunkamp-1.2.0-player.png",
                    registry.release_url(e, "player.png"),
                    registry.release_url(e, "trunkamp-1.1.0-player.png"),
                    registry.release_url(e, "trunkamp-1.2.0-"),
                    registry.release_url(e, "trunkamp-1.2.0-player.gif"),
                    registry.release_url(e, "trunkamp-1.2.0-x/player.png")]:
            self.assertTrue(any(p.startswith("screenshots[0].url") for p in self.problems(entry(screenshots=[{"url": url, "sha256": SHA}]))), url)

    def test_fields_count_checksums_captions_names(self):
        e = shots_entry()
        e["screenshots"][1]["sha256"] = "B" * 64
        self.assertIn("screenshots[1].sha256: 64 lowercase hex digits", self.problems(e))
        e = shots_entry()
        e["screenshots"][0]["caption"] = ""
        self.assertTrue(any(p.startswith("screenshots[0].caption") for p in self.problems(e)))
        e = shots_entry()
        e["screenshots"][0]["path"] = "screenshots/player.png"
        self.assertIn("screenshots[0]: url, sha256 and (if it has one) caption", self.problems(e))
        e = shots_entry()
        e["screenshots"][1] = dict(e["screenshots"][0])
        self.assertTrue(any("twice" in p for p in self.problems(e)))
        e = entry()
        e["screenshots"] = [{"url": registry.release_url(e, f"trunkamp-1.2.0-{n}.png"), "sha256": SHA} for n in range(7)]
        self.assertIn("screenshots: at most 6", self.problems(e))
        self.assertTrue(self.problems(entry(screenshots=[])))
        self.assertTrue(self.problems(entry(screenshots={"url": "x"})))


class Manifest(unittest.TestCase):
    def test_matches(self):
        self.assertEqual(registry.check_manifest(manifest(), entry()), [])
        self.assertEqual(registry.check_manifest(manifest(repository="https://github.com/someone/trunkamp.git"), entry()), [])
        self.assertEqual(registry.check_manifest(manifest(homepage="https://example.com"), entry(homepage="https://example.com")), [])
        # access is listen when it's left out.
        m = manifest()
        del m["access"]
        self.assertEqual(registry.check_manifest(m, entry()), [])

    def test_differs(self):
        for k, v in [("id", "other"), ("name", "Other"), ("description", "x"), ("version", "1.1.0"), ("api", 2), ("access", "control"),
                     ("license", "GPL-3.0"), ("homepage", "https://example.com"), ("repository", "https://github.com/other/trunkamp")]:
            self.assertTrue(registry.check_manifest(manifest(**{k: v}), entry()), k)
        self.assertTrue(registry.check_manifest([], entry()))


class ManifestScreenshots(unittest.TestCase):
    def test_match_the_entry_in_order_with_captions(self):
        self.assertEqual(registry.check_manifest(shots_manifest(), shots_entry()), [])
        self.assertEqual(registry.check_manifest(manifest(), entry()), [])

    def test_differ(self):
        def differs(m, e):
            return any(p.startswith("screenshots:") for p in registry.check_manifest(m, e))
        self.assertTrue(differs(shots_manifest(), entry()), "the entry has none")
        self.assertTrue(differs(manifest(), shots_entry()), "interface.json has none")
        m = shots_manifest()
        m["screenshots"].reverse()
        self.assertTrue(differs(m, shots_entry()), "order")
        m = shots_manifest()
        m["screenshots"][0]["caption"] = "Something else"
        self.assertTrue(differs(m, shots_entry()), "caption")
        m = shots_manifest()
        m["screenshots"][1]["caption"] = "Now it has one"
        self.assertTrue(differs(m, shots_entry()), "a caption added")
        m = shots_manifest()
        m["screenshots"][0]["path"] = "shots/player-2.png"
        self.assertTrue(differs(m, shots_entry()), "file name")

    def test_paths_as_the_recorder_takes_them(self):
        for bad in ("../a.png", "/etc/a.png", ".hidden/a.png", "a/.b.png", "./a.png", "a//b.png", "a\\b.png", "a.svg", "a", ""):
            m = manifest(screenshots=[{"path": bad}])
            self.assertTrue(any(p.startswith("interface.json: screenshot") for p in registry.check_manifest(m, entry())), bad)
        m = manifest(screenshots=[{"path": "a/x.png"}, {"path": "b/x.png"}])
        self.assertTrue(any("two screenshots named x.png" in p for p in registry.check_manifest(m, entry())))
        m = manifest(screenshots=[{"path": f"{n}.png"} for n in range(7)])
        self.assertIn("interface.json: at most 6 screenshots", registry.check_manifest(m, entry()))
        self.assertTrue(registry.check_manifest(manifest(screenshots="a.png"), entry()))
        self.assertTrue(registry.check_manifest(manifest(screenshots=[{"path": "a.png", "caption": 3}]), entry()))


class Sums(unittest.TestCase):
    def test_parse(self):
        text = f"{SHA}  trunkamp-1.2.0.zip\n{'B' * 64} *SHA-binary-mode\n\n"
        self.assertEqual(registry.parse_sums(text), {"trunkamp-1.2.0.zip": SHA, "SHA-binary-mode": "b" * 64})


class Archive(unittest.TestCase):
    def problems(self, data, e=None):
        return registry.check_archive(data, e or entry())

    def test_a_good_archive(self):
        self.assertEqual(self.problems(release()), [])
        self.assertEqual(registry.read_archive(release(), "trunkamp", "1.2.0")["name"], "Trunkamp")
        # Folders as entries of their own are fine.
        self.assertEqual(self.problems(release(**{"trunkamp-1.2.0/": b"", "trunkamp-1.2.0/skin/": b""})), [])

    def test_needs_index_html_and_interface_json(self):
        self.assertIn("no trunkamp-1.2.0/index.html", self.problems(release(**{"trunkamp-1.2.0/index.html": None}))[0])
        self.assertIn("no trunkamp-1.2.0/interface.json", self.problems(release(**{"trunkamp-1.2.0/interface.json": None}))[0])

    def test_one_folder_named_for_the_release(self):
        self.assertIn("outside trunkamp-1.2.0/", self.problems(release(**{"README.md": b"hi"}))[0])
        self.assertIn("outside trunkamp-1.2.0/", self.problems(zip_of({"trunkamp/index.html": b"", "trunkamp/interface.json": manifest()}))[0])
        # Files loose, with no folder.
        self.assertTrue(self.problems(zip_of({"index.html": b"", "interface.json": manifest()})))

    def test_refuses_escaping_paths_and_links(self):
        for bad in [release(**{"trunkamp-1.2.0/../../x": b"x"}), release(**{"/abs": b"x"}), release(**{"trunkamp-1.2.0\\..\\x": b"x"}),
                    zip_of({"trunkamp-1.2.0/index.html": b"", "trunkamp-1.2.0/interface.json": manifest()}, links=["trunkamp-1.2.0/app.js"])]:
            self.assertTrue(self.problems(bad))

    def test_hidden_files_and_the_recorders_paths(self):
        self.assertIn("hidden", self.problems(release(**{"trunkamp-1.2.0/.DS_Store": b""}))[0])
        self.assertIn("hidden", self.problems(release(**{"trunkamp-1.2.0/.git/config": b""}))[0])
        self.assertIn("recorder's", self.problems(release(**{"trunkamp-1.2.0/api/client.js": b""}))[0])
        self.assertIn("recorder's", self.problems(release(**{"trunkamp-1.2.0/calls/x.wav": b""}))[0])
        # Only at the top: a folder of its own called api is fine further down.
        self.assertEqual(self.problems(release(**{"trunkamp-1.2.0/lib/api/x.js": b""})), [])

    def test_the_manifest_must_agree(self):
        self.assertIn("interface.json says version", self.problems(release(**{"trunkamp-1.2.0/interface.json": manifest(version="1.1.0")}))[0])
        self.assertIn("interface.json says access", self.problems(release(**{"trunkamp-1.2.0/interface.json": manifest(access="control")}))[0])
        self.assertIn("isn't readable JSON", self.problems(release(**{"trunkamp-1.2.0/interface.json": b"{nope"}))[0])

    def test_screenshots_in_the_archive(self):
        self.assertEqual(self.problems(shots_release(), shots_entry()), [])
        # Missing from the zip.
        p = self.problems(shots_release(**{"trunkamp-1.2.0/screenshots/phone.png": None}), shots_entry())
        self.assertEqual(p, ["screenshot screenshots/phone.png: not in the archive"])
        # Not the picture the release has on its own.
        p = self.problems(shots_release(**{"trunkamp-1.2.0/screenshots/player.png": b"\x89PNG changed"}), shots_entry())
        self.assertEqual(p, ["screenshot screenshots/player.png: not the same picture as trunkamp-1.2.0-player.png (their sha256 differ)"])
        e = shots_entry()
        e["screenshots"][1]["sha256"] = SHA
        self.assertIn("trunkamp-1.2.0-phone.png", self.problems(shots_release(), e)[0])
        # Too big for the recorder to show.
        with mock.patch.object(registry, "MAX_SCREENSHOT", 12):
            self.assertIn("over", self.problems(shots_release(), shots_entry())[0])
        # The entry's screenshots must be interface.json's.
        self.assertTrue(self.problems(release(), shots_entry()))
        self.assertTrue(self.problems(shots_release(), entry()))

    def test_not_a_zip_or_too_big(self):
        self.assertIn("not a readable zip", self.problems(b"PK nope")[0])
        with mock.patch.object(registry, "MAX_ARCHIVE", 100):
            self.assertIn("over", self.problems(release())[0])
        with mock.patch.object(registry, "MAX_UNPACKED", 10):
            self.assertIn("unpacks to more than", self.problems(release())[0])


class Index(unittest.TestCase):
    def test_the_index_is_up_to_date(self):
        self.assertEqual(registry.INDEX.read_text(), registry.build_index(write=False))

    def test_every_entry_is_valid(self):
        for p in sorted(registry.INTERFACES.glob("*.json")):
            with self.subTest(p.name):
                self.assertEqual(registry.validate(json.loads(p.read_text()), p.stem), [])

    def test_what_the_recorder_reads(self):
        with mock.patch.object(registry, "entries", lambda: [shots_entry(id="b", homepage="https://b.example"), entry(id="a")]):
            index = json.loads(registry.build_index(write=False))
        self.assertEqual(index["format"], 1)
        self.assertEqual([e["id"] for e in index["interfaces"]], ["a", "b"])
        self.assertEqual(list(index["interfaces"][1]), registry.FIELDS)
        self.assertNotIn("screenshots", index["interfaces"][0])
        self.assertEqual(index["interfaces"][1]["screenshots"][1], {"url": "https://github.com/someone/trunkamp/releases/download/v1.2.0/trunkamp-1.2.0-phone.png", "sha256": sha(PHONE)})
        with mock.patch.object(registry, "entries", lambda: []):
            self.assertEqual(registry.build_index(write=False), '{\n  "format": 1,\n  "interfaces": []\n}\n')


class AddRelease(unittest.TestCase):
    """make_entry: what add-release writes, from a release's files."""

    def files(self, e, *names):
        return {n: (registry.release_url(e, n), sha(PNG if "player" in n else PHONE)) for n in names}

    def test_screenshots_from_the_release(self):
        e = shots_entry()
        files = self.files(e, "trunkamp-1.2.0.zip", "trunkamp-1.2.0-player.png", "trunkamp-1.2.0-phone.png")
        made = registry.make_entry(shots_manifest(), e["repository"], "v1.2.0", e["commit"], "community", e["asset"], files)
        self.assertEqual(registry.dump(made), registry.dump(e))
        self.assertEqual(registry.check_archive(shots_release(), made), [])

    def test_a_screenshot_missing_from_the_release(self):
        e = shots_entry()
        files = self.files(e, "trunkamp-1.2.0-player.png")
        with self.assertRaises(registry.Problem) as c:
            registry.make_entry(shots_manifest(), e["repository"], "v1.2.0", e["commit"], "community", e["asset"], files)
        self.assertIn("trunkamp-1.2.0-phone.png", str(c.exception))

    def test_none_and_homepage(self):
        e = entry()
        made = registry.make_entry(manifest(homepage="https://example.com"), e["repository"], "v1.2.0", e["commit"], "community", e["asset"], {})
        self.assertNotIn("screenshots", made)
        self.assertEqual(registry.validate(made), [])
        self.assertEqual(list(json.loads(registry.dump(made))), [k for k in registry.FIELDS if k != "screenshots"])


if __name__ == "__main__":
    unittest.main()
