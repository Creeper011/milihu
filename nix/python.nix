{ pkgs }:
pkgs.python313.withPackages (
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
)
