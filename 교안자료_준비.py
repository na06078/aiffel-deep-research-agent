"""고정 교안 자료를 검증하고 명시 요청에서만 내려받는다. 기존 파일은 덮어쓰지 않는다."""

import argparse
import hashlib
import io
import json
import stat
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

SOURCE_URL = (
    "https://raw.githubusercontent.com/88chacha/deepresearch-agent-data/"
    "21f6625aeebc5629e34545c717969e80b62b47c1/data/deepresearch_d48_data.zip"
)
ARCHIVE_HASH = "318eca28efb2f9e5fb2f289d2288d930126dc9dbd110fba4672429a0c7e0feb4"
DATA_HASHES = {
    "corpus.json": "583dbf10ee811796c80302e005bef71812476059dc79c3b5dd2fd3ea251e4ec7",
    "결과자료.json": "93efed1d4cf1b35909377203094f3fe0b8f2af18f9155668df775f79cbda6697",
}
MAX_ARCHIVE_BYTES = 1_048_576
MAX_MEMBER_BYTES = 1_500_000


class PreparationError(ValueError):
    """사용자가 조치할 수 있는 준비 오류. 인증값은 포함하지 않는다."""


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def download_archive():
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "AIFFEL-reproduction/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise PreparationError("download_failed: HTTP 200 required")
            data = response.read(MAX_ARCHIVE_BYTES + 1)
    except (OSError, urllib.error.URLError):
        raise PreparationError("download_failed: fixed source URL is unavailable") from None
    if len(data) > MAX_ARCHIVE_BYTES:
        raise PreparationError("archive_too_large")
    return data


def _archive_members(data):
    if _digest(data) != ARCHIVE_HASH:
        raise PreparationError("archive_hash_mismatch")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) != 2 or {item.filename for item in infos} != set(DATA_HASHES):
                raise PreparationError("unexpected_archive_members")
            result = {}
            for item in infos:
                if item.is_dir() or stat.S_ISLNK(item.external_attr >> 16):
                    raise PreparationError("unsafe_archive_member")
                if not 0 < item.file_size <= MAX_MEMBER_BYTES:
                    raise PreparationError("archive_member_too_large")
                with archive.open(item) as member:
                    content = member.read(MAX_MEMBER_BYTES + 1)
                if (
                    len(content) > MAX_MEMBER_BYTES
                    or _digest(content) != DATA_HASHES[item.filename]
                ):
                    raise PreparationError("archive_member_hash_mismatch")
                result[item.filename] = content
            return result
    except (zipfile.BadZipFile, RuntimeError, OSError):
        raise PreparationError("invalid_archive") from None


def _inventory(folder, downloaded):
    try:
        corpus = json.loads((folder / "corpus.json").read_text(encoding="utf-8"))
        docs, links = corpus["docs"], corpus["links"]
        if not isinstance(docs, dict) or not isinstance(links, dict):
            raise PreparationError("invalid_corpus")
        if set(links) != set(docs) or any(not isinstance(value, str) for value in docs.values()):
            raise PreparationError("invalid_corpus")
        if any(
            not isinstance(targets, list) or not set(targets).issubset(docs)
            for targets in links.values()
        ):
            raise PreparationError("invalid_corpus")
        count = sum(len(targets) for targets in links.values())
        if len(docs) != 34 or count != 428:
            raise PreparationError("corpus_count_mismatch")
    except (KeyError, ValueError, TypeError) as error:
        if isinstance(error, PreparationError):
            raise
        raise PreparationError("invalid_corpus") from None
    return {
        "documents": len(docs),
        "characters": sum(map(len, docs.values())),
        "links": count,
        "downloaded": downloaded,
        "source_url": SOURCE_URL,
        "data_hashes": dict(DATA_HASHES),
    }


def prepare_materials(root, *, download=False):
    folder = Path(root).resolve() / "01_참고자료/원본데이터"
    for name, expected in DATA_HASHES.items():
        path = folder / name
        if path.exists() and (not path.is_file() or _digest(path.read_bytes()) != expected):
            raise PreparationError(f"existing_hash_mismatch: {name}; original preserved")
    if all((folder / name).is_file() for name in DATA_HASHES):
        return _inventory(folder, False)
    if not download:
        raise PreparationError("materials_missing: run 교안자료_준비.py --download")
    data = download_archive()
    members = _archive_members(data)
    archive_path = folder / "교안_원본자료.zip"
    if archive_path.exists() and _digest(archive_path.read_bytes()) != ARCHIVE_HASH:
        raise PreparationError("existing_hash_mismatch: archive; original preserved")
    # Validate every byte before creating a destination, then use exclusive creation.
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in {"교안_원본자료.zip": data, **members}.items():
        path = folder / name
        if path.exists():
            continue
        try:
            with path.open("xb") as stream:
                stream.write(content)
        except FileExistsError:
            raise PreparationError("destination_changed: rerun read-only check") from None
    return _inventory(folder, True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--download", action="store_true", help="고정 자료가 없을 때만 준비")
    mode.add_argument("--check", action="store_true", help="검사만 수행 (기본)")
    args = parser.parse_args()
    try:
        result = prepare_materials(Path(__file__).resolve().parent, download=args.download)
    except PreparationError as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
