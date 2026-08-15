"""Backup/restore operational and security tests — TODO 0.7.3."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management.base import CommandError

from apps.core.backups import create_backup, restore_backup

pytestmark = pytest.mark.django_db


@pytest.fixture
def postgres_settings(settings, tmp_path):
    settings.DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "dojo_test",
            "USER": "dojo_user",
            "PASSWORD": "top-secret-db-password",
            "HOST": "db.internal",
            "PORT": "5432",
        }
    }
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.BASE_DIR = tmp_path
    return settings


def fake_postgres(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if "--file" in command:
            Path(command[command.index("--file") + 1]).write_bytes(b"PGDUMP")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("apps.core.backups.subprocess.run", run)
    return calls


def test_backup_contains_dump_media_and_hash_manifest(postgres_settings, tmp_path, monkeypatch):
    calls = fake_postgres(monkeypatch)
    media = Path(postgres_settings.MEDIA_ROOT)
    media.mkdir()
    (media / "student.txt").write_text("photo bytes", encoding="utf-8")

    output = create_backup(tmp_path / "safe" / "backup.tar.gz")

    with tarfile.open(output) as archive:
        names = archive.getnames()
        manifest = json.load(archive.extractfile("manifest.json"))
    assert names == ["manifest.json", "database.dump", "media/student.txt"]
    assert manifest["format"] == 1
    assert manifest["media"][0]["path"] == "student.txt"
    command, kwargs = calls[0]
    assert command[0] == "pg_dump"
    assert "top-secret-db-password" not in command
    assert kwargs["env"]["PGPASSWORD"] == "top-secret-db-password"


def test_backup_refuses_output_inside_media(postgres_settings, monkeypatch):
    fake_postgres(monkeypatch)
    media = Path(postgres_settings.MEDIA_ROOT)
    media.mkdir()

    with pytest.raises(CommandError, match="inside MEDIA_ROOT"):
        create_backup(media / "backup.tar.gz")


def test_backup_refuses_media_symlinks(postgres_settings, tmp_path, monkeypatch):
    fake_postgres(monkeypatch)
    media = Path(postgres_settings.MEDIA_ROOT)
    media.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    try:
        (media / "link.txt").symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")

    with pytest.raises(CommandError, match="symlink"):
        create_backup(tmp_path / "backup.tar.gz")


def test_restore_requires_exact_database_confirmation(postgres_settings, tmp_path):
    with pytest.raises(CommandError, match="confirm-database"):
        restore_backup(tmp_path / "missing.tar.gz", confirm_database="wrong")


def test_restore_rejects_path_traversal_before_running_pg_restore(
    postgres_settings, tmp_path, monkeypatch
):
    calls = fake_postgres(monkeypatch)
    archive_path = tmp_path / "hostile.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        payload = b"owned"
        member = tarfile.TarInfo("../outside.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    with pytest.raises(CommandError, match="Unsafe path"):
        restore_backup(archive_path, confirm_database="dojo_test")
    assert calls == []


def test_restore_rejects_tampered_dump_before_running_pg_restore(
    postgres_settings, tmp_path, monkeypatch
):
    calls = fake_postgres(monkeypatch)
    archive_path = tmp_path / "tampered.tar.gz"
    manifest = json.dumps({"format": 1, "database_sha256": "0" * 64, "media": []}).encode()
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, payload in (("manifest.json", manifest), ("database.dump", b"tampered")):
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

    with pytest.raises(CommandError, match="hash"):
        restore_backup(archive_path, confirm_database="dojo_test")
    assert calls == []


def test_backup_restore_round_trip_replaces_media(postgres_settings, tmp_path, monkeypatch):
    calls = fake_postgres(monkeypatch)
    media = Path(postgres_settings.MEDIA_ROOT)
    media.mkdir()
    (media / "record.txt").write_text("original", encoding="utf-8")
    archive = create_backup(tmp_path / "backup.tar.gz")
    (media / "record.txt").write_text("changed", encoding="utf-8")
    (media / "extra.txt").write_text("remove me", encoding="utf-8")

    restore_backup(archive, confirm_database="dojo_test")

    assert (media / "record.txt").read_text(encoding="utf-8") == "original"
    assert not (media / "extra.txt").exists()
    restore_call = calls[-1][0]
    assert restore_call[0] == "pg_restore"
    assert "--clean" in restore_call
    assert "--exit-on-error" in restore_call


def test_sqlite_backend_is_refused(settings, tmp_path):
    settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

    with pytest.raises(CommandError, match="PostgreSQL"):
        create_backup(tmp_path / "backup.tar.gz")


def test_restore_refuses_broad_media_root_before_pg_restore(
    postgres_settings, tmp_path, monkeypatch
):
    calls = fake_postgres(monkeypatch)
    postgres_settings.MEDIA_ROOT = postgres_settings.BASE_DIR
    archive = tmp_path / "valid.tar.gz"
    dump = b"PGDUMP"
    manifest = json.dumps(
        {
            "format": 1,
            "database_sha256": hashlib.sha256(dump).hexdigest(),
            "media": [],
        }
    ).encode()
    with tarfile.open(archive, "w:gz") as bundle:
        for name, payload in (("manifest.json", manifest), ("database.dump", dump)):
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))

    with pytest.raises(CommandError, match="too broad"):
        restore_backup(archive, confirm_database="dojo_test")
    assert calls == []
