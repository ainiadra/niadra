"""The tag of each image a release runs, from what the image is built of rather than from the release.

A tag is `src-` and 16 hex characters of a SHA-256 over the image's build: its Dockerfile target and
build arguments, then the path, mode and content of every file in its build context. A release that
changes only the site keeps the tags of server and models, so helm leaves every back-end pod as it
is; one that changes only the server leaves site and site-api alone; one that changes only the chart
keeps all four. On 28/09/2026 a site-only release retagged all four images with the release's name and
rolled every back-end pod at once, and the API answered 502 for a moment.

The digest reads every file of the context except `.git`, `__pycache__` and compiled Python: a
.dockerignore could leave more out, and reading a file the image does not hold only costs a rollout.

An image whose inputs match the live release's keeps the tag it runs under. on-ops.sh writes each
release's tags and digests to `<release>/images` once they are known; the next release reads that file
from /opt/niadra/current. helm skips an upgrade that renders nothing new, so a release that changes
nothing leaves the cluster as it was, and the file is what carries the tags forward. A live release
without the file lends no tag: every image gets its own.

    python3 scripts/image_tags.py <release dir> [--live <dir>] [--no-site]

prints one line per image, `<name> <tag> kept|new <digest>`: `kept` is a tag the live release runs,
`new` a tag to build (on-ops.sh skips the build when the registry has it already).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path

SKIPPED_DIRS = {".git", "__pycache__"}
TAG_PREFIX = "src-"


def builds(root: Path, site: bool) -> dict[str, tuple[Path, list[str]]]:
    """Each image of release directory `root`: its build context and `docker build` options."""
    scripts = root / "deploy" / "scripts"
    embed_model = _run(scripts / "embed-model.sh")
    ocr = _run(scripts / "models-ocr.sh")
    images = {
        "server": (root / "server", []),
        "models": (
            root / "server" / "models",
            ["--build-arg", f"EMBED_MODEL={embed_model}", "--build-arg", f"OCR={ocr}"],
        ),
    }
    if site:
        images["site"] = (root / "web", ["--target", "site"])
        images["site-api"] = (root / "web", ["--target", "site-api"])
    return images


def digest(context: Path, options: list[str]) -> str:
    if not context.is_dir():
        raise FileNotFoundError(f"no build context at {context}")
    h = hashlib.sha256()
    for option in options:
        h.update(option.encode() + b"\0")
    h.update(b"\0")
    for path in _files(context):
        info = path.lstat()
        relative = path.relative_to(context).as_posix()
        h.update(f"{relative}\0{stat.S_IMODE(info.st_mode):o}\0".encode())
        if path.is_symlink():
            h.update(b"link\0" + os.readlink(path).encode())
        else:
            h.update(b"file\0" + hashlib.sha256(path.read_bytes()).digest())
        h.update(b"\0")
    return h.hexdigest()


def _files(context: Path) -> list[Path]:
    found = []
    for directory, dirs, names in os.walk(context):
        dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS]
        found.extend(Path(directory) / n for n in names if not n.endswith((".pyc", ".pyo")))
    return sorted(found, key=lambda p: p.relative_to(context).as_posix())


def _run(script: Path) -> str:
    return subprocess.run(["bash", str(script)], check=True, capture_output=True, text=True).stdout.strip()


def recorded(release: Path) -> dict[str, tuple[str, str]]:
    """The tag and digest of each image a release ran, from its `images` file (empty without one)."""
    try:
        lines = (release / "images").read_text().splitlines()
    except FileNotFoundError:
        return {}
    return {name: (tag, value) for name, tag, value in (line.split() for line in lines if line.strip())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("release", type=Path)
    parser.add_argument("--live", type=Path)
    parser.add_argument("--no-site", action="store_true")
    args = parser.parse_args()

    site = not args.no_site
    wanted = {name: digest(*build) for name, build in builds(args.release, site).items()}
    running: dict[str, tuple[str, str]] = {}
    if args.live and args.live.is_dir() and args.live.resolve() != args.release.resolve():
        try:
            running = recorded(args.live.resolve())
        except (OSError, ValueError) as error:
            print(f"    live release not comparable ({error}); every image gets its own tag", file=sys.stderr)
            running = {}
    for name, value in wanted.items():
        if name in running and running[name][1] == value:
            print(name, running[name][0], "kept", value)
        else:
            print(name, TAG_PREFIX + value[:16], "new", value)


if __name__ == "__main__":
    main()
