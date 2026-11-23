{
  pkgs ? import <nixpkgs> { },
}:

let
  pythonEnv = pkgs.python313.withPackages (
    ps:
    let
      # Discord requires the DAVE E2EE protocol (voice close code 4017),
      # which needs discord.py >= 2.7.0 and davey < 0.2 (0.2.0 broke key
      # transitions for discord.py 2.7.1).
      davey = ps.buildPythonPackage {
        pname = "davey";
        version = "0.1.7";
        format = "wheel";
        src = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/58/9e/8004065a2ff2e76f9dedab628f723ed62a55db8cde80d3521db9d41bc33b/davey-0.1.7-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl";
          hash = "sha256-3bhi4upPEbRLioY6egC9dyvzvhjwOTLj0TvBltMw0DM=";
        };
        nativeBuildInputs = [ pkgs.autoPatchelfHook ];
        buildInputs = [ pkgs.stdenv.cc.cc.lib ];
        pythonImportsCheck = [ "davey" ];
      };
      discordpy = ps.discordpy.overridePythonAttrs (old: {
        version = "2.7.1";
        src = pkgs.fetchFromGitHub {
          owner = "Rapptz";
          repo = "discord.py";
          tag = "v2.7.1";
          hash = "sha256-WH1HgzJ2pVyxX0/6j8XsBQcEeR88bBrNrOtQOC8ZCWE=";
        };
        dependencies = (old.dependencies or [ ]) ++ [ davey ];
      });
    in
    [
      ps.aiohttp
      discordpy
      ps.pydantic
      ps.python-dotenv
    ]
  );
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
    NODELINK_IMAGE = "ghcr.io/performanc/nodelink@sha256:2335a9daf22a71503386956ff6b9850ca331e95d21a8fba803d23b8dcddbfc23";
  };

  shellHook = ''
    if [ -d .venv ]; then
      venv_python="$(readlink -f .venv/bin/python 2>/dev/null || true)"
      nix_python="$(readlink -f "$UV_PYTHON")"
      if [ "$venv_python" != "$nix_python" ]; then
        uv venv --clear --system-site-packages .venv
      fi
      source .venv/bin/activate
    fi
  '';
}
