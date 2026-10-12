#!/usr/bin/env bash
# Puts a `cortex` command on your PATH pointing at this checkout, and cortex
# itself in your app menu, so it can be started without a terminal from now on.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${HOME}/.local/bin"
mkdir -p "$BIN"

cat > "${BIN}/cortex" <<LAUNCH
#!/usr/bin/env bash
exec python3 "${SRC}/cortex.py" "\$@"
LAUNCH
chmod +x "${BIN}/cortex"

echo "installed: ${BIN}/cortex  ->  ${SRC}/cortex.py"
case ":${PATH}:" in
  *":${BIN}:"*) echo "PATH ok. run: cortex" ;;
  *) echo "add to your shell rc:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# The app-menu entry remembers this shell's PATH and $EDITOR, because an app
# menu does not read your shell's startup files. `cortex --remove-launcher`
# takes it out again.
python3 "${SRC}/cortex.py" --install-launcher || true
