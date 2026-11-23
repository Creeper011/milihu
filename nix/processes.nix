{
  pkgs,
  pythonEnv,
  backends,
}:
let
  curl = "${pkgs.curl}/bin/curl --fail --silent --show-error --output /dev/null --max-time 2 --retry 30 --retry-connrefused --retry-delay 1";
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
    };
    nodelink = {
      command = "exec ${backends.nodelinkCommand}/bin/milihu-nodelink";
      inherit shutdown;
      availability = {
        restart = "on_failure";
        backoff_seconds = 1;
        max_restarts = 3;
      };
    };
    backends-ready = {
      command = ''
        ${curl} http://127.0.0.1:9000/ &&
        ${curl} --header "Authorization: $NODELINK_PASSWORD" http://127.0.0.1:2333/version
      '';
      depends_on = {
        cobalt.condition = "process_started";
        nodelink.condition = "process_started";
      };
      availability = {
        restart = "exit_on_failure";
        exit_on_skipped = true;
      };
    };
    bot = {
      command = "exec ${pythonEnv}/bin/python -m milihu.milihu";
      inherit shutdown;
      depends_on = {
        backends-ready.condition = "process_completed_successfully";
      };
      availability = {
        exit_on_end = true;
        exit_on_skipped = true;
      };
    };
  };
}
