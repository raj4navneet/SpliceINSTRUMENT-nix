{
  pkgs,
}:
rec {
  x11 =
    (pkgs.writeText "x11-regedit.reg" ''
      Windows Registry Editor Version 5.00

      [HKEY_CURRENT_USER\Software\Wine\X11 Driver]
      "Decorated"="Y"
      "Managed"="Y"
      "UseTakeFocus"="N"
    '').outPath;

  theme =
    (pkgs.writeText "theme-regedit.reg" ''
      Windows Registry Editor Version 5.00

      [HKEY_CURRENT_USER\Control Panel\Desktop]
      "ThemeActive"="1"

      [HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes]
      "CurrentTheme"="C:\\windows\\resources\\themes\\light\\light.theme"
    '').outPath;

  webview2 =
    (pkgs.writeText "webview2-regedit.reg" ''
      Windows Registry Editor Version 5.00

      [HKEY_CURRENT_USER\Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments]
      "*"="--no-sandbox --disable-gpu-sandbox --disable-gpu --disable-gpu-compositing --disable-direct-composition --disable-features=RendererCodeIntegrity"
    '').outPath;

  dxvk =
    (pkgs.writeText "dxvk-regedit.reg" ''
      Windows Registry Editor Version 5.00

      [HKEY_CURRENT_USER\Software\Wine\DllOverrides]
      "dxgi"="native"
      "d3d11"="native"
    '').outPath;

  combined = pkgs.linkFarm "registry-patches-combined" [
    {
      name = "01-x11.reg";
      path = x11;
    }
    {
      name = "02-theme.reg";
      path = theme;
    }
    {
      name = "03-webview2.reg";
      path = webview2;
    }
    {
      name = "04-dxvk.reg";
      path = dxvk;
    }
  ];
}
