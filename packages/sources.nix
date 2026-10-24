{
  pkgs,
  lib,
  installerUrl ? null,
  installerHash ? null,
  ...
}:
let
  mkExtract =
    source:
    pkgs.runCommand "splice-extracted-sources"
      {
        nativeBuildInputs = [
          pkgs.innoextract
          pkgs._7zz
        ];
        meta.license = lib.licenses.unfree;
      }
      ''
        mkdir -p $out
        if innoextract -e ${source} -d $out/app 2>/dev/null; then
          echo "Extracted via innoextract successfully."
        else
          7zz x ${source} -o$out/app
        fi
      '';
in
{
  inherit mkExtract;

  defaultInstaller =
    if installerUrl != null && installerHash != null then
      mkExtract (pkgs.fetchurl {
        url = installerUrl;
        hash = installerHash;
      })
    else
      null;
}
