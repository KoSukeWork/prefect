#!/bin/sh
set -eu

# Install from the image into the persistent editor volume, also on first boot.
# No registry access is needed at startup.
if ! code-server --list-extensions | grep -Fqx 'ms-ceintl.vscode-language-pack-zh-hans'; then
    code-server --install-extension /opt/code-server-language/zh-hans.vsix --force
fi
python /usr/local/bin/editor-language.py

exec "$@"
