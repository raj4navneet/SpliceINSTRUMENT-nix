# SpliceINSTRUMENT-nix

Pure declarative Nix packaging and runtime orchestrator for **Splice INSTRUMENT** on Linux under Wine, modeled directly after [`affinity-nix`](https://github.com/mrshmllow/affinity-nix).

## Architecture

This package contains **zero binary payloads** in its git repository. All registry patches, system wrappers, and runtime configurations are defined purely as Nix derivations:

- **Declarative Registry Patches (`packages/registry-patches.nix`)**: Pure Nix derivations using `pkgs.writeText` and `pkgs.linkFarm` for X11 focus (`UseTakeFocus=N`), GNOME Mutter integration, dark/light theme, and WebView2 Chromium sandbox policies.
- **External Dependencies (`packages/dependencies.nix`)**: Manages external installers (such as Microsoft Edge WebView2 runtime) via `pkgs.fetchurl` with cryptographic hashes.
- **Self-Extracting Sources (`packages/sources.nix`)**: Uses `innoextract` and `7zz` to unpack official Inno Setup installers cleanly.
- **Audio & Engine Baselines (`seed-config/`)**: DirectSound 48kHz setup XML and SQLite database patches resolving Spitfire Audio Error #4.
- **Runtime Runner (`runner/splice-instrument.sh`)**: Prevents race conditions with `flock`, sets DXVK composition swapchain flags, and dynamically applies declarative patches upon launch.

## Usage

### Run with Flakes
```bash
nix run github:raj4navneet/SpliceINSTRUMENT-nix
```

### Import into NixOS Flake (`/etc/nixos/flake.nix`)
```nix
inputs = {
  SpliceINSTRUMENT-nix.url = "github:raj4navneet/SpliceINSTRUMENT-nix";
};

# Inside nixpkgs.overlays:
nixpkgs.overlays = [
  inputs.SpliceINSTRUMENT-nix.overlays.default
];

# Inside system packages:
environment.systemPackages = [
  pkgs.SpliceINSTRUMENT
];
```
