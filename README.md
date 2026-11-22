# QDVC Spork

A native Git client in two editions that share one design: **SwiftUI for macOS** and
**GTK 4 / libadwaita for GNOME** (packaged as a local Flatpak). Part of the
[QDVC suite](https://github.com/qdvc-apps).

Spork is built around a handful of repositories you care about, often with several
remotes each, some of them on a local server with a self-signed certificate.

| Edition | Folder | Build and install |
|---|---|---|
| macOS 14+ (Swift, SwiftUI, AppKit) | `macos/` | `macos/scripts/build-app.sh --install` |
| GNOME and other Linux desktops (Python, GTK 4, libadwaita, Flatpak `qdvc.spork.gtk4app`) | `gtk4/` | `gtk4/scripts/build-app.sh --install` |

The editions share no code. They share this README, the design in
[`docs/DESIGN.md`](docs/DESIGN.md), the settings format in
[`docs/SETTINGS_FORMAT.md`](docs/SETTINGS_FORMAT.md), the certificate approach in
[`docs/CERTIFICATES.md`](docs/CERTIFICATES.md), and the icon generator in
`tools/make_icons.py`.

## What it does

- **`spork .`** in a terminal opens (and bookmarks) the repository you're in.
- **Three panes**: bookmarked repositories · commits (with an *Uncommitted Changes* row on
  top and a branch graph) · a tab container:
  - **Commit**: the commit's message, author, dates, parents and stats. For
    *Uncommitted Changes* it becomes the commit composer (summary, description, amend).
  - **Changes**: changed-file list and diff. For uncommitted changes, tick files to stage
    them, or stage, unstage and discard single hunks.
  - **Browse**: the repository's files as of that commit, with **Blame** and **History**.
- **Multiple remotes**: a strip above the commit list shows ↑ahead / ↓behind for every
  remote; fetch, pull or push per remote, or **Push to All Remotes**. The sidebar shows a
  badge per repository.
- **Dashboard**: a census of all bookmarks in the spirit of
  [qdvc-repo-census](https://github.com/qdvc-apps/qdvc-repo-census): what needs attention,
  which servers are offline, one reachability check per server before fetching.
- **Self-signed HTTPS**: add a local server's `cert.pem` (for example from a
  [Forgejo in Podman](https://github.com/blairwang-online/linux-install-notes/blob/main/tasks/forgejo-podman.md)
  setup) once per server, and Spork's fetch, pull and push just work. Spork never touches
  `~/.gitconfig` or the system trust store.
- Branch switcher (switch, create, delete), commit search (message, author, hash, path),
  a native password/passphrase dialog for git and ssh, and Open in Finder/Files,
  Terminal or your editor.

Spork runs the real `git` command for everything, so your configuration, credential
helpers, SSH keys and hooks apply as usual.

## Repository layout

```
docs/                     shared design, settings format, certificate notes
tools/make_icons.py       generates both editions' icons
macos/                    SwiftPM package: SporkCore (testable on Linux) + QDVCSpork app
gtk4/                     Python package, Flatpak manifest, tests
cursed-knowledge-reports/ platform gotchas found while building Spork
ai-assistance-logs/       records of AI-assisted development sessions
```
