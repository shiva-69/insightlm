"""The architecture, asserted.

``import-linter`` runs in CI as its own step; running it here too means a boundary
violation shows up in the same place as every other broken test, which is where people
actually look.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_import_contracts_hold() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "importlinter.cli", "lint"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "Architecture contracts broken. This is not a lint failure to wave through -- "
        "it means a dependency crossed a boundary that docs/02 says it cannot.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_domain_layers_import_no_infrastructure() -> None:
    """A direct check, so the guarantee survives anyone editing .importlinter."""
    forbidden = ("sqlalchemy", "fastapi", "httpx", "redis", "arq", "pydantic")
    offenders: list[str] = []

    for module_dir in (PROJECT_ROOT / "src" / "insightlm").iterdir():
        domain = module_dir / "domain"
        if not domain.is_dir():
            continue
        for source in domain.rglob("*.py"):
            text = source.read_text()
            for package in forbidden:
                if f"import {package}" in text or f"from {package}" in text:
                    offenders.append(f"{source.relative_to(PROJECT_ROOT)} imports {package}")

    assert not offenders, "domain layer touched infrastructure: " + "; ".join(offenders)
