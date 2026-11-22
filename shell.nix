{
  pkgs ? import <nixpkgs> { },
}:

let
  pythonEnv = pkgs.python313.withPackages (ps: [
    ps.aiohttp
    ps.discordpy
    ps.pydantic
    ps.python-dotenv
  ]);
in
pkgs.mkShell {
  packages = with pkgs; [
    pythonEnv
    docker_29
    uv
    ruff
    basedpyright
  ];

  env = {
    UV_PYTHON = "${pythonEnv}/bin/python";
    UV_PYTHON_PREFERENCE = "only-system";
    COBALT_IMAGE = "ghcr.io/imputnet/cobalt@sha256:28540f81d2cffbd9d089c08438e39a7689a7cee7e9a5b0f60de93a22d19f5dbe";
  };

  shellHook = ''
    if [ -d .venv ]; then
      source .venv/bin/activate
    fi
  '';
}
