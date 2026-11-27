#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the experimental headless AI Linux reference image (Ubuntu 26.04 base, A/B root slots).

This is a **prototype** (ADR-0012, proposed) — not the base-distribution decision of ADR-0005.

Outputs (in --out):
  ailx-<version>-<flavor>.raw     GPT disk: ESP | rootA | rootB | data   (bootable via UEFI, shim+GRUB signed by Ubuntu)
  ailx-<version>-<flavor>.qcow2   compressed copy of the disk for VMs
  rootfs-<version>-<flavor>.ext4  root filesystem image for A/B updates ('ailx image stage')
  manifest-<version>-<flavor>.json  package list with versions, sizes, sha256 of every artifact

Requires root, mmdebstrap, e2fsprogs (mke2fs -d), dosfstools, mtools, util-linux (sfdisk), qemu-img.
No loop devices or mounts are used, so it also runs in unprivileged-ish CI containers with root.

Example:
  sudo python3 packaging/image/build_image.py --deb dist/ailx_0.1.0-1_all.deb --version 1 \\
       --authorized-key ~/.ssh/id_ed25519.pub --data-preload /path/with/ailx/engines+models --out out/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = HERE / "files"
REPO = HERE.parents[1]
MiB = 1024 * 1024

BASE = [
    "systemd",
    "systemd-sysv",
    "udev",
    "dbus",
    "systemd-resolved",
    "systemd-timesyncd",
    "iproute2",
    "openssh-server",
    "sudo",
    "ca-certificates",
    "apt",
    "unattended-upgrades",
    "e2fsprogs",
    "dosfstools",
    "kmod",
    "initramfs-tools",
    "curl",
    "procps",
    "less",
    "vim-tiny",
    "libgomp1",
    # ailx runtime dependencies (also declared by the .deb)
    "python3",
    "python3-numpy",
    "python3-scipy",
    "python3-jsonschema",
    "python3-zstandard",
    "python3-rfc8785",
    "init-system-helpers",
]
FLAVORS = {
    # vm: virtio guests (QEMU/KVM, Proxmox) — small kernel package, no firmware blobs
    "vm": ["linux-image-virtual"],
    # hw: bare metal (workstation) — generic kernel, firmware, driver detection (NVIDIA via ubuntu-drivers)
    "hw": ["linux-image-generic", "linux-firmware", "ubuntu-drivers-common", "pciutils"],
}
GPT_ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
GPT_ROOT_X86_64 = "4F68BCE3-E8CD-4DB1-96E7-FBCAF984B709"
GPT_SRV = "3B8F8425-20E0-4F3B-907F-1A25A76F98E8"
DOC_EXCLUDES = [
    "path-exclude=/usr/share/man/*",
    "path-exclude=/usr/share/doc/*",
    "path-include=/usr/share/doc/*/copyright",
    "path-exclude=/usr/share/locale/*",
    "path-include=/usr/share/locale/locale.alias",
    "path-exclude=/usr/share/info/*",
    "path-exclude=/usr/share/lintian/*",
]


def sh(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, text=True, **kw)  # type: ignore[call-overload]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8 << 20):
            h.update(chunk)
    return h.hexdigest()


def chroot(root: Path, *cmd: str) -> None:
    sh(["chroot", str(root), "/usr/bin/env", "-i", "PATH=/usr/sbin:/usr/bin:/sbin:/bin", *cmd])


def bootstrap(root: Path, flavor: str, deb: Path, mirror: str, suite: str, bootpkgs: Path, epoch: int) -> None:
    sources = [
        f"deb {mirror} {suite} main universe",
        f"deb {mirror} {suite}-updates main universe",
        f"deb {mirror} {suite}-security main universe",
    ]
    pkgs = BASE + FLAVORS[flavor]
    hooks = [
        f'cp {deb} "$1/tmp/ailx.deb"',
        'chroot "$1" dpkg -i /tmp/ailx.deb',
        'rm -f "$1/tmp/ailx.deb"',
        # trial boots arm a watchdog already in the initramfs (QEMU finding: a slot without a working systemd
        # hangs in an initramfs fallback shell, where no systemd watchdog is ever armed); see ADR-0012
        f'install -D -m 0755 {FILES}/ailx-trial-watchdog.hook "$1/etc/initramfs-tools/hooks/ailx-trial-watchdog"',
        f"install -D -m 0755 {FILES}/ailx-trial-watchdog.init-bottom "
        '"$1/etc/initramfs-tools/scripts/init-bottom/ailx-trial-watchdog"',
        'chroot "$1" update-initramfs -u -k all',
        'chroot "$1" sh -c "cd /tmp && apt-get download shim-signed grub-efi-amd64-signed"',
        f'mv "$1"/tmp/shim-signed_*.deb "$1"/tmp/grub-efi-amd64-signed_*.deb {bootpkgs}/',
        # single quotes: the shell must not expand ${Package} etc.
        "chroot \"$1\" dpkg-query -W -f '${Package}\\t${Version}\\t${Installed-Size}\\n' > "
        + f"{bootpkgs}/packages.tsv",
    ]
    cmd = ["mmdebstrap", "--variant=apt", "--components=main,universe", f"--include={','.join(pkgs)}"]
    cmd += [f"--dpkgopt={o}" for o in DOC_EXCLUDES]
    cmd += [f"--customize-hook={h}" for h in hooks]
    cmd += [suite, str(root), *sources]
    sh(cmd, env=dict(os.environ, SOURCE_DATE_EPOCH=str(epoch)))


def configure(
    root: Path, version: str, flavor: str, build_id: str, deb_version: str, admin_key: str | None, nopasswd: bool
) -> dict[str, int]:
    def put(rel: str, data: str, mode: int = 0o644) -> None:
        p = root / rel.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)
        p.chmod(mode)

    f = HERE / "files"
    put("/etc/hostname", "ailx\n")
    put("/etc/hosts", "127.0.0.1 localhost\n127.0.1.1 ailx\n::1 localhost ip6-localhost ip6-loopback\n")
    put("/etc/fstab", (f / "fstab").read_text())
    # hang protection for A/B trial boots: a hardware watchdog (if present) resets a hung system,
    # GRUB then falls back to the default slot
    put(
        "/etc/systemd/system.conf.d/10-ailx-watchdog.conf",
        "[Manager]\nRuntimeWatchdogSec=2min\nRebootWatchdogSec=5min\n",
    )
    put("/etc/systemd/network/20-wired.network", (f / "20-wired.network").read_text())
    put("/etc/ssh/sshd_config.d/10-ailx.conf", (f / "10-ailx.conf").read_text())
    put("/usr/libexec/ailx/ailx-firstboot", (f / "ailx-firstboot").read_text(), 0o755)
    for unit in ("ailx-firstboot.service", "ailx-boot-health.service"):
        put(f"/etc/systemd/system/{unit}", (f / unit).read_text())
    put(
        "/etc/ailx/image-release",
        f'NAME="AI Linux reference image (experimental prototype)"\nVERSION="{version}"\nFLAVOR="{flavor}"\n'
        f'BUILD_ID="{build_id}"\nBASE="ubuntu-26.04"\nAILX_PACKAGE="{deb_version}"\n',
    )
    resolv = root / "etc/resolv.conf"
    if resolv.exists() or resolv.is_symlink():
        resolv.unlink()
    resolv.symlink_to("../run/systemd/resolve/stub-resolv.conf")
    for p in (root / "etc/ssh").glob("ssh_host_*"):  # never ship host keys: generated per machine at first boot
        p.unlink()
    (root / "etc/machine-id").write_text("")  # generated per machine/slot at first boot
    (root / "var/lib/dbus/machine-id").unlink(missing_ok=True)
    (root / "boot/efi").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(exist_ok=True)
    chroot(
        root,
        "systemctl",
        "enable",
        "systemd-networkd.service",
        "systemd-resolved.service",
        "ailx-firstboot.service",
        "ailx-boot-health.service",
    )
    chroot(
        root,
        "useradd",
        "--uid",
        "1000",
        "--user-group",
        "--groups",
        "sudo,ailx",
        "--home-dir",
        "/home/ailx-admin",
        "--no-create-home",
        "--shell",
        "/bin/bash",
        "ailx-admin",
    )
    chroot(root, "passwd", "--lock", "ailx-admin")
    if nopasswd:
        put("/etc/sudoers.d/90-ailx-admin", "ailx-admin ALL=(ALL) NOPASSWD:ALL\n", 0o440)
    # minimise: caches, logs, apt lists (re-downloaded by apt update)
    for d in ("var/cache/apt/archives", "var/lib/apt/lists", "var/log", "tmp", "var/tmp"):
        for p in (root / d).glob("*"):
            if p.is_file() or p.is_symlink():
                p.unlink()
            elif p.is_dir() and p.name not in ("partial",):
                shutil.rmtree(p, ignore_errors=True)
    gid = next(
        int(line.split(":")[2]) for line in (root / "etc/group").read_text().splitlines() if line.startswith("ailx:")
    )
    return {"ailx_gid": gid, "admin_uid": 1000, "admin_gid": 1000, "has_key": int(bool(admin_key))}


def data_skeleton(dst: Path, ids: dict[str, int], admin_key: str | None, preload: Path | None) -> None:
    for d, mode, gid in (
        ("ailx", 0o2770, ids["ailx_gid"]),
        ("ailx/transactions", 0o2770, ids["ailx_gid"]),
        ("ailx/engines", 0o2775, ids["ailx_gid"]),
        ("ailx/models", 0o2775, ids["ailx_gid"]),
        ("ailx/profiles", 0o2775, ids["ailx_gid"]),
        ("home", 0o755, 0),
        ("ssh", 0o755, 0),
    ):
        p = dst / d
        p.mkdir(parents=True, exist_ok=True)
        os.chown(p, 0, gid)
        p.chmod(mode)
    home = dst / "home/ailx-admin"
    (home / ".ssh").mkdir(parents=True, exist_ok=True)
    if admin_key:
        (home / ".ssh/authorized_keys").write_text(admin_key.strip() + "\n")
        (home / ".ssh/authorized_keys").chmod(0o600)
    for p in [home, *home.rglob("*")]:
        os.chown(p, ids["admin_uid"], ids["admin_gid"])
    (home / ".ssh").chmod(0o700)
    home.chmod(0o750)
    if preload:
        for sub in ("engines", "models"):
            if (preload / sub).is_dir():
                shutil.copytree(preload / sub, dst / "ailx" / sub, dirs_exist_ok=True, symlinks=True)
        for p in (dst / "ailx").rglob("*"):
            os.chown(p, 0, ids["ailx_gid"], follow_symlinks=False)


def mkext4(img: Path, label: str, src: Path, size_mib: int, fs_uuid: str, epoch: int) -> None:
    img.unlink(missing_ok=True)
    sh(
        [
            "mke2fs",
            "-q",
            "-t",
            "ext4",
            "-L",
            label,
            "-U",
            fs_uuid,
            "-E",
            f"root_owner=0:0,hash_seed={fs_uuid}",
            "-d",
            str(src),
            str(img),
            f"{size_mib}M",
        ],
        env=dict(os.environ, E2FSPROGS_FAKE_TIME=str(epoch)),
    )


def mkesp(img: Path, bootpkgs: Path, size_mib: int) -> None:
    ext = bootpkgs / "x"
    for deb in sorted(bootpkgs.glob("*.deb")):
        sh(["dpkg-deb", "-x", str(deb), str(ext)])
    shim = next(
        p
        for p in [ext / "usr/lib/shim/shimx64.efi.signed.latest", ext / "usr/lib/shim/shimx64.efi.signed"]
        if p.exists()
    )
    mm = ext / "usr/lib/shim/mmx64.efi"
    grub = ext / "usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed"
    img.unlink(missing_ok=True)
    sh(
        [
            "mkfs.vfat",
            "-C",
            "-F",
            "32",
            "-n",
            "AILX-ESP",
            "-i",
            "a11a0001",
            "--invariant",
            str(img),
            str(size_mib * 1024),
        ]
    )
    env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
    for d in ("::/EFI", "::/EFI/BOOT", "::/EFI/ubuntu"):
        sh(["mmd", "-i", str(img), d], env=env)
    grubenv = bootpkgs / "grubenv"
    body = "# GRUB Environment Block\ndefault_slot=A\n"
    grubenv.write_text(body + "#" * (1024 - len(body)))
    for src, dst in (
        (shim, "::/EFI/BOOT/BOOTX64.EFI"),
        (grub, "::/EFI/BOOT/grubx64.efi"),
        (mm, "::/EFI/BOOT/mmx64.efi"),
        (HERE / "files/grub.cfg", "::/EFI/BOOT/grub.cfg"),
        (HERE / "files/grub.cfg", "::/EFI/ubuntu/grub.cfg"),
        (grubenv, "::/EFI/ubuntu/grubenv"),
    ):
        sh(["mcopy", "-i", str(img), str(src), dst], env=env)


def assemble(disk: Path, parts: list[tuple[str, str, Path]], data_extra_mib: int) -> None:
    """parts: (name, gpt-type, fs-image). The last partition gets data_extra_mib of extra free space."""
    lba = 2048
    lines = ["label: gpt", "unit: sectors", "first-lba: 2048"]
    layout = []
    for i, (name, ptype, img) in enumerate(parts):
        sectors = (img.stat().st_size + 511) // 512
        if i == len(parts) - 1:
            sectors += data_extra_mib * MiB // 512
        lines.append(f'start={lba}, size={sectors}, type={ptype}, name="{name}"')
        layout.append((lba, img))
        lba += sectors + (2048 - sectors % 2048) % 2048
    disk.unlink(missing_ok=True)
    with open(disk, "wb") as f:
        f.truncate((lba + 2048) * 512)
    sh(["sfdisk", "--no-reread", "--no-tell-kernel", str(disk)], input="\n".join(lines) + "\n")
    for start, img in layout:
        sh(
            [
                "dd",
                f"if={img}",
                f"of={disk}",
                "bs=4M",
                f"seek={start * 512}",
                "oflag=seek_bytes",
                "conv=notrunc,sparse",
                "status=none",
            ]
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--deb", required=True, type=Path)
    ap.add_argument("--version", required=True, help="image version, e.g. 1")
    ap.add_argument("--flavor", choices=sorted(FLAVORS), default="vm")
    ap.add_argument("--out", type=Path, default=REPO / "out")
    ap.add_argument(
        "--mirror",
        default="http://archive.ubuntu.com/ubuntu",
        help="apt mirror; for byte-stable inputs use https://snapshot.ubuntu.com/ubuntu/<timestamp>",
    )
    ap.add_argument("--suite", default="resolute")
    ap.add_argument("--authorized-key", type=Path, help="SSH public key for user ailx-admin (no password login)")
    ap.add_argument("--no-admin-nopasswd", action="store_true", help="do not grant ailx-admin passwordless sudo")
    ap.add_argument(
        "--data-preload", type=Path, help="directory with engines/ and models/ to place on the data partition"
    )
    ap.add_argument("--root-size-mib", type=int, default=3072)
    ap.add_argument("--data-size-mib", type=int, default=2048)
    ap.add_argument("--rootfs-only", action="store_true", help="only produce the slot image (for updates)")
    a = ap.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("needs root (mmdebstrap/chroot)")
    tools = ["mmdebstrap", "mke2fs", "tune2fs", "dpkg-deb", "git", "du"]
    if not a.rootfs_only:
        tools += ["mkfs.vfat", "mmd", "mcopy", "sfdisk", "dd", "qemu-img"]
    missing = [t for t in tools if not shutil.which(t)]
    if missing:  # fail before the multi-minute bootstrap, not after it
        raise SystemExit(
            f"missing build tools: {', '.join(missing)} "
            "(Ubuntu packages: mmdebstrap e2fsprogs dosfstools mtools fdisk qemu-utils)"
        )
    t0 = time.time()
    epoch = int(
        os.environ.get("SOURCE_DATE_EPOCH")
        or subprocess.run(
            ["git", "-C", str(REPO), "log", "-1", "--format=%ct"], capture_output=True, text=True
        ).stdout.strip()
        or 0
    )
    git = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True
    ).stdout.strip()
    build_id = f"{a.version}-{git}{'+dirty' if dirty else ''}-{epoch}"  # +dirty: uncommitted tracked changes
    deb_version = subprocess.run(
        ["dpkg-deb", "-f", str(a.deb), "Version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    key = a.authorized_key.read_text() if a.authorized_key else None
    a.out.mkdir(parents=True, exist_ok=True)
    stem = f"{a.version}-{a.flavor}"
    ns = uuid.UUID("6f1c7a1e-0000-4000-8000-000000a11a00")
    with tempfile.TemporaryDirectory(prefix="ailx-image-", dir=a.out) as tmp:
        w = Path(tmp)
        root, boot = w / "root", w / "bootpkgs"
        boot.mkdir()
        bootstrap(root, a.flavor, a.deb.resolve(), a.mirror, a.suite, boot, epoch)
        # written by an mmdebstrap hook outside the rootfs (mmdebstrap empties /tmp during cleanup)
        packages = [line.split("\t") for line in (boot / "packages.tsv").read_text().splitlines()]
        ids = configure(root, a.version, a.flavor, build_id, deb_version, key, not a.no_admin_nopasswd)
        rootfs = a.out / f"rootfs-{stem}.ext4"
        # update images carry a neutral label; 'ailx image stage' relabels them to the target slot, so an
        # attached update image can never be mistaken for an installed slot (root=LABEL=… / GRUB search)
        mkext4(rootfs, "ailx-update", root, a.root_size_mib, str(uuid.uuid5(ns, build_id + "upd")), epoch)
        used_root_mib = int(
            subprocess.run(["du", "-sm", "--apparent-size", str(root)], capture_output=True, text=True).stdout.split()[
                0
            ]
        )
        artifacts = {"rootfs": rootfs}
        if not a.rootfs_only:
            roota, rootb = w / "rootA.ext4", w / "rootB.ext4"
            for slot, img in (("A", roota), ("B", rootb)):
                shutil.copyfile(rootfs, img)
                sh(["tune2fs", "-L", f"ailx-root{slot}", "-U", str(uuid.uuid5(ns, build_id + slot)), str(img)])
            skel = w / "data"
            data_skeleton(skel, ids, key, a.data_preload)
            data = w / "data.ext4"
            mkext4(data, "ailx-data", skel, a.data_size_mib, str(uuid.uuid5(ns, build_id + "data")), epoch)
            esp = w / "esp.vfat"
            mkesp(esp, boot, 128)
            disk = a.out / f"ailx-{stem}.raw"
            assemble(
                disk,
                [
                    ("ailx-esp", GPT_ESP, esp),
                    ("ailx-rootA", GPT_ROOT_X86_64, roota),
                    ("ailx-rootB", GPT_ROOT_X86_64, rootb),
                    ("ailx-data", GPT_SRV, data),
                ],
                0,
            )
            qcow = a.out / f"ailx-{stem}.qcow2"
            sh(["qemu-img", "convert", "-c", "-O", "qcow2", str(disk), str(qcow)])
            artifacts.update(disk=disk, qcow2=qcow)
    manifest = {
        "schema": "ailx.image-manifest/v0",
        "version": a.version,
        "flavor": a.flavor,
        "build_id": build_id,
        "base": f"ubuntu {a.suite}",
        "mirror": a.mirror,
        "ailx_package": deb_version,
        "source_date_epoch": epoch,
        "packages": [{"name": n, "version": v, "installed_kib": int(s or 0)} for n, v, s in packages],
        "package_count": len(packages),
        "rootfs_used_mib": used_root_mib,
        "artifacts": {
            k: {
                "file": p.name,
                "bytes": p.stat().st_size,
                "allocated_bytes": p.stat().st_blocks * 512,
                "sha256": sha256(p),
            }
            for k, p in artifacts.items()
        },
        "build_seconds": round(time.time() - t0),
        "notes": [
            "experimental prototype (ADR-0012, proposed); not hardware-validated",
            "byte-identical rebuilds additionally need a pinned --mirror snapshot",
        ],
    }
    mpath = a.out / f"manifest-{stem}.json"
    mpath.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({k: v for k, v in manifest.items() if k != "packages"}, indent=2))


if __name__ == "__main__":
    main()
