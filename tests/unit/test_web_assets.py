import hashlib
import json
from pathlib import Path, PurePosixPath


def test_committed_browser_assets_match_their_build_inputs():
    root = Path(__file__).resolve().parents[2]
    frontend = root / "frontend"
    assets = root / "src" / "ekb" / "interfaces" / "web" / "static" / "ui"
    expected = {
        path for path in frontend.iterdir() if path.suffix in {".ts", ".js", ".json", ".html"}
    }
    expected.update(
        path for path in (frontend / "src").rglob("*") if path.suffix in {".ts", ".tsx", ".css"}
    )
    expected.add(assets.parent / "theme.css")
    recorded = json.loads((assets / "sources.json").read_text(encoding="utf-8"))
    assert set(recorded) == {path.relative_to(root).as_posix() for path in expected}
    for name, digest in recorded.items():
        text = (root / Path(*PurePosixPath(name).parts)).read_text(encoding="utf-8")
        assert hashlib.sha256(text.encode()).hexdigest() == digest, (
            f"Stale UI asset input: {name}; run npm run build in frontend"
        )
    for name in ("app.js", "app.css", "index.html", "LICENSES.txt"):
        assert (assets / name).stat().st_size > 0
