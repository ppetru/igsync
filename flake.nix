{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
  let
    system = "x86_64-linux";
    pkgs = nixpkgs.legacyPackages.${system};
    python = pkgs.python313.withPackages (pyPkgs: [
      pyPkgs.black
      pyPkgs.prometheus-client
      pyPkgs.python-dateutil
      pyPkgs.python-dotenv
      pyPkgs.python-slugify
      pyPkgs.requests
    ]);
  in {
    devShells.${system}.default = pkgs.mkShell {
      buildInputs = with pkgs; [
        python
        sqlite
      ];
    };
  };
}
