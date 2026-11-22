#!/usr/bin/env bash
# Build "QDVC Spork.app" from the Swift package.
#
#   scripts/build-app.sh               build into build/QDVC Spork.app
#   scripts/build-app.sh --install     also copy it to ~/Applications and install the spork command
#   scripts/build-app.sh --universal   build for Apple silicon and Intel
#   scripts/build-app.sh --no-tests    skip the SporkCore unit tests
#
# Needs full Xcode (not just the Command Line Tools): from Xcode 27, SwiftUI's
# @State is a macro whose plugin ships only with Xcode. Xcode 26 or later is
# needed for the Liquid Glass look. This block is shared with the other QDVC
# macOS apps; see docs/MAINTENANCE.md.
set -euo pipefail

cd "$(dirname "$0")/.."
PRODUCT=QDVCSpork
APP_NAME="QDVC Spork"
APP="build/$APP_NAME.app"

install=0 tests=1
arch_args=()
for arg in "$@"; do
    case "$arg" in
        --install) install=1 ;;
        --universal) arch_args=(--arch arm64 --arch x86_64) ;;
        --no-tests) tests=0 ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
    esac
done

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

# --- Toolchain: prefer the newest full Xcode ------------------------------------
newest_xcode() {
    local best="" best_major=0 app ver major
    while IFS= read -r app; do
        [ -d "$app" ] || continue
        ver=$(defaults read "$app/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo 0)
        major=${ver%%.*}
        if [ "${major:-0}" -gt "$best_major" ] 2>/dev/null; then
            best_major=$major
            best=$app
        fi
    done < <( { mdfind "kMDItemCFBundleIdentifier == 'com.apple.dt.Xcode'" 2>/dev/null;
                ls -d /Applications/Xcode*.app 2>/dev/null; } | sort -u )
    [ -n "$best" ] && echo "$best"
}

if [ -z "${DEVELOPER_DIR:-}" ]; then
    current=$(xcode-select -p 2>/dev/null || true)
    if [[ "$current" == *CommandLineTools* || -z "$current" ]]; then
        if xcode=$(newest_xcode); then
            export DEVELOPER_DIR="$xcode/Contents/Developer"
            echo "Using $xcode (xcode-select points at the Command Line Tools)."
        else
            echo "warning: no Xcode found; the Command Line Tools may fail with" >&2
            echo "         \"plugin for module 'SwiftUIMacros' not found\". Install Xcode." >&2
        fi
    fi
fi

say "Toolchain"
xcrun swift --version 2>&1 | head -1
SDK_VERSION=$(xcrun --sdk macosx --show-sdk-version)
echo "macOS SDK $SDK_VERSION · developer dir $(xcode-select -p 2>/dev/null) ${DEVELOPER_DIR:+(DEVELOPER_DIR=$DEVELOPER_DIR)}"
if [ "${SDK_VERSION%%.*}" -lt 26 ]; then
    echo "warning: SDK $SDK_VERSION is older than 26, so the app gets the classic look" >&2
    echo "         instead of Liquid Glass. Install Xcode 26 or later." >&2
fi

# --- Tests ---------------------------------------------------------------------
if [ "$tests" = 1 ]; then
    say "Testing SporkCore"
    xcrun swift test --filter SporkCoreTests 2>&1 | tail -20
fi

# --- Build ---------------------------------------------------------------------
say "Building $PRODUCT (release)"
xcrun swift build -c release --product "$PRODUCT" ${arch_args[@]+"${arch_args[@]}"}
BIN_DIR=$(xcrun swift build -c release --product "$PRODUCT" ${arch_args[@]+"${arch_args[@]}"} --show-bin-path)

say "Assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_DIR/$PRODUCT" "$APP/Contents/MacOS/$PRODUCT"
cp Resources/Info.plist "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"
cp Resources/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"

# --- Record the real SDK so macOS applies Liquid Glass ---------------------------
# `swift build` writes the deployment target into the binary's SDK field, and
# AppKit only gives the Liquid Glass look to binaries linked against SDK 26+.
EXE="$APP/Contents/MacOS/$PRODUCT"
build_info=$(xcrun vtool -show-build "$EXE" 2>/dev/null || true)
minos=$(echo "$build_info" | awk '/minos/ {print $2; exit}')
linked_sdk=$(echo "$build_info" | awk '/ sdk/ {print $2; exit}')
if [ -n "$minos" ] && [ "$linked_sdk" != "$SDK_VERSION" ]; then
    tmp=$(mktemp)
    xcrun vtool -set-build-version macos "$minos" "$SDK_VERSION" -replace -output "$tmp" "$EXE"
    mv "$tmp" "$EXE"
    chmod 755 "$EXE"
    echo "Recorded SDK $SDK_VERSION in the binary (was ${linked_sdk:-unknown}, minos $minos)."
fi
xcrun vtool -show-build "$EXE" 2>/dev/null | grep -E 'minos|sdk' || true

# --- Sign (ad hoc; no Apple Developer account needed) ----------------------------
codesign --force --sign - --timestamp=none "$APP"
codesign --verify --verbose=1 "$APP"

if [ "$install" = 1 ]; then
    say "Installing to ~/Applications"
    if pgrep -x "$PRODUCT" >/dev/null; then
        osascript -e "tell application id \"org.qdvc.spork.mac\" to quit" >/dev/null 2>&1 || true
        sleep 1
    fi
    mkdir -p "$HOME/Applications"
    rm -rf "$HOME/Applications/$APP_NAME.app"
    cp -R "$APP" "$HOME/Applications/"
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
        -f "$HOME/Applications/$APP_NAME.app" >/dev/null 2>&1 || true

    if [ -w /usr/local/bin ]; then cli_dir=/usr/local/bin; else cli_dir="$HOME/.local/bin"; fi
    mkdir -p "$cli_dir"
    cp scripts/spork "$cli_dir/spork"
    chmod 755 "$cli_dir/spork"
    echo "Installed the spork command to $cli_dir/spork"
    case ":$PATH:" in
        *":$cli_dir:"*) ;;
        *) echo "Add it to your PATH:  echo 'export PATH=\"$cli_dir:\$PATH\"' >> ~/.zshrc" ;;
    esac
fi

echo "Done: $APP"
