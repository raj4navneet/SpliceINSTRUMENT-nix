{
  description = "Splice INSTRUMENT virtual instrument synthesizer and sampler for NixOS under Wine";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";

    # Independent Wine runtime with WinRT MR 10060 (Windows.UI.Composition) prebuilt binary
    wine-winrt-src = {
      url = "github:mrshmllow/affinity-nix/c1e1f894f90c0610021a06836dcd6b033bff034c";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      wine-winrt-src,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config = {
            allowUnfree = true;
          };
        };

        # Sovereign Wine-WinRT runtime package
        wine-winrt = wine-winrt-src.packages.${system}.wine;

        splice-instrument = pkgs.callPackage ./packages/default.nix {
          winePackage = wine-winrt;
        };
      in
      {
        packages = {
          default = splice-instrument;
          SpliceINSTRUMENT = splice-instrument;
          splice-instrument = splice-instrument;
          wine-winrt = wine-winrt;
        };

        apps = {
          default = flake-utils.lib.mkApp {
            drv = splice-instrument;
          };
        };
      }
    )
    // {
      overlays.default = final: prev: {
        wine-winrt = wine-winrt-src.packages.${final.stdenv.hostPlatform.system}.wine;
        SpliceINSTRUMENT = final.callPackage ./packages/default.nix {
          winePackage = wine-winrt-src.packages.${final.stdenv.hostPlatform.system}.wine;
        };
        splice-instrument = final.callPackage ./packages/default.nix {
          winePackage = wine-winrt-src.packages.${final.stdenv.hostPlatform.system}.wine;
        };
      };
    };
}
