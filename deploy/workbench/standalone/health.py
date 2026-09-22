"""Check every supervised process and essential HTTP endpoints."""

import json
import os
import urllib.request
from pathlib import Path

for pid in json.loads(Path("/tmp/workbench-ready.json").read_text()).values():
    os.kill(pid, 0)
for port, path in [
    (4200, "/api/health"),
    (8081, "/healthz"),
    (8082, "/health"),
    (8091, "/"),
    (8090, "/"),
]:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}{path}", timeout=2
    ) as response:
        assert response.status == 200
