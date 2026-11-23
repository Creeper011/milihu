{
  pkgs,
  pythonEnv,
  backends,
}:
let
  probe = command: {
    exec.command = command;
    initial_delay_seconds = 1;
    period_seconds = 1;
    timeout_seconds = 2;
    success_threshold = 1;
    failure_threshold = 30;
  };
  shutdown = {
    signal = 15;
    timeout_seconds = 5;
  };
in
(pkgs.formats.yaml { }).generate "milihu-processes.yaml" {
  version = "0.5";
  is_strict = true;
  disable_env_expansion = true;
  shell.shell_command = "${pkgs.bash}/bin/bash";
  shell.shell_argument = "-c";
  processes = {
    cobalt = {
      command = "exec ${backends.cobaltCommand}/bin/milihu-cobalt";
      inherit shutdown;
      availability = {
        restart = "on_failure";
        backoff_seconds = 1;
        max_restarts = 3;
      };
      readiness_probe = probe "${pkgs.curl}/bin/curl --fail --silent http://127.0.0.1:9000/ > /dev/null";
    };
    nodelink = {
      command = "exec ${backends.nodelinkCommand}/bin/milihu-nodelink";
      inherit shutdown;
      availability = {
        restart = "on_failure";
        backoff_seconds = 1;
        max_restarts = 3;
      };
      readiness_probe = probe ''${pkgs.curl}/bin/curl --fail --silent --header "Authorization: $NODELINK_PASSWORD" http://127.0.0.1:2333/version > /dev/null'';
    };
    bot = {
      command = "exec ${pythonEnv}/bin/python -m milihu.milihu";
      inherit shutdown;
      depends_on = {
        cobalt.condition = "process_healthy";
        nodelink.condition = "process_healthy";
      };
      availability = {
        exit_on_end = true;
        exit_on_skipped = true;
      };
    };
  };
}
