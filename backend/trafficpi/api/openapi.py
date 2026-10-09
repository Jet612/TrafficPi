"""Write the API's OpenAPI schema, the source of the dashboard's TypeScript types.

    uv run python -m trafficpi.api.openapi ../dashboard/src/lib/openapi.json

``scripts/gen_api_types.sh`` runs this and then turns the schema into ``api-types.ts``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from trafficpi.api.app import create_app
from trafficpi.api.settings import ApiSettings
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store


def build_schema() -> dict[str, Any]:
    """The schema of the real application, built without touching any real data."""
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    with tempfile.TemporaryDirectory() as scratch:
        app = create_app(
            ApiSettings(data_dir=Path(scratch), dashboard_dir=None), store=Store(engine)
        )
        return app.openapi()


def render(schema: dict[str, Any]) -> str:
    """Stable text, so that a regenerated file only differs when the API does."""
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m trafficpi.api.openapi OUTPUT.json", file=sys.stderr)
        return 2
    Path(args[0]).write_text(render(build_schema()), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
