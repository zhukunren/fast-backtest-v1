"""Run the clean-checkout smoke required by the V1 full public edition."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test an exported V1 full public repository.")
    parser.add_argument("--skip-web-build", action="store_true")
    parser.add_argument("--skip-frontend-tests", action="store_true")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    _run([sys.executable, "tools/audit_full_public_surface.py"], env=env)
    _run([sys.executable, "-m", "pytest", "tests/test_offline_smoke.py", "tests/test_basic.py", "-q"], env=env)
    _run([sys.executable, "-m", "compileall", "-q", "app", "backtest", "config", "core", "data", "strategies", "web"], env=env)

    if not args.skip_web_build:
        _run([_npm(), "ci"], cwd=ROOT / "web" / "frontend", env=env)
        _run(
            [_npm(), "run", "audit:dependencies"],
            cwd=ROOT / "web" / "frontend",
            env=env,
        )
        if not args.skip_frontend_tests:
            _run([_npm(), "run", "test:unit"], cwd=ROOT / "web" / "frontend", env=env)
            _run([_npm(), "run", "test:coverage:all"], cwd=ROOT / "web" / "frontend", env=env)
            _run([_npm(), "run", "typecheck"], cwd=ROOT / "web" / "frontend", env=env)
        _run([_npm(), "run", "build"], cwd=ROOT / "web" / "frontend", env=env)

    _run([sys.executable, "tools/smoke_full_public_repo.py", "--skip-web-build", "--skip-frontend-tests"], env=env, nested=True)
    _check_web_app(require_frontend=not args.skip_web_build)
    print(f"[OK] full public repository smoke: {ROOT}")
    return 0


def _check_web_app(*, require_frontend: bool) -> None:
    from web.server import app

    client = app.test_client()
    library = client.get("/api/library")
    if library.status_code != 200:
        raise RuntimeError(f"Web library endpoint returned HTTP {library.status_code}")
    catalog = library.get_json(silent=True)
    items = catalog.get("items") if isinstance(catalog, dict) else None
    if not isinstance(items, list) or len(items) != 1:
        raise RuntimeError("Web library must expose exactly the full-release smoke strategy")
    item = items[0]
    if not isinstance(item, dict) or item.get("name") != "public_offline_smoke":
        raise RuntimeError("Web library exposed an unexpected release strategy")
    if item.get("description_status") != "verified":
        raise RuntimeError("Web release strategy description is not source-verified")
    health = client.get("/api/library/health")
    if health.status_code != 200:
        raise RuntimeError(f"Web health endpoint returned HTTP {health.status_code}")
    frontend = client.get("/")
    expected_frontend_status = 200 if require_frontend else 503
    if frontend.status_code != expected_frontend_status:
        raise RuntimeError(
            f"Web frontend entry returned HTTP {frontend.status_code}; "
            f"expected {expected_frontend_status}"
        )


def _npm() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def _run(
    command: list[str],
    *,
    env: dict[str, str],
    cwd: Path = ROOT,
    nested: bool = False,
) -> None:
    if nested:
        return
    print(f"[RUN] {' '.join(command)}")
    subprocess.run(command, cwd=str(cwd), env=env, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
