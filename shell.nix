{
  pkgs ? import ./nix/nixpkgs.nix { },
}:
let
  pythonEnv = import ./nix/python.nix { inherit pkgs; };
  backends = import ./nix/backends.nix { inherit pkgs; };
  processes = import ./nix/processes.nix { inherit pkgs pythonEnv backends; };
  commands = import ./nix/commands.nix { inherit pkgs pythonEnv processes; };
in
pkgs.mkShell {
  packages = [
    pythonEnv
    pkgs.uv
    pkgs.ffmpeg
    pkgs.ruff
    pkgs.basedpyright
    pkgs.nixfmt-rfc-style
    backends.cobaltCommand
    backends.nodelinkCommand
  ]
  ++ commands;

  env = {
    MILIHU_ROOT = builtins.toString ./.;
    UV_PYTHON = "${pythonEnv}/bin/python";
    UV_PYTHON_PREFERENCE = "only-system";
  };

  shellHook = ''
    cd "$MILIHU_ROOT"
    venv_python=$(readlink -f .venv/bin/python 2>/dev/null || true)
    nix_python=$(readlink -f "$UV_PYTHON")
    if [ "$venv_python" != "$nix_python" ]; then
      uv venv --clear --system-site-packages --python "$UV_PYTHON" .venv
    fi
    printf '%s\n' '${pythonEnv}/${pythonEnv.sitePackages}' \
      > .venv/${pythonEnv.sitePackages}/nix-dependencies.pth
    printf '%s\n' "$MILIHU_ROOT/src" \
      > .venv/${pythonEnv.sitePackages}/milihu.pth
    export PYTHONPATH="$MILIHU_ROOT/src''${PYTHONPATH:+:$PYTHONPATH}"
    source .venv/bin/activate
  '';
}
