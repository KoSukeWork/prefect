"""Populate the VS Code language cache omitted by code-server's headless installer."""

import hashlib
import json
from pathlib import Path

data = Path.home() / ".local/share/code-server"
extensions = json.loads((data / "extensions/extensions.json").read_text())
cache_path = data / "languagepacks.json"
cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
for extension in extensions:
    if extension["identifier"]["id"] != "ms-ceintl.vscode-language-pack-zh-hans":
        continue
    folder = data / "extensions" / extension["relativeLocation"]
    manifest = json.loads((folder / "package.json").read_text())
    for language in manifest["contributes"]["localizations"]:
        cache[language["languageId"]] = {
            "hash": hashlib.md5(
                (extension["identifier"]["id"] + extension["version"]).encode(),
                usedforsecurity=False,
            ).hexdigest(),
            "extensions": [
                {
                    "extensionIdentifier": extension["identifier"],
                    "version": extension["version"],
                }
            ],
            "translations": {
                item["id"]: str((folder / item["path"]).resolve())
                for item in language["translations"]
            },
            "label": language["localizedLanguageName"],
        }
cache_path.write_text(json.dumps(cache), encoding="utf-8")
