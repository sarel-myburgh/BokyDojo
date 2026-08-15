"""PostgreSQL plus media backup archives — TODO 0.7.3."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.management.base import CommandError

FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "database.dump"
MEDIA_PREFIX = "media"


def _database_config() -> dict:
    config = settings.DATABASES["default"]
    if config.get("ENGINE") != "django.db.backends.postgresql":
        raise CommandError("Backup and restore require the PostgreSQL database backend.")
    return config


def _postgres_env(config: dict) -> dict[str, str]:
    environment = os.environ.copy()
    password = config.get("PASSWORD")
    if password:
        environment["PGPASSWORD"] = str(password)
    return environment


def _connection_args(config: dict) -> list[str]:
    arguments = []
    for flag, key in (("--host", "HOST"), ("--port", "PORT"), ("--username", "USER")):
        value = config.get(key)
        if value:
            arguments.extend((flag, str(value)))
    return arguments


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _validate_media_root(media_root: Path) -> Path:
    resolved = media_root.resolve()
    filesystem_root = Path(resolved.anchor).resolve()
    base_dir = Path(settings.BASE_DIR).resolve()
    if resolved in {filesystem_root, base_dir}:
        raise CommandError("MEDIA_ROOT is too broad for backup or restore.")
    return resolved


def _media_files(media_root: Path):
    if not media_root.exists():
        return []
    files = []
    for path in sorted(media_root.rglob("*")):
        if path.is_symlink():
            raise CommandError(f"Refusing to back up media symlink: {path}")
        if path.is_file():
            files.append(path)
    return files


def create_backup(destination: str | Path | None = None) -> Path:
    config = _database_config()
    media_root = _validate_media_root(Path(settings.MEDIA_ROOT))
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if destination is None:
        output = Path(settings.BASE_DIR, "backups", f"dojomaster-{timestamp}.tar.gz")
    else:
        output = Path(destination).expanduser()
        if output.exists() and output.is_dir():
            output = output / f"dojomaster-{timestamp}.tar.gz"
    output = output.resolve()
    if _is_within(output, media_root):
        raise CommandError("The backup archive may not be written inside MEDIA_ROOT.")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="dojomaster-backup-") as temporary:
        temporary_path = Path(temporary)
        dump_path = temporary_path / DATABASE_NAME
        command = [
            getattr(settings, "PG_DUMP_BINARY", "pg_dump"),
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(dump_path),
            *_connection_args(config),
            str(config["NAME"]),
        ]
        try:
            subprocess.run(
                command,
                env=_postgres_env(config),
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise CommandError(f"pg_dump failed: {detail.strip()}") from exc

        media_entries = []
        media_files = _media_files(media_root)
        for path in media_files:
            relative = path.relative_to(media_root).as_posix()
            media_entries.append(
                {"path": relative, "sha256": _sha256(path), "size": path.stat().st_size}
            )

        manifest = {
            "format": FORMAT_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "database_sha256": _sha256(dump_path),
            "media": media_entries,
        }
        manifest_path = temporary_path / MANIFEST_NAME
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

        temporary_archive = output.with_name(f".{output.name}.partial")
        try:
            with tarfile.open(temporary_archive, "w:gz") as archive:
                archive.add(manifest_path, arcname=MANIFEST_NAME, recursive=False)
                archive.add(dump_path, arcname=DATABASE_NAME, recursive=False)
                for path in media_files:
                    relative = path.relative_to(media_root).as_posix()
                    archive.add(path, arcname=f"{MEDIA_PREFIX}/{relative}", recursive=False)
            temporary_archive.replace(output)
            try:
                output.chmod(0o600)
            except OSError:
                pass
        finally:
            temporary_archive.unlink(missing_ok=True)
    return output


def _validate_members(archive: tarfile.TarFile) -> None:
    names = set()
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise CommandError(f"Unsafe path in backup archive: {member.name}")
        if member.issym() or member.islnk() or member.isdev():
            raise CommandError(f"Unsupported archive member: {member.name}")
        if not (
            member.name in {MANIFEST_NAME, DATABASE_NAME}
            or member.name == MEDIA_PREFIX
            or member.name.startswith(f"{MEDIA_PREFIX}/")
        ):
            raise CommandError(f"Unexpected archive member: {member.name}")
        if member.name in names:
            raise CommandError(f"Duplicate archive member: {member.name}")
        names.add(member.name)
    if MANIFEST_NAME not in names or DATABASE_NAME not in names:
        raise CommandError("Backup archive is missing its manifest or database dump.")


def _verify_payload(extracted: Path) -> dict:
    try:
        manifest = json.loads((extracted / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CommandError("Backup manifest is unreadable.") from exc
    if manifest.get("format") != FORMAT_VERSION:
        raise CommandError("Unsupported backup archive format.")
    dump_path = extracted / DATABASE_NAME
    if _sha256(dump_path) != manifest.get("database_sha256"):
        raise CommandError("Database dump hash does not match the manifest.")

    expected = {entry["path"]: entry for entry in manifest.get("media", [])}
    media_root = extracted / MEDIA_PREFIX
    actual = {}
    if media_root.exists():
        for path in _media_files(media_root):
            relative = path.relative_to(media_root).as_posix()
            actual[relative] = {"sha256": _sha256(path), "size": path.stat().st_size}
    if set(actual) != set(expected):
        raise CommandError("Media file list does not match the manifest.")
    for relative, metadata in actual.items():
        expected_entry = expected[relative]
        if metadata["sha256"] != expected_entry.get("sha256") or metadata[
            "size"
        ] != expected_entry.get("size"):
            raise CommandError(f"Media hash does not match the manifest: {relative}")
    return manifest


def restore_backup(archive_path: str | Path, *, confirm_database: str) -> None:
    config = _database_config()
    database_name = str(config["NAME"])
    if confirm_database != database_name:
        raise CommandError(
            f"Refusing destructive restore. Pass --confirm-database {database_name!r}."
        )
    source = Path(archive_path).expanduser().resolve()
    if not source.is_file():
        raise CommandError(f"Backup archive does not exist: {source}")

    with tempfile.TemporaryDirectory(prefix="dojomaster-restore-") as temporary:
        extracted = Path(temporary)
        try:
            with tarfile.open(source, "r:*") as archive:
                _validate_members(archive)
                archive.extractall(extracted, filter="data")
        except (OSError, tarfile.TarError) as exc:
            raise CommandError(f"Backup archive cannot be read: {exc}") from exc
        _verify_payload(extracted)

        media_root = _validate_media_root(Path(settings.MEDIA_ROOT))
        media_root.parent.mkdir(parents=True, exist_ok=True)
        staged_media = extracted / MEDIA_PREFIX
        staged_media.mkdir(exist_ok=True)
        old_media = media_root.with_name(f".{media_root.name}.before-restore")
        if old_media.exists():
            raise CommandError(f"Restore staging path already exists: {old_media}")

        command = [
            getattr(settings, "PG_RESTORE_BINARY", "pg_restore"),
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            *_connection_args(config),
            "--dbname",
            database_name,
            str(extracted / DATABASE_NAME),
        ]
        try:
            subprocess.run(
                command,
                env=_postgres_env(config),
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise CommandError(f"pg_restore failed: {detail.strip()}") from exc

        try:
            if media_root.exists():
                media_root.replace(old_media)
            shutil.copytree(staged_media, media_root)
        except Exception:
            if media_root.exists():
                shutil.rmtree(media_root)
            if old_media.exists():
                old_media.replace(media_root)
            raise
        else:
            if old_media.exists():
                shutil.rmtree(old_media)
