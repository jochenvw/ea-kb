import subprocess
from pathlib import Path


def test_meaningful_folders_have_readmes():
    root = Path(__file__).resolve().parents[2]
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    folders: set[Path] = set()
    fixtures = Path("tests") / "fixtures"
    for name in listing.stdout.split("\0"):
        if not name or not (root / name).is_file():
            continue
        for folder in Path(name).parents:
            if folder == Path("."):
                continue
            if folder != fixtures and folder.is_relative_to(fixtures):
                continue
            folders.add(folder)
    missing = []
    for folder in sorted(folders):
        guide = root / folder / "README.md"
        if not guide.is_file() or not guide.read_text(encoding="utf-8").strip():
            missing.append(str(folder))
    assert not missing, "Add a short, nonempty README.md in:\n" + "\n".join(missing)
