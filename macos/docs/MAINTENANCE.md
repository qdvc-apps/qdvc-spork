# Maintaining the macOS edition

## Toolchain

`scripts/build-app.sh` carries the block shared with the other QDVC macOS apps
(qdvc-progress-mac, qdvc-response-letter-mac):

- `newest_xcode` finds installed Xcodes with Spotlight (`com.apple.dt.Xcode`) and
  `/Applications/Xcode*.app`, and picks the highest major version.
- If `DEVELOPER_DIR` is unset and `xcode-select -p` points at the Command Line Tools, the
  script exports `DEVELOPER_DIR` for that Xcode. Every SwiftPM call goes through
  `xcrun swift …`.
- It prints the Swift version, the SDK version and the developer folder, and warns when
  the SDK is older than 26.
- Before signing it reads `minos` and `sdk` with `vtool -show-build` and, if the recorded
  SDK isn't the one actually used, rewrites it with
  `vtool -set-build-version macos <minos> <sdk> -replace`. `swift build` records the
  deployment target there, and macOS decides on Liquid Glass from that field.

Keep this block in step with the sibling apps when either changes.

## Code structure

- **SporkCore** has no AppKit or SwiftUI. It builds and tests on Linux (`swift test`), so
  logic belongs there. It mirrors the GTK edition's `sporkapp/core` module by module; keep
  behaviour (census reasons, graph layout, search semantics) identical, and update
  `docs/DESIGN.md` when it changes.
- Public structs have explicit `public init`s (memberwise initialisers are internal).
- Text from git is split on Unicode scalars (`Parsers.split`), never on `Character`:
  `"\r\n"` is one Character in Swift.
- The settings type is `SporkSettings`, not `Settings`, which is a SwiftUI scene.
- Models are passed to views explicitly (no `@Environment` model lookups), and list
  selections use `Binding(guarded:set:)` so unchanged values aren't written back.
- `ResizableHSplit` replaces `HSplitView` (which ignores the safe area).
- Module-level helpers must not share a name with any `View` member: inside a view,
  `background { … }` resolves to the `.background` modifier. The off-main-thread helper is
  `offMain`.
- Labeled tuples lose their labels through `x ?? (a, b)`; use named types (`FetchContext`).
- The app uses a single `Window` scene, not `WindowGroup`.

## Things not yet verified on a Mac

This edition was written without a Mac. Expect a first round of compiler fixes; please
paste the complete error list so they can all be fixed in one pass.
