"""Load the repo's .env (nearest one up from the working directory) into os.environ.

Real environment variables win, so deployments can override anything in the file.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(start: Path | None = None) -> Path | None:
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        f = d / ".env"
        if f.is_file():
            for raw in f.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip().removeprefix("export ").strip(), value.strip().strip("'\"")
                if value and key not in os.environ:
                    os.environ[key] = value
            return f
    return None
