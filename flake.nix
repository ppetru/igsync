# ABOUTME: Nix packaging for the IGSync Instagram-to-WordPress sync script.
# ABOUTME: Exposes the runtime package, app entrypoint, checks, and development shell.
{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
  let
    system = "x86_64-linux";
    pkgs = nixpkgs.legacyPackages.${system};
    runtimePython = pkgs.python313.withPackages (pyPkgs: [
      pyPkgs.prometheus-client
      pyPkgs.python-dateutil
      pyPkgs.python-dotenv
      pyPkgs.python-slugify
      pyPkgs.requests
    ]);
    devPython = pkgs.python313.withPackages (pyPkgs: [
      pyPkgs.black
      pyPkgs.prometheus-client
      pyPkgs.python-dateutil
      pyPkgs.python-dotenv
      pyPkgs.python-slugify
      pyPkgs.requests
    ]);
    igsync = pkgs.writeShellApplication {
      name = "igsync";
      text = ''
        exec ${runtimePython}/bin/python ${./igsync.py} "$@"
      '';
    };
  in {
    packages.${system}.default = igsync;

    apps.${system}.default = {
      type = "app";
      program = "${igsync}/bin/igsync";
    };

    checks.${system}.default = pkgs.runCommand "igsync-package-check" { } ''
      set -euo pipefail
      workdir=$(mktemp -d)
      cd "$workdir"
      INSTAGRAM_ACCESS_TOKEN=dummy \
        WORDPRESS_SITE_URL=https://example.com \
        WORDPRESS_USERNAME=dummy \
        WORDPRESS_APPLICATION_PASSWORD=dummy \
        CATEGORY_ID=1 \
        PROMETHEUS_PUSH_GATEWAY=http://127.0.0.1:1 \
        ${igsync}/bin/igsync --help >/dev/null
      IGSYNC_SOURCE=${./igsync.py} ${runtimePython}/bin/python -m unittest discover -s ${./tests}
      touch $out
    '';

    devShells.${system}.default = pkgs.mkShell {
      buildInputs = with pkgs; [
        devPython
        sqlite
      ];
    };
  };
}
