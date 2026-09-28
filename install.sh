#!/bin/sh
# Install Umbrella for the current user. Needs only python3 (3.8+). No pip, no sudo.
#
#   ./install.sh              install to ~/.local/share/umbrella, command in ~/.local/bin
#   ./install.sh --uninstall  remove it again (your profiles in ~/.umbrella are kept)
#
# Override locations with UMBRELLA_LIB_DIR and UMBRELLA_BIN_DIR.
set -eu

LIB_DIR="${UMBRELLA_LIB_DIR:-$HOME/.local/share/umbrella}"
BIN_DIR="${UMBRELLA_BIN_DIR:-$HOME/.local/bin}"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)/src/umbrella"

say() { printf '%s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  rm -rf "$LIB_DIR" "$BIN_DIR/umbrella"
  say "Removed Umbrella. Your profiles and backups in ~/.umbrella were left in place."
  exit 0
fi

PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || die "python3 wasn't found. Install Python 3.8 or newer and try again."
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
  || die "Umbrella needs Python 3.8 or newer (found $("$PYTHON" --version 2>&1))."
[ -d "$SRC_DIR" ] || die "Run this from the Umbrella project folder (couldn't find src/umbrella)."

mkdir -p "$LIB_DIR" "$BIN_DIR"
rm -rf "$LIB_DIR/umbrella"
cp -R "$SRC_DIR" "$LIB_DIR/umbrella"
find "$LIB_DIR" -name __pycache__ -type d -prune -exec rm -rf {} +

cat > "$BIN_DIR/umbrella" <<EOF
#!/bin/sh
PYTHONPATH="$LIB_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PYTHON" -m umbrella "\$@"
EOF
chmod 755 "$BIN_DIR/umbrella"

say "Installed Umbrella $("$BIN_DIR/umbrella" --version | cut -d' ' -f2) to $BIN_DIR/umbrella"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) say ""
     say "Note: $BIN_DIR isn't on your PATH yet. Add this to your shell's startup file:"
     say "  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
say ""
say "Next: run 'umbrella init'"
