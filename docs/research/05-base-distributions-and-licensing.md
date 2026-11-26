# Base Distributions and Licensing Strategy

> Status: Phase 0 research · Last updated: 2026-11-26 · Evidence grading: see 00-method.md

## Summary

- **No distribution dominates; the ranking depends on the weights.** With the default weights below, Ubuntu 26.04 LTS scores highest *as a host*. It ships CUDA and ROCm in its archive, Canonical-signed NVIDIA modules and an amd64v3 variant [D-12][D-15][D-47][D-03]. Debian 13/14, Fedora 45 bootc and NixOS fall inside the ±10-point uncertainty band. If atomic updates get more weight, Fedora bootc draws level with Ubuntu (see 1.8).
- **The GPU stack separates the distributions more than the kernel does.** Ubuntu 26.04 carries CUDA in multiverse under a redistribution agreement, plus ROCm [D-12][D-13][D-15]. Fedora carries ROCm in main but gets NVIDIA only through third parties [D-35][D-38][D-40]. Debian has ROCm libraries in main and CUDA in non-free [D-43][D-44]. NixOS needs community caches for unfree CUDA [D-72].
- **ISA-optimized repos exist but their benefit is unproven.** CachyOS ships x86-64-v3, x86-64-v4 and znver4 repos [D-17]. Ubuntu 26.04 added an amd64v3 variant (public cloud images are v3-only) [D-03]. Fedora's v3 change slipped to F46 at the earliest [D-28]. Arch's v3 RFC was accepted but not implemented [D-21][D-23]. Phoronix found mixed gains [D-18]. None of this is proven for AI hot paths.
- **The immutability options are mature.** bootc (CNCF Sandbox since 2025-01-21) does OCI-native atomic upgrade and rollback [D-34]. The alternatives are NixOS generations, Yocto A/B with RAUC or SWUpdate [D-92], and systemd-sysext overlays [D-76]. Their CI-centric workflows suit a "build-heavy / deferred validation" strategy.
- **Reproducibility is improving.** Since 2026-05-09, Debian's testing migration rejects packages that do not reproduce, for the forky cycle [D-10][D-11]. Arch's rebuilderd reports about 86% reproducible packages, a snapshot [D-25].
- **sched_ext schedulers must be GPL-compatible.** They are BPF `struct_ops` programs, and the verifier rejects struct_ops programs without a GPL-compatible license [D-58][D-57]. User-space loaders can stay permissive (separate processes) [D-57].
- **NVIDIA's open kernel modules can be built and signed by us; the userspace cannot be replaced.** The modules are MIT per file and dual MIT/GPLv2 once linked [D-60]. They need matching proprietary GSP firmware and userspace from the same release and support Turing and later GPUs [D-59]. AI Linux can therefore build and sign the modules in CI.
- **CUDA, cuDNN and TensorRT: fetch on demand, never redistribute by default.** Their licenses allow redistribution only of the *designated* portions, in object code, inside an application with "material additional functionality". Developer tools are internal-use only [D-61][D-63][D-64]. The same default applies to model weights, whose licenses carry flow-down terms [D-70][D-71].
- **Provisional license set** [LEGAL REVIEW]:
  - `Apache-2.0 OR MIT` for userspace
  - `GPL-2.0-only` for kernel code and BPF
  - `CC-BY-4.0` for documentation
  - DCO sign-off rather than a CLA
  - SPDX/REUSE headers on every file
- **Compliance should be built in from day one.** EU CRA reporting for manufacturers has applied since 2026-09-11, and obligations for open-source stewards most likely start in December 2027; one source contradicts this date [D-89][D-91]. Plan for coordinated vulnerability disclosure (CVD), SBOMs (SPDX 3.0 or CycloneDX 1.7, which is ECMA-424) and SLSA v1.2 provenance from the start [D-82][D-83][D-81].

## Scope

- **Part 1** compares Ubuntu (the baseline), Debian, Arch, CachyOS, Fedora (including Atomic and bootc), NixOS, Yocto and Buildroot as bases for AI Linux. It also evaluates three hybrid strategies.
- **Part 2** covers licensing, contribution, governance and supply-chain policy, plus the redistribution limits of third-party AI stacks.
- **Out of scope:** runtime selection (doc 04), kernel tuning details (doc 02) and benchmarks. No measurements were taken.
- **This is not legal advice.** Items marked **[LEGAL REVIEW]** need counsel before any decision.

## Part 1: Base distributions

### 1.1 Verified release status (as of 2026-11-26)

| Distro | Current release (verified) | Kernel / toolchain (as shipped) | Support lifetime | Evidence |
|---|---|---|---|---|
| Ubuntu | 26.04 LTS "Resolute Raccoon" | Linux 7.0 (generic) | LTS. Ubuntu Pro extends ROCm support up to 15 years | [D-02][D-15] |
| Debian | 13 "trixie" (13.7 released 2026-09-12); forky (14) is in testing | 6.12 (trixie) | Security support to 2028-08-09, LTS to 2030-06-30, commercial ELTS to 2035-06-30 | [D-07][D-08][D-09][D-46] |
| Fedora | 45, released 2026-11-03 after two slips | Linux 7.2 per beta coverage (weak); GCC 16.2; Python 3.15 | About 13 months (EOL about 2027-11-24) | [D-01][D-93][D-94][D-95] |
| Arch | Rolling | Latest stable kernel | None (rolling) | — |
| CachyOS | Rolling (Arch-based) | Kernel fork on 7.1 with topic branches; Clang ThinLTO, AutoFDO and Propeller | None (rolling) | [D-19] |
| NixOS | 26.05 "Yarara" (2026-05-30) | Linux 6.18 default; GCC 15; LLVM 21; systemd stage 1 | Until 2026-12-31 (about 7 months) | [D-05][D-06] |
| Yocto | 6.0 "Wrynose" LTS | Linux 6.18 LTS; GCC 15.2; glibc 2.43; LLVM 22.1; Rust 1.94 | To April 2030 | [D-50] |
| Buildroot | 2026.02 (regular); 2025.02.x is the LTS | 6.19 kernel headers by default | LTS is the first release of each odd year, supported 3 years | [D-51][D-52] |

Kernel context:

- Linux 7.0 was released on 2026-04-12 and is not an LTS release [D-53].
- Linux 6.18 became the LTS kernel in December 2025. Its end-of-life date is disputed (see the Contradictions section) [D-54][D-55].
- NixOS 26.11 is due around now; its status was not verified.

### 1.2 Accelerator stacks and policy constraints

| Distro | NVIDIA driver | CUDA | ROCm | oneAPI | Policy notes |
|---|---|---|---|---|---|
| Ubuntu 26.04 | Archive; Canonical-signed precompiled `linux-modules-nvidia` | In archive (multiverse). Archive builds lag NVIDIA's repo: 13.1.x vs 13.3 in guide snapshots | `apt install rocm`. Shipped at 7.1, with SRU updates to 7.2.x | Intel apt repo (third-party) | Redistribution agreement requires packaging identical to NVIDIA's [D-13] [D-12][D-14][D-15][D-16][D-47] |
| Debian 13 | non-free; open flavor for Turing and later | `nvidia-cuda-toolkit` in non-free (12.4.1 seen) | Many libraries in main since trixie. 7.2.4 is in experimental. Debian's GPU support list differs from AMD's | `intel-mkl` in non-free (older) | DKMS plus MOK for Secure Boot [D-42][D-43][D-44][D-45][D-67] |
| Fedora 45 | Not in Fedora. Comes from RPM Fusion (akmods) or negativo17 | RPM Fusion points users to NVIDIA's repo | In main: 7.2.2 in F45, newer in Rawhide | Intel repo | Third-party repo policy. The PyTorch ROCm build stalled on a libstdc++ update [D-35][D-36][D-38][D-40][D-41] |
| Arch | `nvidia-open` is the default since the 590 driver (Turing and later). Pascal moved to the AUR | In repos (not verified this session) | In repos (not verified) | AUR (not verified) | [D-24] |
| CachyOS | Inherits from Arch | Inherits from Arch | Inherits from Arch | Inherits from Arch | Own kernels in v3, v4 and znver4 builds [D-19] |
| NixOS | nixpkgs (unfree) | Unfree packages are not built by Hydra. Community caches exist (`cache.nixos-cuda.org`, Flox) | `rocmPackages` (not verified) | Not verified | NixOS, Flox and NVIDIA announced CUDA redistribution on Nix [D-72][D-73][D-74] |
| Yocto / Buildroot | Do-it-yourself recipes; no first-class x86 dGPU support (hypothesis H-D-07) | Do-it-yourself | Do-it-yourself | Do-it-yourself | — |

### 1.3 Platform engineering properties

| Distro | x86-64-v3/v4 | Reproducible builds | Atomic update / rollback | Image tooling | Secure Boot with out-of-tree modules |
|---|---|---|---|---|---|
| Ubuntu | amd64v3 variant from 26.04. Archive packages are opt-in via dpkg/apt variants (C source). 26.10 has v3 daily ISOs | No data gathered | Classic: none built in | mkosi [D-77] | Signed prebuilt modules. DKMS needs MOK. Not every driver branch is signed [D-03][D-04][D-96][D-47][D-48] |
| Debian | No official port found (open question) | Required for testing migration since 2026-05-09 (forky) | None built in | mkosi; debos (not verified) | DKMS plus MOK (anecdotal) [D-10][D-42] |
| Fedora | Proposed v3 change pushed to F46 at the earliest. The hwcaps "v2" change was approved but reportedly never implemented | No data gathered | bootc / OSTree (Atomic desktops, CoreOS, IoT) | bootc-image-builder; image-builder [D-32][D-33] | akmods with a locally generated key enrolled via MOK [D-27][D-28][D-29][D-39] |
| Arch | RFC accepted, unimplemented as of late 2025 | About 86% overall (rebuilderd snapshot); bit-for-bit reproducible Docker image since April 2026 | None; btrfs with snapper possible | archiso (not verified) | User-managed (not verified) [D-21][D-22][D-23][D-25][D-26] |
| CachyOS | v3, v4 and znver4 repos | No data | btrfs snapshots (not verified) | — | Not verified [D-17] |
| NixOS | Possible via local rebuild, with no binary cache (hypothesis) | Design goal; no data gathered | Generations | `nixos-rebuild build-image` (nixos-generators was upstreamed in 25.05 and archived 2026-01-30) | Not verified (lanzaboote is a community project) [D-75] |
| Yocto | Any `-march` (the whole system is built from source) | Project goal (not verified) | A/B via RAUC (LGPL-2.1), SWUpdate (GPLv2) or Mender | BitBake | Do-it-yourself [D-92] |
| Buildroot | Any `-march` | Not verified | Do-it-yourself (RAUC or SWUpdate packages) | make | Do-it-yourself; better SBOM in 2026.02 [D-52] |

### 1.4 Per-distribution notes

**Ubuntu 26.04 (baseline).** Ubuntu 26.04 is the first LTS with CUDA in the archive, which Canonical announced on 2025-09-15 [D-12].

- **CUDA packaging:** a Launchpad packaging bug says the packages are built from NVIDIA binaries, must "match NVIDIA's own packaging exactly", and land in multiverse [D-13] (grade B).
- **ROCm:** Canonical formed a dedicated packaging team and promises SRU updates [D-15]. Phoronix-sourced reporting calls the shipped ROCm "months out of date" [D-16].
- **amd64v3:** public-cloud images for 26.04 are v3-only, and "no additional variant support beyond amd64v3" is planned for 26.04 [D-03].
- **sched_ext:** the only Ubuntu-specific evidence found is a community PPA [D-49]. Whether the stock kernel enables `CONFIG_SCHED_CLASS_EXT` is unknown.
- **Strength:** lowest friction for NVIDIA together with Secure Boot.
- **Weakness for a derivative:** vendor control (score C10).

**Debian.**

- **Governance and lifetime:** community governance and the longest community support window (5 years) [D-07], with commercial ELTS on top [D-09].
- **Reproducibility:** the 2026 reproducibility gate makes Debian the most *auditable* binary base [D-10]. The release team expects exceptions, and LWN notes that packages sometimes flip in and out of reproducibility [D-11].
- **Freshness:** stable kernels and toolchains are conservative (trixie ships 6.12) [D-46]. A wishlist bug asking to enable sched_ext in trixie kernels was open in April 2025, and its outcome was not verified [D-46].
- **ROCm:** an active Debian ROCm team packages it [D-44][D-45].
- **Fit:** good for an "own image" built with mkosi; immutability has to be added.

**Fedora 45 and Fedora bootc.**

- **Freshness:** the freshest kernel and toolchain of the release-based options [D-94].
- **Images:** bootc base images (minimal, minimal-plus, standard) are published as OCI images, with a documented way to build custom bases [D-30][D-31]. CoreOS, Atomic Desktops and IoT share a "fedora-derived" pipeline built `FROM fedora-bootc` [D-32].
- **ROCm:** packaged by a SIG that picks "the most stable ROCm release" 2–6 weeks before branching [D-37].
- **NVIDIA:** Fedora never ships it. Users rely on RPM Fusion akmods plus MOK enrollment [D-39], negativo17 [D-41], or NVIDIA's own CUDA repo; mixing them causes conflicts [D-38].
- **Lifetime:** about 13 months forces two upgrades a year.
- **Fit:** strongest candidate for "own image as OCI artifact" (strategy c).

**Arch.**

- **Freshness:** rolling, so the kernel and toolchain are always fresh.
- **NVIDIA:** the open modules became the default with the 590 driver [D-24].
- **x86-64-v3:** the official port has been "stuck in limbo" [D-22][D-23].
- **Reproducibility:** good per rebuilderd [D-25].
- **Fit:** no LTS and no atomic updates by default. A derivative inherits rolling breakage. Better suited as a *test host* than as a base.

**CachyOS.**

- **ISA repos:** the only verified source of v3, v4 and znver4 package repos [D-17].
- **Kernel:** linux-cachyos uses EEVDF with Clang ThinLTO, AutoFDO and Propeller, and supports sched_ext. The SCX schedulers are incompatible with the BMQ variant [D-19].
- **Fedora port:** a COPR ports the kernel to Fedora and ships `scx-manager` [D-20]. This is evidence that its kernel work can be reused on other distributions.
- **Performance claim:** the project estimates a 5–20% uplift for v3. That is a vendor claim without published methodology (grade D) [D-17], and Phoronix measured mixed results [D-18].
- **Risk:** small upstream team, so bus factor and governance.
- **Best use:** a *source of kernel patches and a benchmark reference*, not a base.

**NixOS.**

- **Strengths:** declarative system, generations and rollback.
- **Lifetime:** short support window [D-05].
- **CUDA:** unfree packages are absent from cache.nixos.org, so users rely on community or Flox caches [D-72][D-74]. A NixOS, Flox and NVIDIA redistribution arrangement exists [D-73].
- **Image building:** now native via `nixos-rebuild build-image` [D-75].
- **Fit:** well suited for strategy (b), Nix as a portable AI-stack layer on any host. As a base, the cost is the Nix learning curve (hypothesis H-D-06).

**Yocto and Buildroot.**

- **Strengths:** full source builds with any `-march`, strong SBOM and CVE tooling aimed at the CRA [D-50][D-52], long LTS (Yocto to 2030), and A/B updates via RAUC or SWUpdate [D-92]. This suits appliances and edge devices, and suits "build-heavy" pipelines.
- **Weaknesses:** no packaged Python/AI ecosystem, and no x86 dGPU proprietary-stack integration.
- **Fit:** Phase-2+ appliance target, not a primary base (hypothesis H-D-07).

### 1.5 Cross-cutting mechanisms

**Kernel and sched_ext.**

- **Requirements:** `CONFIG_SCHED_CLASS_EXT` plus BPF, BPF JIT and `DEBUG_INFO_BTF` [D-56].
- **Safety:** on error or stall, or on SysRq-S, the kernel restores the fair-class scheduler, and "system integrity is maintained no matter what the BPF scheduler does" [D-56]. This fits the "never trade stability" principle.
- **Delivery options:** ship our own kernel (CachyOS-style fork, signing burden) or depend on distro kernels that enable sched_ext. Enablement in Ubuntu, Debian and Fedora is not verified.

**systemd-sysext and confext.**

- Images overlay `/usr` and `/opt` (sysext) or `/etc` (confext) via overlayfs, read-only [D-76].
- All installed images activate at boot.
- `ID=` must match the host unless set to `_any`.
- systemd recommends Portable Services, not sysext, for shipping services [D-76].
- Consequence: a single distro-agnostic sysext needs `_any` plus self-contained binaries; otherwise build one per host distro (experiment E-08).

**Secure Boot.**

- **Ubuntu** signs prebuilt NVIDIA modules with Canonical's key; DKMS modules need MOK [D-47].
- **Fedora/RPM Fusion** uses a per-machine akmods key plus MOK [D-39].
- **Own image:** with an own image, AI Linux could build NVIDIA open modules [D-60] in CI and sign them with a project key enrolled once (opportunity O-D-02). This must never default to disabling Secure Boot.

### 1.6 Hybrid strategies

| Strategy | Description | Pros | Cons / risks | Fit for small team |
|---|---|---|---|---|
| (a) Distro-agnostic layer first | AI Linux ships as a CLI, daemons, sysext/confext images, OCI containers and BPF schedulers on top of existing distros | Smallest maintenance burden. Reaches the existing user base. Uses distro GPU stacks and signing (Ubuntu, Fedora) | Limited control over kernel, driver versions and Secure Boot. sysext `ID` matching [D-76]. Support matrix grows with each host | High |
| (b) Host distro plus Nix or containers for AI stacks | Pin the AI stacks (PyTorch, llama.cpp, ROCm/CUDA userspace) with Nix or OCI images; the host supplies kernel and driver | Reproducible stacks that work across hosts. Nix CUDA cache exists [D-72][D-74] | The driver and userspace version coupling is hard [D-59]. Unfree redistribution in caches [LEGAL REVIEW] | Medium–high |
| (c) Own image | Derived image (Fedora bootc or Debian with mkosi, or NixOS), with kernel, drivers and tuning built in CI | Full control: custom kernel, signed modules, atomic rollback, v3 builds | Highest effort. Release engineering, CVE response and CRA steward duties [D-89] | Low initially |

Recommended sequencing (provisional): start with (a)+(b) on Ubuntu 26.04, Fedora 45, Debian 13 and Arch/CachyOS as *test hosts*. Prototype (c) in CI only, with Fedora bootc and Debian/mkosi as the two candidates, until experiments E-01 to E-09 decide.

### 1.7 Weighted scoring rubric

Scores run from 1 (poor) to 5 (excellent) and are author judgment from the evidence above. Unknown values default to 3. Weighted total = Σ(weight × score) / 5, out of 100.

| ID | Criterion | Weight |
|---|---|---|
| C1 | Accelerator stack availability and policy (NVIDIA, CUDA, ROCm, oneAPI) | 15 |
| C2 | Kernel freshness and ease of shipping a custom kernel or sched_ext | 10 |
| C3 | Atomic update, rollback and image tooling | 12 |
| C4 | Maintenance effort for a small-team derivative | 15 |
| C5 | Security cadence and support lifetime | 10 |
| C6 | Secure Boot story for out-of-tree modules | 8 |
| C7 | Reproducibility and supply chain | 8 |
| C8 | Python/AI ecosystem and user familiarity | 8 |
| C9 | ISA-optimized builds (v3, v4, znver4) | 4 |
| C10 | Governance, community and openness to upstreaming | 5 |
| C11 | CI/build cost and fit with deferred validation | 5 |

| Distro | C1 | C2 | C3 | C4 | C5 | C6 | C7 | C8 | C9 | C10 | C11 | Total |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Ubuntu 26.04 | 5 | 3 | 2 | 4 | 5 | 5 | 3 | 5 | 3 | 2 | 4 | **77.0** |
| Debian 13/14 | 3 | 2 | 2 | 4 | 5 | 3 | 5 | 4 | 1 | 5 | 4 | **68.8** |
| Fedora 45 / bootc | 3 | 5 | 5 | 3 | 2 | 3 | 3 | 4 | 1 | 4 | 4 | **68.8** |
| NixOS 26.05 | 3 | 4 | 5 | 3 | 2 | 2 | 4 | 3 | 3 | 4 | 3 | **65.8** |
| CachyOS | 4 | 5 | 2 | 2 | 2 | 2 | 3 | 3 | 5 | 2 | 2 | **57.6** |
| Arch | 4 | 5 | 1 | 2 | 2 | 2 | 4 | 3 | 1 | 4 | 2 | **55.6** |
| Yocto 6.0 | 1 | 4 | 4 | 1 | 4 | 3 | 4 | 1 | 5 | 4 | 3 | **55.4** |
| Buildroot | 1 | 4 | 2 | 2 | 3 | 2 | 3 | 1 | 5 | 4 | 3 | **48.4** |

### 1.8 Provisional ranking and uncertainty

1. **Ubuntu 26.04: host baseline, about 77.** Strong on the accelerator stack and Secure Boot. Weak on governance and on atomic updates for a derivative.
2. **Debian, about 69, and Fedora bootc, about 69 (tie).** Debian wins on auditability and governance, Fedora on freshness and atomic updates.
3. **NixOS, about 66.** Best fit as a layer for strategy (b).
4. **CachyOS, Arch and Yocto, about 55–58.** Use CachyOS and Arch as kernel and ISA references and test hosts; use Yocto for a later appliance track.
5. **Buildroot, about 48.**

**Uncertainty** is roughly ±10 points per row. Several cells rest on unverified items; see Open questions.

**Sensitivity:** with immutability-first weights (C1 = 12, C3 = 20, C4 = 12, C5 = 8, others unchanged), the totals become:

- Fedora bootc: 72.4
- Ubuntu: 72.8
- NixOS: 69.4
- Debian: 65.8

So the choice between Ubuntu-as-host and Fedora-bootc-as-own-image is a *strategy* decision ((a) versus (c)), not a quality ranking.

### 1.9 Deciding experiments

| ID | Experiment | Decides | Pass/fail signal |
|---|---|---|---|
| E-01 | Run the same models and runtimes on Ubuntu amd64 vs amd64v3 vs CachyOS v3/v4/znver4. Check outputs bit-exactly or by perplexity | Whether ISA repos matter (H-D-01) | Speedup beyond run-to-run noise *and* unchanged outputs |
| E-02 | Compare stock vs CachyOS kernel vs sched_ext schedulers on CPU-offload inference plus desktop latency | Whether to ship our own kernel (H-D-03) | Measurable p50/p99 gains with no stalls or regressions |
| E-03 | Track 6 months of version lag (driver, CUDA, ROCm, PyTorch) per distro against upstream | C1 scores (H-D-05) | Median lag in days |
| E-04 | Build and sign NVIDIA open modules in CI for Fedora bootc and Debian/mkosi; run MOK, upgrade and rollback drills | C6; feasibility of (c) | Zero manual steps after the first enrollment |
| E-05 | Inject failed updates and power loss: bootc vs NixOS vs RAUC A/B | C3 | Automatic rollback; time to recovery |
| E-06 | Run a shadow derivative for 3 months: count manual interventions, CPU-hours and CVE turnaround | C4, C11 | Effort per month within team capacity |
| E-07 | Build our image twice on separate builders and compare with diffoscope | C7 | Bit-identical, or a documented diff list |
| E-08 | Deploy one AI-runtime sysext on 4 hosts (`ID=_any` vs per-distro builds) | Feasibility of (a) | Count of per-distro artifacts needed |
| E-09 | Generate an SBOM for each candidate image and do a license review of the NVIDIA, ROCm and firmware components | Redistribution posture | No redistribution conflicts [LEGAL REVIEW] |

## Part 2: Licensing and community

**Disclaimer:** this section is technical research, not legal advice. All **[LEGAL REVIEW]** items need qualified counsel.

### 2.1 License options for project code

| License | Patent grant | GPLv2-kernel compatible | Notes |
|---|---|---|---|
| Apache-2.0 | Explicit grant with patent-retaliation termination | **No** for GPLv2. The kernel's LICENSES text says "Do NOT use… not GPL2 compatible" | FSF also cites the patent-termination clause [D-78][D-79] |
| MIT | No explicit grant | Yes | Maximally permissive; NVIDIA uses it per file in its open modules [D-60] |
| Apache-2.0 OR MIT (dual) | The recipient picks | Yes, via MIT | Common in Rust; bootc uses this dual license [D-34] |
| GPL-2.0-only | Implicit only | Required for in-kernel code | Mandatory for kernel patches and modules linking GPL-only symbols |
| LGPL-2.1 | Implicit | Yes | Example: RAUC [D-92]; library-level copyleft |
| MPL-2.0 | Explicit | Via the "Secondary License" clause (text not re-verified) | File-level copyleft [LEGAL REVIEW] |

### 2.2 Kernel module and BPF constraints

- **BPF programs are licensed like kernel modules.** Programs loaded into the kernel face the same license checks as modules [D-57].
- **GPL-only helpers and implicit GPL dependencies.** Programs that use GPL-only helpers must be GPL-compatible. LSM and struct_ops programs, and programs that call kfuncs, are "implicitly calling GPL-only functions" [D-57].
- **The verifier enforces this for struct_ops.** It has rejected non-GPL struct_ops programs since 2021, with the change backported to 5.10 and 5.11 [D-58]. sched_ext schedulers are struct_ops programs, so every AI Linux scheduler must declare `GPL` or `Dual BSD/GPL` (or similar) in its license section.
- **User-space loaders can be permissive.** Proprietary or permissive user space and GPL BPF programs "can co-exist because they are separate executable processes" [D-57].
- **NVIDIA module and userspace.** The NVIDIA open module links as dual MIT/GPLv2 [D-60], but its userspace and GSP firmware stay proprietary and must match the module's version [D-59].

### 2.3 Documentation license

- **Recommended: CC-BY-4.0.** It is permissive and keeps docs reusable by distros and vendors.
- **Alternative: CC-BY-SA-4.0.** It forces derivatives to share but complicates mixing docs into permissively licensed code samples.
- **Code snippets inside docs:** license them as MIT or 0BSD so they can be copied freely.
- **Not verified this session:** the license texts and CC's GPLv3 compatibility declaration were not re-fetched [LEGAL REVIEW].

### 2.4 DCO vs CLA

- **DCO:** a per-commit `Signed-off-by` certification, created in 2004 for the kernel. It is a "one-way statement", not a contract [D-84].
- **Trend toward DCO:** OpenInfra moved from CLAs to DCO in July 2025 [D-85].
- **Critique:** some commentators question the DCO's legal effect [D-86].
- **Recommendation:** use DCO, enforced by a CI bot, and avoid a CLA. A CLA deters contributors and is unnecessary without relicensing plans [LEGAL REVIEW].

### 2.5 Governance

| Model | Pros | Cons | When |
|---|---|---|---|
| BDFL / founding maintainers | Fast decisions | Bus factor; perceived capture | Phase 0–1, with a published charter |
| Steering council (elected, about 5 seats) | Legitimacy; continuity | Process overhead | Once about 10 regular contributors exist |
| Foundation: LF AI & Data or CNCF | Neutral IP/trademark home, funding, legal support | Entry criteria and fees; slower | Later. LF AI & Data runs Sandbox → Incubation → Graduation, with incubation citing an OpenSSF Best Practices *silver* badge and a TAC vote (criteria vary by source) [D-87][D-88]. CNCF already hosts bootc [D-34] |

- **Code of Conduct:** adopt one in Phase 0 (for example the Contributor Covenant; not researched here).
- **Trademark:** register the project name early or hand it to a foundation [LEGAL REVIEW].

### 2.6 Security process and supply chain

- **OpenSSF Best Practices badge.** It has passing, silver and gold levels. Gold "is not achievable by small or single-organization projects". MUST criteria must be met, and unmet SHOULD criteria need a justification [D-80]. Target *passing* in Phase 1 and *silver* before any foundation application. OpenSSF Scorecard was not researched (search budget exhausted).
- **Coordinated vulnerability disclosure.** Publish `SECURITY.md`, a private reporting channel, an embargo policy and a CVE pathway.
- **CRA timing.** CRA reporting via ENISA's Single Reporting Platform has been mandatory for *manufacturers* since 2026-09-11. Open-source *steward* duties most likely start 2027-12-11 [D-89][D-90], though one source says September 2026 [D-91].
- **CRA scope.** Non-monetised FOSS is reportedly out of scope [D-90]. Whether AI Linux would be a "steward" needs **[LEGAL REVIEW]**.
- **SBOM.** Use SPDX 3.0.1; ISO/IEC 5962:2021 is still based on SPDX 2.2.1, with a 3.0 edition in ballot [D-82][D-97]. Alternatively use CycloneDX 1.7, which is ECMA-424 2nd edition (December 2025) [D-83]. Yocto 6.0 and Buildroot 2026.02 emit SBOMs natively [D-50][D-52].
- **SLSA.** v1.2 (November 2025) adds a Source track; the Build track runs L0–L3 [D-81]. Target Build L2 early and L3 once a hardened build farm exists.
- **Sigstore.** Cosign signing and transparency logs are plausible for OCI images (bootc, sysext) but were not researched (search budget exhausted).

### 2.7 Redistribution constraints of third-party components

| Component | License posture (as found) | AI Linux default | Evidence |
|---|---|---|---|
| NVIDIA open kernel modules | MIT per file; dual MIT/GPLv2 when linked | May build, sign and ship them in images | [D-59][D-60] |
| NVIDIA userspace driver and GSP firmware | Proprietary; must match the module version. Distros redistribute in non-free/multiverse (inference) | Fetch from distro or NVIDIA repos; mirror only after legal review | [D-59][D-43] [LEGAL REVIEW] |
| CUDA Toolkit | Only portions "identified as distributable", in object code, inside an application with "material additional functionality". Developer tools are internal-use only. Notify NVIDIA of non-compliant distribution. Older Attachment A lists cudart and cuBLAS | Do not ship. Fetch on demand. Canonical needed a dedicated agreement | [D-61][D-62][D-13] |
| cuDNN / TensorRT | Same structure. Distributable portions "shall only be accessed by your application" | Do not ship; fetch on demand | [D-63][D-64] |
| linux-firmware | Per-file terms in WHENCE. The "Redistributable" label summarises custom licences. Some files may be shipped only with a kernel or for the vendor's own devices, and Debian excludes some | Ship as distros do; generate per-file SBOM entries | [D-68][D-69] |
| ROCm | Per component: mostly MIT, some Apache-2.0, NCSA or GPL. Some binary-only parts (for example the ROCprof trace decoder) | Redistributable with per-component notices; exclude the binary-only parts | [D-65] |
| Intel oneAPI (oneMKL and others) | Intel Simplified Software License: binary redistribution subject to conditions; third-party parts licensed separately | Fetch on demand pending review | [D-66][D-67] [LEGAL REVIEW] |
| Model weights (Llama, Gemma and others) | Llama: attach the license, show "Built with Llama", AUP incorporated. Gemma: use restrictions must flow down, Notice file required | **Never redistribute by default.** Fetch on demand behind a license-acceptance gate and record the license in the model manifest and SBOM | [D-70][D-71] |

### 2.8 Recommended licence set (provisional) [LEGAL REVIEW]

| Artifact | License |
|---|---|
| Userspace tools, daemons, libraries and image recipes | `Apache-2.0 OR MIT` |
| Kernel patches and modules | `GPL-2.0-only` |
| BPF programs (sched_ext, struct_ops, LSM) | `GPL-2.0-only`, or `GPL-2.0-only OR BSD-2-Clause` for reuse in other operating systems |
| Documentation | `CC-BY-4.0`, with `MIT` for code snippets |
| Config examples | `0BSD` or `MIT` |

Apply SPDX headers and REUSE compliance to every file; mkosi already uses REUSE [D-77]. Contributions use DCO.

## Opportunities

| ID | Level | Opportunity | Expected benefit | Feasibility | Risk | Effort | Evidence | Novelty |
|---|---|---|---|---|---|---|---|---|
| O-D-01 | platform | Distro-agnostic delivery as sysext/confext plus OCI on Ubuntu, Fedora, Debian and Arch | Reach without our own distro; low maintenance | High | `ID` matching; support matrix | M | [D-76] | Emerging |
| O-D-02 | kernel | CI-built NVIDIA open modules signed with a project key (MOK once) in bootc/mkosi images | Secure Boot kept on; no DKMS on clients | Medium | Key custody; version lock with userspace | L | [D-59][D-60][D-39] | Known in derivatives; uncommon for AI |
| O-D-03 | userspace | Per-ISA (v3, v4, znver4) builds of only the AI-hot libraries, chosen by E-01 data | Speedups without a full v3 port | Medium | Gains may be nil (H-D-01) | M | [D-17][D-18][D-03] | Emerging |
| O-D-04 | kernel | GPL-2.0 sched_ext AI scheduler shipped as an optional sysext; relies on kernel fallback safety | Latency and throughput for hybrid inference | Medium | Distro kernels may lack sched_ext | L | [D-56][D-58][D-19] | Novel combination |
| O-D-05 | platform | SBOM (SPDX 3/CycloneDX) plus SLSA provenance plus reproducible-image CI from day one | CRA readiness; trust | High | CI cost | M | [D-81][D-82][D-83][D-10] | Established |
| O-D-06 | application | Model fetcher with license gate and manifest (no weight redistribution) | Legal safety; transparency | High | License text drift | S | [D-70][D-71] | Uncommon |
| O-D-07 | platform | Vendor-specific bootc image variants (nvidia, rocm, intel) built nightly | Atomic rollback per GPU stack | Medium | Build-farm cost | L | [D-31][D-34] | Emerging |
| O-D-08 | runtime | Nix-pinned AI stacks reused across host distros (strategy b) | Reproducible stacks everywhere | Medium | Unfree cache terms | M | [D-72][D-73] | Emerging |
| O-D-09 | platform | Upstream-first contributions (Fedora v3 change, Debian sched_ext enablement, packaging) | Shared maintenance burden | Medium | Slow acceptance | M | [D-27][D-46] | Established |

## Hypotheses

- **H-D-01: x86-64-v3/v4 repo rebuilds yield little for AI workloads.**
  - *Rationale:* BLAS, llama.cpp and GPU kernels already dispatch on CPU features at runtime, and Phoronix gains were mixed [D-18].
  - *Falsify:* in E-01, a v3/v4 build shows a speedup beyond noise on more than 2 representative AI workloads with unchanged outputs.
  - *Risk:* we under-invest in a real gain.
- **H-D-02: a distro-agnostic layer (strategy a) reaches most target users with under half the maintenance effort of an own image.**
  - *Rationale:* GPU stacks and signing are already provided by Ubuntu and Fedora.
  - *Falsify:* E-06 plus E-08 show per-host artifacts and effort comparable to (c).
  - *Risk:* lack of kernel control blocks key optimizations.
- **H-D-03: the choice of kernel or scheduler matters much less than driver/runtime versions for GPU-bound inference, but matters for CPU-offload and hybrid inference.**
  - *Rationale:* GPU-bound paths are dominated by device time.
  - *Falsify:* E-02 shows large GPU-bound gains from the kernel or scheduler.
  - *Risk:* we over-engineer the kernel track.
- **H-D-04: a Fedora-bootc-derived image gives the best atomic-update and rollback story with the least custom code, but NVIDIA needs per-kernel signed kmods in CI.**
  - *Rationale:* the tooling is all OCI-native [D-31][D-34].
  - *Falsify:* E-04 or E-05 needs persistent manual steps or fails rollback drills.
  - *Risk:* the 13-month Fedora lifecycle churns the build farm.
- **H-D-05: Ubuntu 26.04 offers the lowest-friction NVIDIA/CUDA/ROCm setup but lags upstream by months.**
  - *Rationale:* CUDA 13.1.x in the archive vs 13.3 upstream; the ROCm "months out of date" report [D-14][D-16].
  - *Falsify:* E-03 shows a median lag under 30 days.
  - *Risk:* users bypass the archive and break Secure Boot [D-47].
- **H-D-06: NixOS maximizes reproducibility and rollback, but the Nix learning curve and the unfree CUDA cache raise the contributor barrier.**
  - *Falsify:* onboarding trials where newcomers ship changes as fast as on Debian.
  - *Risk:* we dismiss the most reproducible option.
- **H-D-07: Yocto and Buildroot are unsuitable for workstation AI with dGPUs, but suit edge appliances.**
  - *Rationale:* no packaged Python ecosystem or proprietary GPU stack.
  - *Falsify:* a working Yocto image with CUDA or ROCm and PyTorch at acceptable effort.
  - *Risk:* we miss the embedded market.
- **H-D-08: a Debian-based image is the most auditable base after the 2026 reproducibility gate.**
  - *Falsify:* E-07 shows non-reproducible core packages in our image.
  - *Risk:* we trade freshness for auditability.
- **H-D-09: `Apache-2.0 OR MIT` userspace plus GPL-2.0 kernel/BPF minimizes license friction for contributors and downstreams.**
  - *Falsify:* legal review or downstream feedback identifies blockers.
  - *Risk:* the dual license weakens the patent posture for MIT recipients [LEGAL REVIEW].

## Contradictory or weak evidence

- **Linux 6.18 LTS end of life:** December 2027 per The New Stack [D-54], December 2028 per ITsFOSS [D-55]. The kernel.org releases page was not reachable.
- **CRA steward reporting start:** 2027-12-11 per [D-89][D-90], September 2026 per TechTarget [D-91].
- **Apache-2.0 and GPLv2:** a Zephyr issue claims the ASF sees one-way GPLv2 compatibility [D-79]. The kernel LICENSES note and the FSF call the two incompatible [D-78]. Treat them as incompatible.
- **CachyOS default kernel scheduler:** the current README says EEVDF [D-19]; older wiki text says BORE. The vendor's 5–20% v3 uplift claim (grade D) [D-17] conflicts with Phoronix's mixed results [D-18].
- **Fedora "Optimized Binaries v2":** recorded as approved for F42 but reportedly never implemented [D-29] (search snippets only).
- **Ubuntu archive currency:** Canonical promises SRU updates for ROCm [D-15], while reporting calls it "months out of date" [D-16]. CUDA archive and upstream versions also differ by snapshot [D-14].
- **bootc maturity:** CNCF lists bootc as Sandbox [D-34]; The Register reportedly calls it an incubator project (not cited as a source).
- **Yocto 6.0 date:** the announcement is dated 2026-05-13, while Wikipedia says April 2026 [D-50].
- **LF AI & Data criteria:** stage criteria differ between the current page, a 2020 draft and project blogs [D-87][D-88].
- **Search-snippet evidence:** several A-graded sources (CUDA EULA, Fedora packages, CachyOS wiki) were read only through search snippets. Their content is effectively weaker than the grade suggests.

## Open questions

1. Do the stock Ubuntu 26.04, Debian 13/14 and Fedora 45 kernels enable `CONFIG_SCHED_CLASS_EXT`, and do they package `scx`? [D-46][D-49]
2. Does Debian plan any x86-64-v3 port or variant? (No evidence found.)
3. What are the current CUDA 13.x EULA Attachment A file list and NVIDIA driver redistribution terms? (docs.nvidia.com was unreachable.) [LEGAL REVIEW]
4. What are the NixOS reproducibility status, the Secure Boot (lanzaboote) maturity, and the 26.11 release status?
5. How do community bootc derivatives (for example Universal Blue/Bazzite) handle prebuilt signed NVIDIA kmods? (Not researched; potential prior art for O-D-02.)
6. Would AI Linux be a CRA "manufacturer", a "steward" or out of scope, and which obligations follow? [LEGAL REVIEW]
7. What do OpenSSF Scorecard, Sigstore/cosign, KIWI, debos and archiso offer? (Not researched.)
8. Do Ubuntu's trademark and derivative policies constrain an Ubuntu-based image? [LEGAL REVIEW]
9. What is the Mender client's license, and where is the open/closed boundary (delta updates are commercial)? [D-92]

## Access limitations

- **WebFetch DNS failures:**
  - `packages.fedoraproject.org` (Fedora ROCm package data was taken from search snippets).
  - `docs.nvidia.com` (CUDA, cuDNN and TensorRT license texts were taken from search snippets of the PDFs).
  - The caller reported that `docs.kernel.org` fails. Kernel documentation was read via the `github.com/torvalds/linux` mirror instead [D-56][D-57].
- **No further new hosts tried:** after two DNS failures, WebFetch was limited to `github.com`, which works. debian.org, rpmfusion.org, wiki.cachyos.org, slsa.dev, bestpractices.dev, freedesktop.org and other pages were used via search snippets only.
- **Search budget exhausted:** the WebSearch budget (shared per turn) ran out before covering Universal Blue, Scorecard, Sigstore, KIWI/debos/archiso, distro sched_ext configs, NixOS reproducibility, Arch CUDA/ROCm packaging and MPL/CC license texts. These are listed as open questions.

## Sources

All sources were accessed on 2026-11-26.

| ID | Title | URL | Date | Accessed | Grade | Notes |
|---|---|---|---|---|---|---|
| D-01 | Fedora 45 Released | https://phoronix.com/news/Fedora-45-Released | 2026-11-03 | 2026-11-26 | C | Release after two slips |
| D-02 | Ubuntu 26.04 LTS summary for LTS users | https://documentation.ubuntu.com/release-notes/26.04/summary-for-lts-users/ | 2026 | 2026-11-26 | A | Kernel 6.8 → 7.0; snippet |
| D-03 | Architecture variants in Ubuntu cloud images | https://ubuntu.com/cloud/public-cloud/docs/all-clouds-explanation/architecture-variants/ | n.d. | 2026-11-26 | A | Fetched |
| D-04 | Ubuntu amd64v3 guide | https://computingforgeeks.com/ubuntu-amd64v3/ | n.d. | 2026-11-26 | C | Opt-in archive variants |
| D-05 | NixOS 26.05 released | https://nixos.org/blog/announcements/2026/nixos-2605/ | 2026-05-30 | 2026-11-26 | A | Support to 2026-12-31; snippet |
| D-06 | NixOS 26.05 Released (Phoronix) | https://phoronix.com/news/nixos-26.05-released | 2026-05 | 2026-11-26 | C | Toolchain details via coverage |
| D-07 | Debian "trixie" release information | https://www.debian.org/releases/trixie/ | n.d. | 2026-11-26 | A | Security/LTS dates; snippet |
| D-08 | Updated Debian 13: 13.7 released | https://www.debian.org/News/2026/20260912 | 2026-09-12 | 2026-11-26 | A | Snippet |
| D-09 | About Debian 13 Trixie (ELTS) | https://www.freexian.com/lts/extended/docs/debian-13-support/ | n.d. | 2026-11-26 | B | Commercial ELTS to 2035 |
| D-10 | Bits from the release team (reproducibility) | https://lists.debian.org/debian-devel-announce/2026/05/msg00001.html | 2026-05 | 2026-11-26 | A | Migration gate since 2026-05-09 |
| D-11 | LWN on Debian reproducibility gate | https://lwn.net/Articles/1072314/ | 2026-05 | 2026-11-26 | B | Notes flip-flopping |
| D-12 | Canonical to distribute NVIDIA CUDA in Ubuntu | https://ubuntu.com/blog/canonical-announces-it-will-support-and-distribute-nvidia-cuda-in-ubuntu | 2025-09-15 | 2026-11-26 | A | Snippet |
| D-13 | [needs-packaging] cuda-13-1 | https://bugs.launchpad.net/ubuntu/+bug/2141744 | n.d. | 2026-11-26 | B | Redistribution agreement; multiverse |
| D-14 | Install CUDA on Ubuntu 26.04 | https://linuxcapable.com/how-to-install-cuda-on-ubuntu-linux/ | n.d. | 2026-11-26 | C | Version snapshot 13.1.x vs 13.3 |
| D-15 | AI with AMD ROCm on Ubuntu | https://canonical.com/blog/2026/06/03/amd-rocm-on-ubuntu | 2026-06-03 | 2026-11-26 | A | ROCm 7.1 → 7.2.x via SRU; Pro 15 years |
| D-16 | Ubuntu 26.04 "apt install rocm" months out of date | https://www.linux.org/threads/phoronix-ubuntu-26-04-allows-sudo-apt-install-rocm-but-its-months-out-of-date.65551/ | 2026 | 2026-11-26 | C | Repost of Phoronix |
| D-17 | CachyOS Optimized Repositories | https://wiki.cachyos.org/features/optimized_repos/ | n.d. | 2026-11-26 | B | Uplift figure graded D; snippets of localized pages |
| D-18 | CachyOS x86-64-v3/v4 benchmarks | https://www.phoronix.com/review/cachyos-x86-64-v3-v4 | 2024-02 | 2026-11-26 | C | Mixed results |
| D-19 | CachyOS/linux-cachyos README | https://github.com/CachyOS/linux-cachyos | n.d. | 2026-11-26 | B | Fetched; fork on 7.1 |
| D-20 | CachyOS/copr-linux-cachyos | https://github.com/CachyOS/copr-linux-cachyos | n.d. | 2026-11-26 | B | Fedora port; snippet |
| D-21 | Arch RFC: x86_64_v3 port (MR 2) | https://gitlab.archlinux.org/archlinux/rfcs/-/merge_requests/2 | 2020-03 | 2026-11-26 | A | RFC 0002 |
| D-22 | aur-general: v3 "stuck in limbo" | https://lists.archlinux.org/archives/list/aur-general@lists.archlinux.org/message/47N5H4KZZDV4FANRVDB3OELHDLJUC33R | 2024-09 | 2026-11-26 | B | |
| D-23 | LWN comment on Arch v3 | https://lwn.net/Articles/1046949/ | 2025-11 | 2026-11-26 | D | Reader comment |
| D-24 | NVIDIA 590 drops Pascal; open modules | https://archlinux.org/news/nvidia-590-driver-drops-pascal-support-main-packages-switch-to-open-kernel-modules/ | 2025-12 | 2026-11-26 | A | |
| D-25 | Reproducible builds/Status (ArchWiki) | https://wiki.archlinux.org/title/Reproducible_builds/Status | n.d. | 2026-11-26 | B | About 86% snapshot |
| D-26 | Arch reproducible Docker image | https://lists.reproducible-builds.org/pipermail/rb-general/2026-April/004087.html | 2026-04 | 2026-11-26 | B | |
| D-27 | F45 Change Proposal: Build x86-64-v3 Packages | https://discussion.fedoraproject.org/t/f45-change-proposal-build-x86-64-v3-packages-systemwide/187943 | 2026 | 2026-11-26 | A | |
| D-28 | Fedora x86-64-v3 pushed to F46 | https://www.phoronix.com/news/Fedora-x86-64-v3-Delayed-F46 | 2026-08-18 | 2026-11-26 | C | |
| D-29 | Changes/Optimized Binaries AMD64 v2 | https://www.fedoraproject.org/wiki/Changes/Optimized_Binaries_for_the_AMD64_Architecture_v2 | n.d. | 2026-11-26 | A | Implementation unclear |
| D-30 | Initiatives/Fedora bootc | https://fedoraproject.org/wiki/Initiatives/Fedora_bootc | n.d. | 2026-11-26 | A | Snippet |
| D-31 | Fedora bootc base-images | https://forge.fedoraproject.org/iot/base-images | n.d. | 2026-11-26 | B | Image tiers |
| D-32 | Changes/AtomicUnifiedPipeline | https://fedoraproject.org/wiki/Changes/AtomicUnifiedPipeline | n.d. | 2026-11-26 | A | Proposal status unverified |
| D-33 | Changes/BuildAtomicDesktopsWithImageBuilder | https://fedoraproject.org/wiki/Changes/BuildAtomicDesktopsWithImageBuilder | n.d. | 2026-11-26 | A | Proposal |
| D-34 | bootc (CNCF project page) | https://www.cncf.io/projects/bootc/ | 2025-01-21 | 2026-11-26 | A | Sandbox; Apache-2.0/MIT |
| D-35 | rocm-hip (Fedora Packages) | https://packages.fedoraproject.org/pkgs/rocclr/rocm-hip/ | n.d. | 2026-11-26 | A | Fetch failed; snippet only |
| D-36 | Fedora ai-ml-sig meeting 2026-08-27 | https://discussion.fedoraproject.org/t/fedora-ai-ml-sig-meeting-2026-08-27/200441 | 2026-08-27 | 2026-11-26 | B | PyTorch ROCm stalled |
| D-37 | SIGs/HC (Heterogeneous Computing) | https://fedoraproject.org/wiki/SIGs/HC | n.d. | 2026-11-26 | B | ROCm version policy |
| D-38 | RPM Fusion Howto/CUDA | https://rpmfusion.org/Howto/CUDA | n.d. | 2026-11-26 | B | Points to NVIDIA repo |
| D-39 | Fedora quick-docs: MOK enrollment | https://docs.fedoraproject.org/en-US/quick-docs/mok-enrollment/ | n.d. | 2026-11-26 | A | Plus akmods README.secureboot |
| D-40 | fedora-workstation #37 NVIDIA 3rd-party repo | https://pagure.io/fedora-workstation/issue/37 | n.d. | 2026-11-26 | B | Third-party repo policy |
| D-41 | negativo17 repositories | https://negativo17.org/repositories/ | n.d. | 2026-11-26 | B | |
| D-42 | Debian wiki NvidiaGraphicsDrivers | https://wiki.debian.org/NvidiaGraphicsDrivers | n.d. | 2026-11-26 | B | Open flavor for Turing and later |
| D-43 | nvidia-cuda-toolkit 12.4.1-2 EULA.txt | https://sources.debian.org/src/nvidia-cuda-toolkit/12.4.1-2/arm64/EULA.txt | n.d. | 2026-11-26 | A | Debian packages CUDA (non-free) |
| D-44 | Debian wiki ROCm | https://wiki.debian.org/ROCm | n.d. | 2026-11-26 | B | |
| D-45 | Debian tracker rocm-hipamd | https://tracker.debian.org/pkg/rocm-hipamd | 2026 | 2026-11-26 | A | 7.2.4 in experimental |
| D-46 | debian-kernel: enable sched_ext (#1102639) | https://lists.debian.org/debian-kernel/2025/04/msg00153.html | 2025-04 | 2026-11-26 | B | Wishlist; outcome unknown |
| D-47 | Ubuntu: NVIDIA driver packages | https://ubuntu.com/desktop/docs/en/latest/how-to/graphics/nvidia-driver-packages/ | n.d. | 2026-11-26 | A | Signed modules vs DKMS |
| D-48 | LP #2060852 550-open with Secure Boot | https://bugs.launchpad.net/bugs/2060852 | 2024 | 2026-11-26 | B | |
| D-49 | arighi sched-ext PPA | https://launchpad.net/~arighi/+archive/ubuntu/sched-ext | n.d. | 2026-11-26 | B | Experimental |
| D-50 | Yocto 6.0 (Wrynose) release notes | https://docs.yoctoproject.org/migration-guides/release-notes-6.0.html | 2026-05-13 | 2026-11-26 | A | LTS to April 2030 |
| D-51 | Buildroot LTS | https://buildroot.org/lts.html | n.d. | 2026-11-26 | A | Snippet |
| D-52 | Buildroot 2026.02 released | https://lwn.net/Articles/1061543/ | 2026-03 | 2026-11-26 | B | |
| D-53 | Linux 7.0 Released | https://phoronix.com/news/linux-7.0-released | 2026-04-12 | 2026-11-26 | C | Not LTS |
| D-54 | Linux 6.18: new LTS kernel | https://thenewstack.io/linux-6-18-all-about-the-new-long-term-support-linux-kernel/ | 2025-12 | 2026-11-26 | C | EOL December 2027 claim |
| D-55 | Linux Kernel 7.0 is Out | https://itsfoss.com/news/linux-kernel-7-0-release/ | 2026-04 | 2026-11-26 | C | 6.18 EOL December 2028 claim |
| D-56 | sched-ext.rst (kernel docs) | https://github.com/torvalds/linux/blob/master/Documentation/scheduler/sched-ext.rst | n.d. | 2026-11-26 | A | Fetched via GitHub |
| D-57 | bpf_licensing.rst (kernel docs) | https://github.com/torvalds/linux/blob/master/Documentation/bpf/bpf_licensing.rst | n.d. | 2026-11-26 | A | Fetched via GitHub |
| D-58 | bpf: enforce struct_ops programs be GPL-only | https://lore.kernel.org/bpf/20210326100314.121853-2-toke@redhat.com/T/ | 2021-03-26 | 2026-11-26 | A | Snippet |
| D-59 | NVIDIA open-gpu-kernel-modules README | https://github.com/NVIDIA/open-gpu-kernel-modules | n.d. | 2026-11-26 | B | Fetched; 620.73.04 |
| D-60 | NVIDIA open-gpu-kernel-modules COPYING | https://github.com/NVIDIA/open-gpu-kernel-modules/blob/main/COPYING | n.d. | 2026-11-26 | A | MIT files; dual MIT/GPLv2 module |
| D-61 | CUDA Toolkit EULA | https://docs.nvidia.com/cuda/eula/index.html | 2026-05-21 (13.3 PDF) | 2026-11-26 | A | Fetch failed; wording from snippets |
| D-62 | ScanCode: nvidia-cuda-supplement-2020 | https://scancode-licensedb.aboutcode.org/nvidia-cuda-supplement-2020.html | 2020 | 2026-11-26 | B | Old Attachment A list |
| D-63 | TensorRT License Agreement | https://docs.nvidia.com/deeplearning/tensorrt/sla/ | n.d. | 2026-11-26 | A | Snippet |
| D-64 | cuDNN SLA v8.7.0 | https://docs.nvidia.com/deeplearning/cudnn/archives/cudnn-870/pdf/cuDNN-SLA.pdf | 2024-04 | 2026-11-26 | A | Snippet |
| D-65 | ROCm licenses | https://rocm.docs.amd.com/en/latest/about/license.html | n.d. | 2026-11-26 | A | Snippet; versions vary |
| D-66 | Intel Simplified Software License (via NuGet) | https://packages.nuget.org/packages/intelmkl.redist.win-x64/2025.0.1.5/License | 2022-10 | 2026-11-26 | A | |
| D-67 | intel-mkl README.Debian | https://sources.debian.org/src/intel-mkl/2020.4.304-7/debian/README.Debian | 2020 | 2026-11-26 | B | Dated |
| D-68 | linux-firmware WHENCE | https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/tree/WHENCE | n.d. | 2026-11-26 | A | Snippets via mirrors |
| D-69 | WHENCE: summarise custom licences as "Redistributable" | https://lkml.iu.edu/hypermail/linux/kernel/1510.2/01867.html | 2015-10 | 2026-11-26 | B | |
| D-70 | Llama 4 Community License | https://github.com/meta-llama/llama-models/blob/main/models/llama4/LICENSE | 2025 | 2026-11-26 | A | Snippet |
| D-71 | Gemma Terms of Use | https://ai.google.dev/gemma/terms | 2026-04-01 | 2026-11-26 | A | Snippet |
| D-72 | NixOS wiki: CUDA | https://wiki.nixos.org/wiki/CUDA | n.d. | 2026-11-26 | B | cache.nixos-cuda.org |
| D-73 | Nix, Flox, NVIDIA: CUDA redistribution on Nix | https://discourse.nixos.org/t/nix-flox-nvidia-opening-up-cuda-redistribution-on-nix/69189 | 2025 | 2026-11-26 | B | |
| D-74 | Flox CUDA catalog and cache | https://flox.dev/docs/concepts/cuda-catalog-and-cache | n.d. | 2026-11-26 | B | Vendor docs |
| D-75 | nixos-generators (archived) | https://github.com/nix-community/nixos-generators | 2026-01-30 | 2026-11-26 | B | Fetched; upstreamed in 25.05 |
| D-76 | systemd-sysext(8), systemd 260 | https://www.freedesktop.org/software/systemd/man/260/systemd-sysext.html | n.d. | 2026-11-26 | A | Snippet |
| D-77 | systemd/mkosi | https://github.com/systemd/mkosi | n.d. | 2026-11-26 | B | Fetched |
| D-78 | Kernel LICENSES: Apache-2.0 "Do NOT use" (mirror) | https://tangled.org/tjh.dev/kernel/commit/8ea8814fcdcb32cc667d090455649893a362c658 | n.d. | 2026-11-26 | B | Mirror of kernel commit |
| D-79 | Zephyr issue 55861 (Apache-2.0/GPL) | https://github.com/zephyrproject-rtos/zephyr/issues/55861 | 2023 | 2026-11-26 | C | Contains disputed ASF claim |
| D-80 | OpenSSF Best Practices criteria | https://www.bestpractices.dev/criteria | n.d. | 2026-11-26 | A | Snippet |
| D-81 | Announcing SLSA v1.2 | https://slsa.dev/blog/2025/11/announce-slsa-v1.2 | 2025-11 | 2026-11-26 | A | Snippet |
| D-82 | SPDX 3.0 announcement (LF) | https://www.linuxfoundation.org/press/spdx-3-revolutionizes-software-management-in-systems-with-enhanced-functionality-and-streamlined-use-cases | 2024-04 | 2026-11-26 | A | |
| D-83 | ECMA-424 (CycloneDX) | https://ecma-international.org/publications-and-standards/standards/ecma-424/ | 2025-12 | 2026-11-26 | A | 2nd edition = CycloneDX 1.7 |
| D-84 | DCO (LF best practices) | https://bestpractices.linuxfoundation.org/ip/contribution-mechanisms-dco.html | n.d. | 2026-11-26 | B | |
| D-85 | OpenInfra: transition from CLA to DCO | https://openinfra.org/dco/ | 2025 | 2026-11-26 | B | |
| D-86 | DCO Not CLA | https://writing.kemitchell.com/2021/07/02/DCO-Not-CLA | 2021-07-02 | 2026-11-26 | C | Lawyer's blog |
| D-87 | lfai/proposing-projects | https://github.com/lfai/proposing-projects | n.d. | 2026-11-26 | B | Lifecycle criteria |
| D-88 | OpenLineage: incubation stage at LF AI | https://openlineage.io/blog/incubation-stage-lfai | n.d. | 2026-11-26 | C | Conflicting criteria |
| D-89 | CRA reporting obligations now apply | https://hlc.com/en/publications/eu-cyber-resilience-act-vulnerability-and-incident-reporting-obligations-now-apply | 2026-09 | 2026-11-26 | C | Law-firm note |
| D-90 | CRA reporting requirements now live | https://www.traverssmith.com/knowledge/knowledge-container/the-eu-cyber-resilience-acts-vulnerability-and-incident-reporting-requirements-are-now-live/ | 2026-09 | 2026-11-26 | C | Non-monetised FOSS out of scope |
| D-91 | EU CRA reporting: what CISOs need to know | https://www.techtarget.com/cybersecurity/feature/EU-Cyber-Resilience-Act-reporting-What-CISOs-need-to-know | 2026 | 2026-11-26 | C | Contradicting steward date |
| D-92 | Exploring open source dual A/B update solutions (FOSDEM) | https://fosdem.org/2025/events/attachments/fosdem-2025-6299-exploring-open-source-dual-a-b-update-solutions-for-embedded-linux/slides/236750/leon-anav_nHAgFc8.pdf | 2025-02 | 2026-11-26 | C | RAUC, SWUpdate, Mender |
| D-93 | Fedora 45 release: what's new | https://www.fosslinux.com/163518/fedora-45-release-whats-new-and-how-to-upgrade.htm | 2026-11 | 2026-11-26 | C | EOL date |
| D-94 | Fedora 45 Beta: Python 3.15, GCC 16.2 | https://linuxjournal.com/content/fedora-linux-45-beta-released-python-315-gcc-162-and-major-security-changes | 2026 | 2026-11-26 | C | Title-level evidence |
| D-95 | Fedora 45 Beta with Linux 7.2 | https://www.comss.ru/page.php?id=22023 | 2026 | 2026-11-26 | D | Title-level evidence |
| D-96 | Ubuntu 26.10 amd64v3 daily ISOs | https://www.phoronix.com/news/Ubuntu-26.10-amd64v3-Daily | 2026 | 2026-11-26 | C | |
| D-97 | ISO/IEC 5962 (SPDX) | https://www.iso.org/standard/93810.html | n.d. | 2026-11-26 | A | Next edition in ballot |
