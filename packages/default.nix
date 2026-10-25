{
  lib,
  stdenv,
  callPackage,
  makeWrapper,
  winePackage,
  sqlite,
  util-linux,
  ...
}:
let
  wine = winePackage;
  registryPatches = (callPackage ./registry-patches.nix { }).combined;
  dependencies = callPackage ./dependencies.nix { };
  sources = callPackage ./sources.nix { };
in
stdenv.mkDerivation rec {
  pname = "SpliceINSTRUMENT";
  version = "2.4.18";

  src = ./..;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p $out/bin $out/share/splice-instruments $out/share/applications $out/share/icons/hicolor

    # Install seed configuration and runtime runner
    cp -r $src/seed-config $out/share/splice-instruments/
    cp $src/runner/splice-instrument.sh $out/share/splice-instruments/
    chmod +x $out/share/splice-instruments/splice-instrument.sh

    # Link declarative registry patches
    ln -s ${registryPatches} $out/share/splice-instruments/registry-patches

    # Link external dependencies (WebView2 runtime installer)
    ln -s ${dependencies} $out/share/splice-instruments/dependencies

    # Install desktop entry
    cp $src/splice-instrument.desktop $out/share/applications/

    # Install icons
    for size in 16x16 32x32 48x48 256x256; do
      mkdir -p $out/share/icons/hicolor/$size/apps
      if [ -f $src/icons/$size/splice-instrument.png ]; then
        cp $src/icons/$size/splice-instrument.png $out/share/icons/hicolor/$size/apps/
      fi
    done

    # Detect binary names (supports wine-winrt, affinity-wine, and standard wine)
    WINE_EXEC=""
    for candidate in "${wine}/bin/wine-winrt" "${wine}/bin/affinity-wine" "${wine}/bin/wine"; do
      if [ -x "$candidate" ] || [ -f "$candidate" ]; then
        WINE_EXEC="$candidate"
        break
      fi
    done

    WINESERVER_EXEC=""
    for candidate in "${wine}/bin/wine-winrt-wineserver" "${wine}/bin/affinity-wineserver" "${wine}/bin/wineserver"; do
      if [ -x "$candidate" ] || [ -f "$candidate" ]; then
        WINESERVER_EXEC="$candidate"
        break
      fi
    done

    WINEBOOT_EXEC=""
    for candidate in "${wine}/bin/wine-winrt-wineboot" "${wine}/bin/affinity-wineboot" "${wine}/bin/wineboot"; do
      if [ -x "$candidate" ] || [ -f "$candidate" ]; then
        WINEBOOT_EXEC="$candidate"
        break
      fi
    done

    # Create binary wrapper with paths baked in
    WRAPPER_ARGS=(
      $out/share/splice-instruments/splice-instrument.sh $out/bin/splice-instrument
      --prefix PATH : ${lib.makeBinPath [ wine sqlite util-linux ]}
      --set SPLICE_SHARE_DIR "$out/share/splice-instruments"
      --set WINE_BIN "$WINE_EXEC"
    )
    if [ -n "$WINESERVER_EXEC" ]; then
      WRAPPER_ARGS+=(--set WINESERVER_BIN "$WINESERVER_EXEC")
    fi
    if [ -n "$WINEBOOT_EXEC" ]; then
      WRAPPER_ARGS+=(--set WINEBOOT_BIN "$WINEBOOT_EXEC")
    fi

    makeWrapper "''${WRAPPER_ARGS[@]}"

    runHook postInstall
  '';

  meta = with lib; {
    description = "Splice Instruments virtual instrument plugin and standalone synthesizer under Wine";
    homepage = "https://splice.com/instruments";
    license = licenses.unfree;
    platforms = [ "x86_64-linux" ];
    mainProgram = "splice-instrument";
  };
}
