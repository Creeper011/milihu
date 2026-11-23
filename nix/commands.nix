{
  pkgs,
  pythonEnv,
  processes,
}:
let
  backendCheck = (pkgs.formats.yaml { }).generate "milihu-backend-check.yaml" {
    processes.bot.command = "exec ${pythonEnv}/bin/python ${../tests/integration/check_backends.py}";
  };
  scripts = {
    milihu-run = ''
      export COBALT_URL=http://127.0.0.1:9000/
      export NODELINK_URL=http://127.0.0.1:2333/
      NODELINK_PASSWORD=$(python -c 'import secrets; print(secrets.token_urlsafe(24))')
      export NODELINK_PASSWORD
      env_args=(--disable-dotenv)
      if [[ -f static/.env ]]; then
        env_args=(--env static/.env)
      fi
      exec process-compose "''${env_args[@]}" --config ${processes} --no-server --ordered-shutdown --tui=false up "$@"
    '';
    milihu-lint = ''
      nixfmt --check shell.nix nix/*.nix
      ruff check src tests "$@"
      basedpyright --pythonpath "$UV_PYTHON"
    '';
    milihu-format = ''
      nixfmt shell.nix nix/*.nix
      ruff check --select I --fix src tests "$@"
      ruff format src tests "$@"
    '';
    milihu-test = ''exec python -m unittest discover -s tests "$@"'';
    milihu-test-backends = ''
      NODELINK_PASSWORD=$(python -c 'import secrets; print(secrets.token_urlsafe(24))')
      export NODELINK_PASSWORD
      exec ${pkgs.bubblewrap}/bin/bwrap --die-with-parent --unshare-user --unshare-net --bind / / --dev /dev process-compose --disable-dotenv --config ${processes} --config ${backendCheck} --no-server --ordered-shutdown --tui=false up "$@"
    '';
    milihu-check = ''
      ruff format --check src tests
      milihu-lint
      milihu-test
    '';
  };
in
pkgs.lib.mapAttrsToList (
  name: script:
  pkgs.writeShellApplication {
    inherit name;
    runtimeInputs = [
      pythonEnv
      pkgs.ruff
      pkgs.basedpyright
      pkgs.process-compose
      pkgs.nixfmt-rfc-style
    ];
    text = ''
      cd "''${MILIHU_ROOT:?Enter nix-shell first}"
      ${script}
    '';
  }
) scripts
