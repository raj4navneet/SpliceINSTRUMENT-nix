{
  description = "Splice INSTRUMENT virtual instrument synthesizer and sampler for NixOS under Wine";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
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
        splice-instrument = pkgs.callPackage ./packages/default.nix { };
      in
      {
        packages = {
          default = splice-instrument;
          SpliceINSTRUMENT = splice-instrument;
          splice-instrument = splice-instrument;
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
        SpliceINSTRUMENT = final.callPackage ./packages/default.nix { };
        splice-instrument = final.callPackage ./packages/default.nix { };
      };
    };
}
