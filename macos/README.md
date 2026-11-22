# QDVC Spork for macOS

The SwiftUI edition of [QDVC Spork](../README.md). macOS 14 (Sonoma) or later.

## Build and install

```
macos/scripts/build-app.sh --install
```

This tests SporkCore, builds a release binary, assembles `build/QDVC Spork.app`, records
the real SDK version in the binary (needed for the Liquid Glass look), signs it ad hoc,
copies it to `~/Applications`, and installs the `spork` command to `/usr/local/bin` if
that's writable, otherwise `~/.local/bin`. Options: `--universal`, `--no-tests`, `--help`.

Requirements:

- **Full Xcode**, not just the Command Line Tools. From Xcode 27, a Command Line Tools
  toolchain fails with "plugin for module 'SwiftUIMacros' not found". The script uses the
  newest installed Xcode even when `xcode-select` points at the Command Line Tools.
- **Xcode 26 or later** for the Liquid Glass look. With an older SDK the app builds and
  works, with the classic look.
- No paid Apple Developer account. `git` itself: Xcode's, Homebrew's or any in
  `/opt/homebrew/bin`, `/usr/local/bin` or `/usr/bin`.

For `swift run` and `swift test`, point SwiftPM at Xcode first:

```
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
swift test                      # SporkCore tests (also run on Linux)
swift run QDVCSpork ~/src/repo  # classic look, no icon: use build-app.sh for the real thing
```

## Using it

- `spork .` in Terminal, File › Add Repository… (⌘O), or drop folders on the sidebar or
  the Dock icon.
- Settings (⌘,): **General** (editor and terminal apps, network timeout), **Servers**
  (trusted `cert.pem` files, see [CERTIFICATES.md](../docs/CERTIFICATES.md)),
  **Command Line** (install `spork`).
- When git or ssh needs a password or passphrase, a dialog appears. Saved HTTPS passwords
  come from git's own credential helper (Apple's git uses the keychain).

## Layout

```
Package.swift
Sources/SporkCore/   git runner, parsers, graph, census, certificates, settings (Foundation only)
Sources/QDVCSpork/   SwiftUI app: AppModel, RepoModel, Views/, menu commands
Tests/SporkCoreTests/
Resources/           Info.plist, AppIcon.icns, AppIcon.svg (from ../tools/make_icons.py)
scripts/             build-app.sh, spork
docs/MAINTENANCE.md
```
