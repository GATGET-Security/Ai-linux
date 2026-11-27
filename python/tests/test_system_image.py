# SPDX-License-Identifier: MIT
"""A/B slot logic of 'ailx image' against a fake root (boot-level behaviour is validated in QEMU)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ailx.system.image import GRUBENV_SIZE, ImageError, ImageManager, read_grubenv, write_grubenv
from ailx.system.paths import load_layout


@pytest.fixture
def image_host(host: Path) -> Path:
    env = host / "boot/efi/EFI/ubuntu/grubenv"
    env.parent.mkdir(parents=True)
    write_grubenv(env, {"default_slot": "A"})
    (host / "proc/cmdline").write_text("BOOT_IMAGE=/boot/vmlinuz root=LABEL=ailx-rootA rw ailx.slot=A ailx.trial=0\n")
    (host / "etc/ailx").mkdir(parents=True, exist_ok=True)
    (host / "etc/ailx/image-release").write_text('VERSION="1"\nBUILD_ID="1-abc"\n')
    return host


def test_grubenv_format_is_grub_compatible(tmp_path: Path) -> None:
    p = tmp_path / "grubenv"
    write_grubenv(p, {"default_slot": "B", "next_slot": "", "trial_result": "ok"})
    raw = p.read_bytes()
    assert len(raw) == GRUBENV_SIZE and raw.startswith(b"# GRUB Environment Block\n") and raw.endswith(b"#")
    assert read_grubenv(p) == {"default_slot": "B", "trial_result": "ok"}  # empty values are dropped


def test_status_confirm_rollback(image_host: Path) -> None:
    im = ImageManager(load_layout())
    st = im.status()
    assert st["image"] and st["current_slot"] == "A" and not st["trial_boot"] and st["release"]["VERSION"] == "1"
    (image_host / "proc/cmdline").write_text("root=LABEL=ailx-rootB ailx.slot=B ailx.trial=1\n")
    assert im.status()["trial_boot"]
    im.confirm()
    assert read_grubenv(im.grubenv)["default_slot"] == "B"
    with pytest.raises(ImageError, match="slot A not present"):
        im.rollback()  # no /dev/disk/by-label/ailx-rootA in the fake root


def test_health_confirms_or_records_failure(image_host: Path) -> None:
    im = ImageManager(load_layout())
    (image_host / "proc/cmdline").write_text("ailx.slot=B ailx.trial=1\n")
    r = im.health([("doctor-no-fail", True), ("no-failed-units", False)])
    env = read_grubenv(im.grubenv)
    assert not r["confirmed"] and env["default_slot"] == "A" and env["trial_result"].startswith("failed:")
    assert im.health([("doctor-no-fail", True)])["confirmed"] and read_grubenv(im.grubenv)["default_slot"] == "B"


def test_status_without_root_access_to_the_esp(image_host: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The ESP is mounted umask=0077: unprivileged status must still identify the image (QEMU finding)."""
    import ailx.system.image as image_mod

    def denied(path: Path) -> dict[str, str]:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(image_mod, "read_grubenv", denied)
    im = ImageManager(load_layout())
    st = im.status()
    assert st["image"] and st["current_slot"] == "A" and "root only" in st["note"] and "default_slot" not in st
    with pytest.raises(ImageError, match="needs root"):
        im.confirm()


def _ext4(path: Path, label: str, mib: int, marker: str | None = None) -> None:
    subprocess.run(["truncate", "-s", f"{mib}M", str(path)], check=True)
    subprocess.run(["mke2fs", "-q", "-F", "-t", "ext4", "-L", label, str(path)], check=True)
    if marker:
        subprocess.run(["debugfs", "-w", "-R", f"mkdir {marker}", str(path)], check=True, capture_output=True)


@pytest.mark.skipif(not shutil.which("mke2fs") or not shutil.which("debugfs"), reason="needs e2fsprogs")
def test_stage_writes_inactive_slot_and_arms_one_trial(image_host: Path, tmp_path: Path) -> None:
    """Regression (QEMU run 1): the slot is found by partition name although its filesystem label changes."""
    import hashlib

    links = image_host / "dev/disk/by-partlabel"
    links.mkdir(parents=True)
    _ext4(tmp_path / "slotA.img", "ailx-rootA", 16)
    _ext4(tmp_path / "slotB.img", "ailx-rootB", 16)
    (links / "ailx-rootA").symlink_to(tmp_path / "slotA.img")
    (links / "ailx-rootB").symlink_to(tmp_path / "slotB.img")
    upd = tmp_path / "update.ext4"
    _ext4(upd, "ailx-update", 12, marker="v2")
    im = ImageManager(load_layout())
    with pytest.raises(ImageError, match="NOT armed"):
        im.stage(str(upd), sha256="0" * 64, log=lambda s: None)
    assert "next_slot" not in read_grubenv(im.grubenv)
    r = im.stage(str(upd), sha256=hashlib.sha256(upd.read_bytes()).hexdigest(), log=lambda s: None)
    assert r["slot"] == "B" and read_grubenv(im.grubenv)["next_slot"] == "B"
    label = subprocess.run(["e2label", str(tmp_path / "slotB.img")], capture_output=True, text=True).stdout.strip()
    assert label == "ailx-rootB"  # relabeled after verification
    ls = subprocess.run(["debugfs", "-R", "ls /", str(tmp_path / "slotB.img")], capture_output=True, text=True)
    assert "v2" in ls.stdout
    _ext4(upd, "ailx-rootA", 12)  # a source carrying the running slot's label is refused before writing
    with pytest.raises(ImageError, match="label of the running slot"):
        im.stage(str(upd), log=lambda s: None)


def test_not_an_image(host: Path) -> None:
    im = ImageManager(load_layout())
    assert im.status()["image"] is False
    with pytest.raises(ImageError):
        im.confirm()
