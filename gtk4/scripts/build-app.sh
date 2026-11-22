#!/usr/bin/env bash
# Build QDVC Spork for GNOME as a local Flatpak (qdvc.spork.gtk4app).
#
#   scripts/build-app.sh --install    check, test, build the Flatpak and install it for your user
#   scripts/build-app.sh --run        build and start it without installing
#   scripts/build-app.sh --bundle     also write build/qdvc-spork.flatpak
#   scripts/build-app.sh --uninstall  remove it (your settings and repositories are untouched)
#   scripts/build-app.sh --no-tests   skip the unit tests
#
# --install also writes ~/.local/bin/spork, so `spork .` in a terminal opens
# the repository you're in. Run from anywhere; paths are relative to gtk4/.
set -euo pipefail

cd "$(dirname "$0")/.."
APP_ID=qdvc.spork.gtk4app
MANIFEST=flatpak/$APP_ID.yml
BUILD_DIR=build/flatpak-app
STATE_DIR=build/.flatpak-builder
RUNTIME_VERSION=50

install=0 run=0 bundle=0 uninstall=0 tests=1
for arg in "$@"; do
    case "$arg" in
        --install) install=1 ;;
        --run) run=1 ;;
        --bundle) bundle=1 ;;
        --uninstall) uninstall=1 ;;
        --no-tests) tests=0 ;;
        -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
    esac
done

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

spork_wrapper() {
    cat <<EOF
#!/bin/sh
# spork — open a Git repository in QDVC Spork.  Usage: spork [path]
# Installed by QDVC Spork (gtk4/scripts/build-app.sh).
target="\${1:-.}"
if [ ! -e "\$target" ]; then
    echo "spork: \$target: no such file or directory" >&2
    exit 2
fi
dir=\$(cd "\$target" 2>/dev/null && pwd -P) || dir=\$(cd "\$(dirname "\$target")" && pwd -P)
top=\$dir
if command -v git >/dev/null 2>&1; then
    top=\$(git -C "\$dir" rev-parse --show-toplevel 2>/dev/null) || {
        echo "spork: \$dir is not inside a Git repository" >&2
        exit 1
    }
fi
exec flatpak run $APP_ID "\$top"
EOF
}

if [ "$uninstall" = 1 ]; then
    say "Uninstalling $APP_ID"
    flatpak uninstall --user -y "$APP_ID" || true
    if [ -f "$HOME/.local/bin/spork" ] && grep -q "QDVC Spork" "$HOME/.local/bin/spork"; then
        rm -f "$HOME/.local/bin/spork"
        echo "Removed ~/.local/bin/spork"
    fi
    echo "Settings are kept in ~/.var/app/$APP_ID/ (delete that folder to forget them)."
    exit 0
fi

for tool in flatpak flatpak-builder; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Missing $tool. Install it first, e.g.:" >&2
        echo "  sudo apt install flatpak flatpak-builder    # Debian/Ubuntu" >&2
        echo "  sudo dnf install flatpak flatpak-builder    # Fedora/AlmaLinux" >&2
        exit 1
    fi
done

if ! flatpak info --user org.gnome.Sdk//$RUNTIME_VERSION >/dev/null 2>&1 &&
   ! flatpak info --system org.gnome.Sdk//$RUNTIME_VERSION >/dev/null 2>&1; then
    say "Installing the GNOME $RUNTIME_VERSION runtime and SDK from Flathub (one time)"
    flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
    flatpak install --user -y flathub org.gnome.Platform//$RUNTIME_VERSION org.gnome.Sdk//$RUNTIME_VERSION
fi

if [ "$tests" = 1 ]; then
    if command -v python3 >/dev/null && command -v git >/dev/null; then
        say "Running unit tests"
        python3 -m unittest discover -s tests
    else
        echo "Skipping tests (needs python3 and git on the host)."
    fi
fi

if command -v appstreamcli >/dev/null 2>&1; then
    appstreamcli validate --no-net --explain data/$APP_ID.metainfo.xml || true
fi
if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate data/$APP_ID.desktop || true
fi

mkdir -p build
builder_args=(--user --force-clean --state-dir="$STATE_DIR" --install-deps-from=flathub)
was_running=0
if [ "$install" = 1 ]; then
    if flatpak ps --columns=application 2>/dev/null | grep -qx "$APP_ID"; then
        was_running=1
        say "Asking the running copy to quit"
        gdbus call --session --dest "$APP_ID" --object-path /qdvc/spork/gtk4app \
            --method org.gtk.Actions.Activate quit '[]' '{}' >/dev/null 2>&1 || \
            flatpak kill "$APP_ID" 2>/dev/null || true
        sleep 1
    fi
    builder_args+=(--install)
fi
if [ "$bundle" = 1 ]; then
    builder_args+=(--repo=build/repo)
fi

say "Building the Flatpak (the first build compiles git and ssh, which takes a few minutes)"
flatpak-builder "${builder_args[@]}" "$BUILD_DIR" "$MANIFEST"

if [ "$bundle" = 1 ]; then
    say "Writing build/qdvc-spork.flatpak"
    flatpak build-bundle build/repo build/qdvc-spork.flatpak "$APP_ID"
fi

if [ "$install" = 1 ]; then
    mkdir -p "$HOME/.local/bin"
    spork_wrapper > "$HOME/.local/bin/spork"
    chmod 755 "$HOME/.local/bin/spork"
    say "Installed. Start it from your app grid, or run:  spork ."
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) echo "Note: ~/.local/bin isn't on your PATH; add it to use the spork command." ;;
    esac
    if [ "$was_running" = 1 ]; then
        (flatpak run "$APP_ID" >/dev/null 2>&1 &)
    fi
fi

if [ "$run" = 1 ]; then
    say "Starting from the build folder"
    flatpak-builder --run "$BUILD_DIR" "$MANIFEST" qdvc-spork
fi

if [ "$install" = 0 ] && [ "$run" = 0 ] && [ "$bundle" = 0 ]; then
    echo "Built $BUILD_DIR. Use --install, --run or --bundle next time."
fi
