"""Bake Whisper models into the follower image, from a pinned source (follower spec 5.10).

    python fetch_models.py --lock models.lock.json --into /models tiny.en,large-v3
    python fetch_models.py --pin large-v3-turbo --lock models.lock.json
        # prints a lock entry to add or refresh, and on stderr what it changes in the lock

Only docker/follower.Dockerfile runs the first form, at BUILD time. Every model must be in
the lock file, which names its Hugging Face repository, one commit of it, and the SHA-256 of
every file the loader reads. A file that differs, is missing or is extra fails the build:
nothing unverified reaches an image. No token is read or sent: the models are public.

The result is a Hugging Face cache folder, the layout faster-whisper loads from with
HF_HUB_OFFLINE=1. `refs/main` is written by hand: the loader asks for the revision `main`,
and a download by commit does not record which commit that is.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

# What faster-whisper's own download asks for (faster_whisper/utils.py, download_model).
PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]
CHUNK = 1024 * 1024
COMMIT = re.compile(r"[0-9a-f]{40}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def names(text: str) -> list[str]:
    """`tiny.en, large-v3` -> ["tiny.en", "large-v3"]; commas or spaces, order kept."""
    found: list[str] = []
    for name in text.replace(",", " ").split():
        if name not in found:
            found.append(name)
    return found


def cache_folder(into: Path, repo: str) -> Path:
    return into / ("models--" + repo.replace("/", "--"))


def verify(snapshot: Path, files: dict[str, str]) -> list[str]:
    """What is wrong with a downloaded snapshot, as sentences; empty when it is the lock's."""
    problems = []
    present = {p.name for p in snapshot.iterdir()} if snapshot.is_dir() else set()
    for name in sorted(set(files) - present):
        problems.append(f"{name} is missing")
    for name in sorted(present - set(files)):
        problems.append(f"{name} is not in the lock file")
    for name in sorted(set(files) & present):
        found = sha256(snapshot / name)
        if found != files[name]:
            problems.append(f"{name} has SHA-256 {found}, the lock file says {files[name]}")
    return problems


def fetch(name: str, entry: dict, into: Path) -> None:
    from huggingface_hub import snapshot_download

    repo, revision, files = entry["repo"], entry["revision"], entry["files"]
    if not COMMIT.fullmatch(revision):
        raise SystemExit(
            f"error: {name} ({repo}@{revision}): the revision is not a full commit hash"
        )
    folder = cache_folder(into, repo)
    snapshot = Path(
        snapshot_download(repo, revision=revision, cache_dir=into, allow_patterns=sorted(files))
    )
    problems = verify(snapshot, files)
    if problems:
        # Nothing that failed its check stays behind: without refs/main the loader cannot find
        # it, and a folder with no refs/main is removed so it does not look like a download.
        if not (folder / "refs" / "main").exists():
            shutil.rmtree(folder, ignore_errors=True)
        raise SystemExit(f"error: {name} ({repo}@{revision}): " + "; ".join(problems))
    refs = folder / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(revision, encoding="utf-8")
    size = sum((snapshot / file).stat().st_size for file in files)
    print(f"baked {name}: {repo}@{revision[:12]}, {len(files)} files, {size // 1_000_000} MB")


def pin(name: str, repo: str | None) -> dict:
    """A lock entry for the repository's current `main`, with every file hashed."""
    import tempfile

    from huggingface_hub import snapshot_download

    if repo is None:
        from faster_whisper.utils import _MODELS

        repo = name if "/" in name else _MODELS[name]
    with tempfile.TemporaryDirectory() as folder:
        snapshot = Path(snapshot_download(repo, cache_dir=folder, allow_patterns=PATTERNS))
        files = {p.name: sha256(p) for p in sorted(snapshot.iterdir())}
        return {name: {"repo": repo, "revision": snapshot.name, "files": files}}


def changes(name: str, old: dict | None, new: dict) -> list[str]:
    """What `--pin` would change in the lock file, as sentences for the reviewer."""
    if old is None:
        return [f"{name}: new entry ({new['repo']}@{new['revision']}, {len(new['files'])} files)"]
    found = []
    for key in ("repo", "revision"):
        if old[key] != new[key]:
            found.append(f"{name}: {key} {old[key]} -> {new[key]}")
    for file in sorted(set(old["files"]) | set(new["files"])):
        before, after = old["files"].get(file), new["files"].get(file)
        if before is None:
            found.append(f"{name}: {file} added ({after})")
        elif after is None:
            found.append(f"{name}: {file} removed")
        elif before != after:
            found.append(f"{name}: {file} SHA-256 {before} -> {after}")
    return found or [f"{name}: unchanged"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("models", nargs="?", default="", help="names, comma-separated")
    parser.add_argument("--lock", type=Path, help="the lock file (models.lock.json)")
    parser.add_argument("--into", type=Path, help="the cache folder to fill")
    parser.add_argument(
        "--pin",
        metavar="NAME",
        help="print a lock entry for this model; with --lock, also say on stderr what it changes",
    )
    parser.add_argument("--repo", help="with --pin: the repository, when the name is not one")
    args = parser.parse_args(argv)
    if args.pin:
        entry = pin(args.pin, args.repo)
        old = None
        if args.lock is not None and args.lock.is_file():
            old = json.loads(args.lock.read_text(encoding="utf-8")).get(args.pin)
        print(json.dumps(entry, indent=2))
        for line in changes(args.pin, old, entry[args.pin]):
            print(line, file=sys.stderr)
        return 0
    if args.lock is None or args.into is None:
        parser.error("--lock and --into are required")
    args.into.mkdir(parents=True, exist_ok=True)
    wanted = names(args.models)
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    unknown = [name for name in wanted if name not in lock]
    if unknown:
        print(
            f"error: not in {args.lock.name}: {', '.join(unknown)}."
            f" Known: {', '.join(sorted(lock))}."
            " Add one with `python docker/fetch_models.py --pin NAME`.",
            file=sys.stderr,
        )
        return 2
    for name in wanted:
        fetch(name, lock[name], args.into)
    if not wanted:
        print("no model baked: the follower downloads its model on first start")
    return 0


if __name__ == "__main__":
    sys.exit(main())
