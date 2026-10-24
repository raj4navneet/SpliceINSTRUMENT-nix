{
  pkgs ? import <nixpkgs> { },
}:
pkgs.callPackage ./packages/default.nix { }
