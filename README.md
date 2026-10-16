# Trunk Recorder Pro interfaces

The interfaces that [Trunk Recorder Pro](https://github.com/TrunkRecorder/trunk-recorder-pro)'s
interface store offers. An interface is a web page (HTML, JavaScript and CSS, with no server
side) that the recorder serves on a port of its own: a player, a scanner wall, a phone page.
Each one has an entry here that pins one release of it: its version, the commit it was built
from, and the SHA-256 of its archive. The recorder installs only an archive that matches, so
what users get is what was reviewed.

| Interface | | |
|---|---|---|
| *None listed yet* | | |

To write an interface, start from the
[interface template](https://github.com/TrunkRecorder/trunk-interface-template), and read
[Publishing an interface](https://github.com/TrunkRecorder/trunk-recorder-pro/blob/main/docs/interfaces/publishing.md).

## Listing your interface

1. Release it with the template's release workflow. That workflow names the archive and the
   screenshots, writes `SHA256SUMS` and attests the files' build provenance, and the registry
   needs all three.
2. Fork this repository and add your entry:

   ```sh
   ./add-release https://github.com/you/trunkamp v1.0.0
   ./check interfaces/trunkamp.json
   ```

   `add-release` writes `interfaces/<id>.json` from the release and rebuilds `index.json`.
   `check` downloads the archive and the screenshots and checks them (below). It needs Python
   3.9 or later and the [GitHub CLI](https://cli.github.com) (`gh`).
3. Open a pull request with both files. A maintainer reviews it.

**Releasing an update** is the same: release the new version, run `add-release` with its tag,
and open a pull request. Your entry keeps its tier.

## An entry

```json
{
  "id": "trunkamp",
  "name": "Trunkamp",
  "description": "A Winamp-style player: live audio and recorded calls, with an equalizer.",
  "repository": "https://github.com/someone/trunkamp",
  "homepage": "https://example.com/trunkamp",
  "license": "MIT",
  "tier": "community",
  "version": "1.0.0",
  "api": 1,
  "access": "listen",
  "tag": "v1.0.0",
  "commit": "…40 hex…",
  "asset": {
    "url": "https://github.com/someone/trunkamp/releases/download/v1.0.0/trunkamp-1.0.0.zip",
    "sha256": "…64 hex…"
  },
  "screenshots": [
    {
      "url": "https://github.com/someone/trunkamp/releases/download/v1.0.0/trunkamp-1.0.0-player.png",
      "sha256": "…64 hex…",
      "caption": "The player, with the playlist"
    },
    {
      "url": "https://github.com/someone/trunkamp/releases/download/v1.0.0/trunkamp-1.0.0-phone.png",
      "sha256": "…64 hex…",
      "caption": "At phone width"
    }
  ]
}
```

- `name`, `description`, `homepage`, `license`, `api` and `access` are what the interface's
  `interface.json` says. `homepage` is optional; `description` and `license` aren't, here.
- `access` is what it asks for: `listen` (it can see and play everything, and change nothing)
  or `control` (everything the recorder's own interface can do). The store warns before
  installing one that asks for control.
- `tier` is `official` for interfaces maintained with the recorder, in the TrunkRecorder
  organization, and `community` for everyone else's. The store shows which is which.
- `tag` is `v<version>`, and `commit` is the commit it pointed at when the entry was added. Tags
  can be moved; the commit says which source was reviewed.
- `asset` is the release's one archive, `<id>-<version>.zip`: a single folder,
  `<id>-<version>/`, with `interface.json`, `index.html` and the rest.
- `screenshots` are the pictures `interface.json` lists, in its order and with its captions, each
  as the release has it on its own: `<id>-<version>-<file name>`. Each is the same file as the
  one in the archive. The recorder fetches them to show the interface before it's installed. An
  interface with no screenshots has no `screenshots` (and no `caption` where one has none).
- There's no author: the store shows the GitHub account the repository is in.

`index.json` holds every entry in one file, which is what the recorder fetches. Don't edit it by
hand: `add-release` rebuilds it, and so does `tools/registry.py build-index`.

## What the checks do

On every pull request, for each entry it adds or changes, CI:

- checks the entry's fields, and that every URL is a download from the entry's own repository,
  at its tag: the archive named `<id>-<version>.zip`, and each screenshot
  `<id>-<version>-<file name>`, a `.png`, `.jpg` or `.webp` (at most 6, each named differently)
- checks that the tag still points at the entry's commit
- downloads the archive and each screenshot, and checks their SHA-256
- checks the archive's build provenance with `gh attestation verify`: GitHub Actions built it in the
  entry's repository, from the tag, at the commit
- reads the archive: one folder, `<id>-<version>/`, with `index.html` and `interface.json`;
  plain files only (no links), no paths that leave the folder, no hidden files, nothing under
  `api/` or `calls/` (those paths are the recorder's), and no more than 100 MB
- checks that its `interface.json` agrees with the entry on id, name, description, version,
  `api`, `access`, license, homepage, (if it says one) repository, and screenshots: the same
  ones, in the same order, with the same captions
- checks that each screenshot `interface.json` lists is in the archive, is no more than 2 MB, and
  is the same file as the one released on its own (their SHA-256 agree)

A weekly run checks every entry again, which catches a release deleted or a tag moved after its
entry was merged.

Nothing from a pull request is run: the archive is only read.

## Review

The checks prove where the files came from. A maintainer reviews the rest:

- **The source is public**, and its license allows it to be listed.
- **It does what it says**, and sends nothing anywhere the user didn't ask for. In particular, no
  analytics and no calls to outside services the description doesn't mention.
- **It loads nothing from elsewhere** at run time: no CDN scripts, fonts or styles.
- **It asks for the access it needs**: listen, unless it really has to change the recorder.
- **It works at phone width** and in the browsers people use.
- **Its screenshots show it**, as it is in this version, with nothing private in them (no real
  unit names or talkgroups you shouldn't share).
- **The id is fitting**, and doesn't impersonate another interface or service.

For an update, review looks at what changed since the commit in the current entry:
`https://github.com/<owner>/<repo>/compare/<old commit>...<new commit>`.

## License

MIT.
