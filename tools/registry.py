#!/usr/bin/env python3
"""The registry's tools.

  registry.py add-release <repository URL> <tag> [--tier official|community]
      Write interfaces/<id>.json for a release, from its SHA256SUMS and the
      interface.json in its archive.
  registry.py check [interfaces/<id>.json ...] [--quick] [--changed-since <ref>]
      Check entries (all of them by default). --quick skips the network.
  registry.py build-index
      Rebuild index.json from interfaces/*.json.

Only the standard library, so it runs wherever Python 3.9+ does. Checking
build provenance needs the GitHub CLI, `gh`.
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTERFACES = ROOT / "interfaces"
INDEX = ROOT / "index.json"

# The interface API versions (`api`) the registry lists.
API_VERSIONS = {1}
ACCESS = {"listen", "control"}
TIERS = {"official", "community"}
# Only interfaces in this GitHub organization can be official.
OFFICIAL_OWNER = "TrunkRecorder"
# What the recorder downloads at most; an archive may unpack to no more.
MAX_ARCHIVE = 100 << 20
MAX_UNPACKED = 100 << 20
# Screenshots: at most this many, each at most this big, of these types (the recorder's limits).
MAX_SCREENSHOTS = 6
MAX_SCREENSHOT = 2 << 20
IMAGE_TYPES = {"png", "jpg", "jpeg", "webp"}
# Paths under an interface's folder that are the recorder's, not its.
RESERVED = {"api", "calls"}

# An entry's fields, in the order they're written (the recorder's Listing).
# homepage and screenshots are left out when there are none.
FIELDS = ["id", "name", "description", "repository", "homepage", "license", "tier", "version", "api", "access", "tag", "commit", "asset", "screenshots"]
OPTIONAL = {"homepage", "screenshots"}
SHOT_FIELDS = ["url", "sha256", "caption"]

# The recorder's rule: it names the folder the interface is installed in.
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
SEMVER_RE = re.compile(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?")
REPO_RE = re.compile(r"https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


class Problem(Exception):
    pass


# ─── GitHub ──────────────────────────────────────────────────────────────

def github(path):
    """GET from the GitHub API. GH_TOKEN or GITHUB_TOKEN, when set, raises the rate limit."""
    req = urllib.request.Request("https://api.github.com" + path, headers={"Accept": "application/vnd.github+json", "User-Agent": "trunk-recorder-interfaces"})
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        # Unredirected: never sent on to another host.
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise Problem(f"GitHub API {path}: {e.code} {e.reason}") from None


def download(url, limit=MAX_ARCHIVE):
    req = urllib.request.Request(url, headers={"User-Agent": "trunk-recorder-interfaces"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise Problem(f"{url}: {e.code} {e.reason}") from None
    if len(data) > limit:
        raise Problem(f"{url}: over {limit >> 20} MB, more than the recorder downloads")
    return data


def parse_repository(url):
    """(owner, repo) of a GitHub repository URL."""
    url = url.strip().rstrip("/")
    url = url[:-4] if url.endswith(".git") else url
    m = REPO_RE.fullmatch(url)
    if not m:
        raise Problem(f"{url}: not a GitHub repository URL (https://github.com/<owner>/<repo>)")
    return m.group(1), m.group(2)


def tag_commit(owner, repo, tag):
    """The commit a tag points at now (annotated tags followed)."""
    return github(f"/repos/{owner}/{repo}/commits/{tag}")["sha"]


def parse_sums(text):
    """SHA256SUMS (`<sha256>  <name>` lines) → {name: sha256}."""
    sums = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ─── Names ───────────────────────────────────────────────────────────────

def folder_name(id, version):
    return f"{id}-{version}"


def asset_name(id, version):
    return f"{folder_name(id, version)}.zip"


def release_url(entry, name):
    return f"{entry['repository']}/releases/download/{entry['tag']}/{name}"


def asset_url(entry):
    return release_url(entry, asset_name(entry["id"], entry["version"]))


def base_name(path):
    """A screenshot's file name: the last part of its path, as the recorder takes it."""
    return path.rsplit("/", 1)[-1]


def screenshot_name(id, version, path):
    """The release file a screenshot is also published as: <id>-<version>-<file name>."""
    return f"{folder_name(id, version)}-{base_name(path)}"


def is_image(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in IMAGE_TYPES


def screenshot_path_problem(path):
    """Why a screenshot path in interface.json isn't one the recorder takes,
    if it isn't: relative, no '..', nothing hidden, a .png, .jpg or .webp."""
    if not isinstance(path, str) or not path:
        return "with no path"
    parts = path.split("/")
    if path.startswith("/") or "\\" in path or any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        return f"{path!r}: a plain path in the interface's folder (no leading /, no .., nothing hidden)"
    if not is_image(path):
        return f"{path!r}: a .png, .jpg or .webp"
    return None


# ─── Entries ─────────────────────────────────────────────────────────────

def load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise Problem(f"{path}: {e}") from None


def dump(entry):
    ordered = {k: entry[k] for k in FIELDS if k in entry}
    if isinstance(ordered.get("screenshots"), list):
        ordered["screenshots"] = [{k: s[k] for k in SHOT_FIELDS if k in s} if isinstance(s, dict) else s for s in ordered["screenshots"]]
    return json.dumps(ordered, indent=2, ensure_ascii=False) + "\n"


def entries():
    return [load(p) for p in sorted(INTERFACES.glob("*.json"))]


def validate(entry, file_id=None):
    """What's wrong with an entry on its own, without the network."""
    p = []
    if not isinstance(entry, dict):
        return ["not a JSON object"]
    for k in FIELDS:
        if k not in entry and k not in OPTIONAL:
            p.append(f"{k}: missing")
    for k in entry:
        if k not in FIELDS:
            p.append(f"{k}: not a field the registry knows")
    id = entry.get("id", "")
    if not isinstance(id, str) or not ID_RE.fullmatch(id):
        p.append("id: lowercase letters, digits and dashes, not starting with a dash (up to 64 characters)")
    if file_id is not None and id != file_id:
        p.append(f"id: {id!r}, but the file is interfaces/{file_id}.json")
    for k in ("name", "description", "license"):
        if not isinstance(entry.get(k), str) or not entry.get(k, "").strip():
            p.append(f"{k}: empty")
    owner = None
    try:
        owner, _ = parse_repository(entry.get("repository", ""))
        if entry["repository"] != "https://github.com/{}/{}".format(*parse_repository(entry["repository"])):
            p.append("repository: write it as https://github.com/<owner>/<repo>")
    except (Problem, TypeError, AttributeError) as e:
        p.append(f"repository: {e}")
    if "homepage" in entry and not (isinstance(entry["homepage"], str) and entry["homepage"].startswith("https://")):
        p.append("homepage: an https:// URL (leave it out when there's none)")
    tier = entry.get("tier")
    if tier not in TIERS:
        p.append(f"tier: one of {', '.join(sorted(TIERS))}")
    elif tier == "official" and owner != OFFICIAL_OWNER:
        p.append(f"tier: only interfaces in github.com/{OFFICIAL_OWNER} are official")
    version = entry.get("version", "")
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        p.append("version: semver")
    if entry.get("tag") != f"v{version}":
        p.append(f"tag: v{version}, the release workflow's tag for this version")
    if entry.get("api") not in API_VERSIONS:
        p.append(f"api: {entry.get('api')!r} isn't an interface API the registry lists ({', '.join(map(str, sorted(API_VERSIONS)))})")
    if entry.get("access") not in ACCESS:
        p.append(f"access: one of {', '.join(sorted(ACCESS))}")
    if not isinstance(entry.get("commit"), str) or not COMMIT_RE.fullmatch(entry["commit"]):
        p.append("commit: the tag's full commit SHA")
    # The URLs follow from these; without them there's nothing to compare.
    can_url = all(isinstance(entry.get(k), str) for k in ("id", "version", "repository", "tag"))
    a = entry.get("asset")
    if not isinstance(a, dict) or set(a) != {"url", "sha256"}:
        p.append("asset: url and sha256")
    else:
        if can_url and a["url"] != asset_url(entry):
            p.append(f"asset.url: should be {asset_url(entry)}")
        if not isinstance(a["sha256"], str) or not SHA256_RE.fullmatch(a["sha256"]):
            p.append("asset.sha256: 64 lowercase hex digits")
    if "screenshots" in entry:
        p += validate_screenshots(entry, can_url)
    return p


def validate_screenshots(entry, can_url):
    """An entry's screenshots: each a picture of the entry's own release,
    named <id>-<version>-<file name>, with its checksum."""
    shots = entry["screenshots"]
    if not isinstance(shots, list) or not shots:
        return ["screenshots: a list of them (leave it out when there are none)"]
    p = []
    if len(shots) > MAX_SCREENSHOTS:
        p.append(f"screenshots: at most {MAX_SCREENSHOTS}")
    prefix = release_url(entry, folder_name(entry["id"], entry["version"]) + "-") if can_url else None
    seen = set()
    for n, s in enumerate(shots):
        where = f"screenshots[{n}]"
        if not isinstance(s, dict) or not {"url", "sha256"} <= set(s) or not set(s) <= set(SHOT_FIELDS):
            p.append(f"{where}: url, sha256 and (if it has one) caption")
            continue
        url = s["url"]
        if not isinstance(url, str):
            p.append(f"{where}.url: a URL")
        elif prefix is not None:
            rest = url[len(prefix):] if url.startswith(prefix) else ""
            if not rest or "/" in rest or not is_image(rest):
                p.append(f"{where}.url: should be {prefix}<file name>, a .png, .jpg or .webp of the release")
            elif rest in seen:
                p.append(f"{where}.url: {rest} twice; each screenshot needs a file name of its own")
            seen.add(rest)
        if not isinstance(s["sha256"], str) or not SHA256_RE.fullmatch(s["sha256"]):
            p.append(f"{where}.sha256: 64 lowercase hex digits")
        if "caption" in s and not (isinstance(s["caption"], str) and s["caption"].strip()):
            p.append(f"{where}.caption: text (leave it out when there's none)")
    return p


def expected_screenshots(m, entry):
    """What an entry's screenshots should be for interface.json `m`: [(url, caption)], in its order."""
    return [(release_url(entry, screenshot_name(entry["id"], entry["version"], s["path"])), s.get("caption") or "") for s in m.get("screenshots") or []]


def check_manifest(m, entry):
    """The archive's interface.json, against the entry."""
    if not isinstance(m, dict):
        return ["interface.json: not a JSON object"]
    p = []
    # What the recorder reads when a key is left out.
    defaults = {"description": "", "access": "listen", "license": "", "homepage": ""}
    for k in ("id", "name", "description", "version", "api", "access", "license", "homepage"):
        mine, theirs = m.get(k, defaults.get(k)), entry.get(k, defaults.get(k))
        if mine != theirs:
            p.append(f"interface.json says {k} {mine!r}, the entry {theirs!r}")
    if m.get("repository"):
        try:
            if parse_repository(m["repository"]) != parse_repository(entry["repository"]):
                p.append(f"interface.json says repository {m['repository']!r}, the entry {entry['repository']!r}")
        except (Problem, AttributeError):
            p.append(f"interface.json says repository {m['repository']!r}: not a GitHub repository")
    shots = m.get("screenshots") or []
    if not isinstance(shots, list) or not all(isinstance(s, dict) for s in shots):
        return p + ['interface.json: screenshots should be a list of { "path", "caption" }']
    if len(shots) > MAX_SCREENSHOTS:
        p.append(f"interface.json: at most {MAX_SCREENSHOTS} screenshots")
    names, bad = set(), False
    for s in shots:
        why = screenshot_path_problem(s.get("path"))
        if why:
            p.append(f"interface.json: screenshot {why}")
            bad = True
            continue
        if base_name(s["path"]) in names:
            p.append(f"interface.json: two screenshots named {base_name(s['path'])}; each is released under its file name, so they need different ones")
        names.add(base_name(s["path"]))
        if not isinstance(s.get("caption", ""), str):
            p.append(f"interface.json: screenshot {s['path']}: its caption should be text")
            bad = True
    if not bad:
        want = expected_screenshots(m, entry)
        have = [(s.get("url"), s.get("caption") or "") for s in entry.get("screenshots", []) if isinstance(s, dict)]
        if want != have:
            p.append(f"screenshots: interface.json has {describe_shots(want)}; the entry {describe_shots(have)}")
    return p


def describe_shots(shots):
    """[(url, caption)] → "a.png ('Caption'), b.png", for messages."""
    if not shots:
        return "none"
    return ", ".join(str(url).rsplit("/", 1)[-1] + (f" ({caption!r})" if caption else "") for url, caption in shots)


# ─── Archives ────────────────────────────────────────────────────────────

def safe_member(name):
    parts = Path(name).parts
    return not (name.startswith(("/", "\\")) or "\\" in name or ".." in parts or (parts and ":" in parts[0]))


def is_link(info):
    return info.create_system == 3 and stat.S_ISLNK(info.external_attr >> 16)


def open_archive(data, id, version):
    """A release's zip and its interface.json, after checking the zip is what
    the recorder installs: one folder, <id>-<version>/, with index.html and
    interface.json, plain files only, nothing that leaves the folder, and no
    more than MAX_UNPACKED in all. Raises Problem with everything that's wrong."""
    name = asset_name(id, version)
    if len(data) > MAX_ARCHIVE:
        raise Problem(f"{name}: over {MAX_ARCHIVE >> 20} MB, more than the recorder downloads")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        raise Problem(f"{name}: not a readable zip ({e})") from None
    top = folder_name(id, version)
    p, seen, total = [], set(), 0
    for i in infos:
        n = i.filename
        if not safe_member(n):
            p.append(f"unsafe path {n!r}")
            continue
        if is_link(i):
            p.append(f"{n}: a link, which the recorder refuses")
            continue
        parts = [x for x in n.split("/") if x]
        if not parts or parts[0] != top:
            p.append(f"{n}: outside {top}/, the one folder the archive should hold")
            continue
        if n.rstrip("/") in seen:
            p.append(f"{n}: in the archive twice")
        seen.add(n.rstrip("/"))
        if any(x.startswith(".") for x in parts):
            p.append(f"{n}: hidden files aren't served; leave them out")
        if len(parts) > 1 and parts[1] in RESERVED:
            p.append(f"{n}: /{parts[1]}/ is the recorder's, so this would never be served")
        total += i.file_size
    if total > MAX_UNPACKED:
        p.append(f"unpacks to more than {MAX_UNPACKED >> 20} MB")
    for f in ("index.html", "interface.json"):
        if f"{top}/{f}" not in seen:
            p.append(f"no {top}/{f}")
    m = None
    if f"{top}/interface.json" in seen and not p:
        try:
            m = json.loads(z.read(f"{top}/interface.json").decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            p.append(f"{top}/interface.json isn't readable JSON ({e})")
    if p:
        raise Problem(f"{name}:\n    " + "\n    ".join(p))
    return z, m


def read_archive(data, id, version):
    """The interface.json in a release's zip, once the zip checks out (open_archive)."""
    return open_archive(data, id, version)[1]


def check_archive_screenshots(z, m, entry):
    """Each screenshot interface.json names is in the zip, no bigger than the
    recorder shows, and the same picture as the release file the entry pins."""
    top = folder_name(entry["id"], entry["version"])
    pinned = {s.get("url"): s.get("sha256") for s in entry.get("screenshots", []) if isinstance(s, dict)}
    p = []
    for s in m.get("screenshots") or []:
        path = f"{top}/{s['path']}"
        try:
            info = z.getinfo(path)
        except KeyError:
            p.append(f"screenshot {s['path']}: not in the archive")
            continue
        if info.file_size > MAX_SCREENSHOT:
            p.append(f"screenshot {s['path']}: over {MAX_SCREENSHOT >> 20} MB")
            continue
        name = screenshot_name(entry["id"], entry["version"], s["path"])
        if sha256(z.read(path)) != pinned.get(release_url(entry, name)):
            p.append(f"screenshot {s['path']}: not the same picture as {name} (their sha256 differ)")
    return p


def check_archive(data, entry):
    """Problems with an entry's archive: its layout, its interface.json
    against the entry, and its screenshots against the ones the entry pins."""
    try:
        z, m = open_archive(data, entry["id"], entry["version"])
    except Problem as e:
        return [str(e)]
    return check_manifest(m, entry) or check_archive_screenshots(z, m, entry)


def attestation(path, entry):
    """Check the file's build provenance: built by GitHub Actions in the
    entry's repository, from its tag, at its commit."""
    if not shutil.which("gh"):
        raise Problem("gh (the GitHub CLI) is needed to check build provenance")
    owner, repo = parse_repository(entry["repository"])
    r = subprocess.run(["gh", "attestation", "verify", str(path), "--repo", f"{owner}/{repo}", "--format", "json"], capture_output=True, text=True)
    if r.returncode != 0:
        raise Problem(f"{path.name}: no valid build provenance from {owner}/{repo}: {r.stderr.strip()[:300]}")
    certs = [a["verificationResult"]["signature"]["certificate"] for a in json.loads(r.stdout)]
    want_ref = f"refs/tags/{entry['tag']}"
    if not any(c.get("sourceRepositoryRef") == want_ref and c.get("sourceRepositoryDigest") == entry["commit"] for c in certs):
        got = ", ".join(f"{c.get('sourceRepositoryRef')} at {str(c.get('sourceRepositoryDigest'))[:7]}" for c in certs)
        raise Problem(f"{path.name}: built from {got}, not {want_ref} at {entry['commit'][:7]}")


# ─── Commands ────────────────────────────────────────────────────────────

def make_entry(m, repository, tag, commit, tier, asset, files):
    """The entry for a release: `m` its interface.json, `asset` its zip's
    {url, sha256}, `files` {name: (url, sha256)} for every file that's both
    in the release and in its SHA256SUMS."""
    entry = {
        "id": m.get("id", ""),
        "name": m.get("name", ""),
        "description": m.get("description", ""),
        "repository": repository,
        "license": m.get("license", ""),
        "tier": tier,
        "version": m.get("version", ""),
        "api": m.get("api"),
        "access": m.get("access", "listen"),
        "tag": tag,
        "commit": commit,
        "asset": asset,
    }
    if m.get("homepage"):
        entry["homepage"] = m["homepage"]
    shots = m.get("screenshots") or []
    if not isinstance(shots, list) or not all(isinstance(s, dict) and isinstance(s.get("path"), str) for s in shots):
        raise Problem('interface.json: screenshots should be a list of { "path", "caption" }')
    entry["screenshots"] = []
    for s in shots:
        name = screenshot_name(entry["id"], entry["version"], s["path"])
        if name not in files:
            raise Problem(f"{tag} has no {name} (the screenshot {s['path']}) in the release and its SHA256SUMS: release it with the interface template's release workflow")
        url, sum = files[name]
        shot = {"url": url, "sha256": sum}
        if s.get("caption"):
            shot["caption"] = s["caption"]
        entry["screenshots"].append(shot)
    if not entry["screenshots"]:
        del entry["screenshots"]
    return entry


def add_release(repository, tag, tier=None):
    owner, repo = parse_repository(repository)
    repository = f"https://github.com/{owner}/{repo}"
    rel = github(f"/repos/{owner}/{repo}/releases/tags/{tag}")
    if rel.get("prerelease"):
        raise Problem(f"{tag} is a prerelease")
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    if "SHA256SUMS" not in assets:
        raise Problem(f"{tag} has no SHA256SUMS: release it with the interface template's release workflow")
    sums = parse_sums(download(assets["SHA256SUMS"], 1 << 20).decode())
    # As the recorder does: the one zip in SHA256SUMS.
    zips = [n for n in sums if n.endswith(".zip")]
    if len(zips) != 1:
        raise Problem(f"{tag} should have one <id>-<version>.zip in its SHA256SUMS, it has {len(zips)}")
    name = zips[0]
    if name not in assets:
        raise Problem(f"{tag}: {name} is in SHA256SUMS but not in the release")
    data = download(assets[name])
    if sha256(data) != sums[name]:
        raise Problem(f"{name} doesn't match SHA256SUMS")
    # Its interface.json says what it is; the zip must be named for it.
    top = name[:-len(".zip")]
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            m = json.loads(z.read(f"{top}/interface.json"))
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        raise Problem(f"{name}: no readable {top}/interface.json ({e})") from None
    if not isinstance(m, dict):
        raise Problem(f"{name}: interface.json isn't a JSON object")
    id, version = m.get("id", ""), m.get("version", "")
    if not isinstance(id, str) or not ID_RE.fullmatch(id):
        raise Problem(f"interface.json: {id!r} can't be an id (lowercase letters, digits and -)")
    if name != asset_name(id, version):
        raise Problem(f"the archive is {name}, but its interface.json says {id} {version}: it should be {asset_name(id, version)}")
    if tag != f"v{version}":
        raise Problem(f"the tag is {tag}, but interface.json's version is {version}")
    path = INTERFACES / f"{id}.json"
    old = load(path) if path.exists() else {}
    if old and parse_repository(old["repository"]) != (owner, repo):
        raise Problem(f"interfaces/{id}.json is {old['repository']}'s: the id {id!r} is taken")
    files = {n: (assets[n], sums[n]) for n in sums if n in assets}
    tier = tier or old.get("tier") or "community"
    entry = make_entry(m, repository, tag, tag_commit(owner, repo, tag), tier, {"url": assets[name], "sha256": sums[name]}, files)
    problems = validate(entry, id) + check_archive(data, entry)
    if problems:
        raise Problem(f"{repository} {tag}:\n  " + "\n  ".join(problems))
    INTERFACES.mkdir(exist_ok=True)
    path.write_text(dump(entry))
    build_index()
    was = f" (was {old['version']})" if old else ""
    shots = len(entry.get("screenshots", []))
    print(f"interfaces/{id}.json: {entry['name']} {version}{was}, {entry['access']} access, {shots} screenshot{'' if shots == 1 else 's'}, {entry['tier']}")
    print(f"Check it before you open the pull request: ./check interfaces/{id}.json")


def build_index(write=True):
    """index.json: every entry, by id. What the recorder fetches."""
    text = json.dumps({"format": 1, "interfaces": [json.loads(dump(e)) for e in sorted(entries(), key=lambda e: e["id"])]}, indent=2, ensure_ascii=False) + "\n"
    if write:
        INDEX.write_text(text)
    return text


def check_entry(path, quick):
    """Problems with one entry. Without `quick`: its tag, the checksums of its
    archive and screenshots, the archive's provenance, and what's in it."""
    entry = load(path)
    problems = validate(entry, Path(path).stem)
    if problems or quick:
        return problems
    owner, repo = parse_repository(entry["repository"])
    try:
        now = tag_commit(owner, repo, entry["tag"])
        if now != entry["commit"]:
            problems.append(f"tag {entry['tag']} now points at {now[:7]}, not {entry['commit'][:7]}: it was moved")
    except Problem as e:
        problems.append(str(e))
    # The screenshots the store shows: each the picture the entry pins. (That
    # each is the same as the one in the archive is check_archive's.)
    for s in entry.get("screenshots", []):
        try:
            if sha256(download(s["url"], MAX_SCREENSHOT)) != s["sha256"]:
                problems.append(f"{s['url'].rsplit('/', 1)[-1]}: the download doesn't match its sha256")
        except Problem as e:
            problems.append(str(e))
    a = entry["asset"]
    with tempfile.TemporaryDirectory() as tmp:
        try:
            data = download(a["url"])
            if sha256(data) != a["sha256"]:
                return problems + [f"{asset_name(entry['id'], entry['version'])}: the download doesn't match its sha256"]
            f = Path(tmp) / asset_name(entry["id"], entry["version"])
            f.write_bytes(data)
            attestation(f, entry)
            problems += check_archive(data, entry)
        except Problem as e:
            problems.append(str(e))
    return problems


def changed_since(ref):
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=AM", f"{ref}...HEAD", "--", "interfaces/"], capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        raise Problem(f"git diff against {ref}: {r.stderr.strip()}")
    return [ROOT / n for n in r.stdout.split() if n.endswith(".json")]


def check(paths, quick):
    failed = False
    for p in INTERFACES.iterdir():
        if p.name == ".gitkeep":
            continue
        if p.suffix != ".json" or p.name.startswith("."):
            print(f"interfaces/{p.name}: only <id>.json files belong here")
            failed = True
    if INDEX.exists() and INDEX.read_text() != build_index(write=False):
        print("index.json is out of date: run tools/registry.py build-index")
        failed = True
    for path in paths:
        name = Path(path).relative_to(ROOT) if Path(path).is_absolute() and ROOT in Path(path).parents else path
        try:
            problems = check_entry(path, quick)
        except Problem as e:
            problems = [str(e)]
        print(f"{name}: " + ("ok" if not problems else "\n  " + "\n  ".join(problems)))
        failed |= bool(problems)
    if not paths and not failed:
        print("no entries to check")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(prog="registry.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-release", help="write interfaces/<id>.json for a release")
    a.add_argument("repository")
    a.add_argument("tag")
    a.add_argument("--tier", choices=sorted(TIERS), help="for a new entry: community unless given; an existing one keeps its tier")
    c = sub.add_parser("check", help="check entries")
    c.add_argument("paths", nargs="*")
    c.add_argument("--quick", action="store_true", help="only what can be checked without the network")
    c.add_argument("--changed-since", metavar="REF", help="only entries added or changed since this git ref")
    sub.add_parser("build-index", help="rebuild index.json")
    args = ap.parse_args()
    try:
        if args.cmd == "add-release":
            add_release(args.repository, args.tag, args.tier)
        elif args.cmd == "build-index":
            build_index()
            print("index.json rebuilt")
        else:
            if args.paths:
                paths = [Path(p).resolve() for p in args.paths]
            elif args.changed_since:
                paths = changed_since(args.changed_since)
            else:
                paths = sorted(INTERFACES.glob("*.json"))
            sys.exit(check(paths, args.quick))
    except Problem as e:
        print(e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
