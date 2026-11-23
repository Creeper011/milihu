{ pkgs }:
let
  nodejs = pkgs.nodejs_24;
  sandbox =
    "${pkgs.bubblewrap}/bin/bwrap --die-with-parent --unshare-user --unshare-pid --unshare-ipc --unshare-uts"
    + " --ro-bind /nix/store /nix/store --proc /proc --dev /dev --tmpfs /tmp"
    + " --ro-bind /etc/resolv.conf /etc/resolv.conf --ro-bind /etc/hosts /etc/hosts"
    + " --clearenv --setenv HOME /tmp"
    + " --setenv SSL_CERT_FILE ${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
    + " --setenv NODE_EXTRA_CA_CERTS ${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
  cobalt = pkgs.buildNpmPackage {
    pname = "milihu-cobalt";
    version = "11.7.1-a636575";
    inherit nodejs;
    src = pkgs.fetchzip {
      url = "https://github.com/imputnet/cobalt/archive/a636575b09de1fc55d9b8cd98cac88f5f2f16b42.tar.gz";
      sha256 = "021dhbsqp6xr9nhypy1613qr764ifjg5p1sif3vgv8v4w396px8n";
    };
    sourceRoot = "source/api";
    patches = [ ./cobalt.patch ];
    postPatch = ''
      cp -r ../packages/version-info ./version-info
      cp ${./cobalt/package.json} package.json
      cp ${./cobalt/package-lock.json} package-lock.json
      substituteInPlace src/stream/ffmpeg.js \
        --replace-fail 'import ffmpeg from "ffmpeg-static";' \
        'const ffmpeg = "${pkgs.ffmpeg}/bin/ffmpeg";'
    '';
    npmDepsHash = "sha256-doFQt3DpNI8EklV6sXi5XVVOVM1nxRdxEDygCWe6GaM=";
    npmFlags = [ "--ignore-scripts" ];
    dontNpmBuild = true;
    buildPhase = ''
      runHook preBuild
      npm rebuild isolated-vm --ignore-scripts=false --build-from-source
      runHook postBuild
    '';
    installPhase = ''
      runHook preInstall
      mkdir -p $out/lib/cobalt
      cp -r src package.json node_modules version-info $out/lib/cobalt/
      # Release archives lack .git; expose the pinned build metadata directly.
      chmod -R u+w $out/lib/cobalt/version-info
      cat > $out/lib/cobalt/version-info/index.js <<'JS'
      export const getCommit = async () => "a636575b09de1fc55d9b8cd98cac88f5f2f16b42";
      export const getBranch = async () => "main";
      export const getRemote = async () => "imputnet/cobalt";
      export const getVersion = async () => "11.7.1";
      JS
      runHook postInstall
    '';
  };
  nodelink = pkgs.buildNpmPackage {
    pname = "milihu-nodelink";
    version = "3.10.0-a283f2f";
    inherit nodejs;
    src = pkgs.fetchzip {
      url = "https://github.com/PerformanC/NodeLink/archive/a283f2f4e2f16e856a0da315a946fad2bfb9989d.tar.gz";
      sha256 = "13b9340975q1a727bxn1c42gqxaj27gnjrkci0qpc5nyiksb24y4";
    };
    patches = [ ./nodelink.patch ];
    postPatch = ''
      cp ${./nodelink/package.json} package.json
      cp ${./nodelink/package-lock.json} package-lock.json
    '';
    npmDepsHash = "sha256-CcL2/uBwUzF1AmiR0EKvfy9ctMYEsH6S3D3kwOBFGUY=";
    npmFlags = [ "--ignore-scripts" ];
    makeCacheWritable = true;
    dontNpmBuild = true;
    nativeBuildInputs = [ pkgs.autoPatchelfHook ];
    buildInputs = [ pkgs.stdenv.cc.cc.lib ];
    installPhase = ''
      runHook preInstall
      mkdir -p $out/lib/nodelink
      cp -r dist package.json package-lock.json node_modules $out/lib/nodelink/
      runHook postInstall
    '';
  };
in
{
  inherit cobalt nodelink;
  cobaltCommand = pkgs.writeShellApplication {
    name = "milihu-cobalt";
    text = ''
      exec ${sandbox} \
        --chdir ${cobalt}/lib/cobalt \
        --setenv API_URL http://127.0.0.1:9000/ \
        --setenv API_LISTEN_ADDRESS 127.0.0.1 \
        --setenv API_PORT 9000 \
        --setenv CUSTOM_INNERTUBE_CLIENT VISIONOS \
        ${nodejs}/bin/node src/cobalt.js
    '';
  };
  nodelinkCommand = pkgs.writeShellApplication {
    name = "milihu-nodelink";
    text = ''
      exec ${sandbox} \
        --setenv NODELINK_SERVER_PASSWORD "''${NODELINK_PASSWORD:?Missing NODELINK_PASSWORD}" \
        --setenv NODELINK_SOURCES_SPOTIFY_CLIENTID "''${SPOTIFY_CLIENT_ID:-}" \
        --setenv NODELINK_SOURCES_SPOTIFY_CLIENTSECRET "''${SPOTIFY_CLIENT_SECRET:-}" \
        ${pkgs.writeShellScript "start-nodelink" ''
          set -euo pipefail
          export PATH=${pkgs.lib.makeBinPath [ pkgs.coreutils ]}
          mkdir -p /tmp/nodelink
          cd /tmp/nodelink
          ln -s ${nodelink}/lib/nodelink/{dist,node_modules,package.json,package-lock.json} .
          cp ${nodelink}/lib/nodelink/dist/config.default.js config.default.js
          mkdir -p plugins .nodelink .cache logs
          export NODE_ENV=production
          export GIT_COMMIT=a283f2f4e2f16e856a0da315a946fad2bfb9989d
          export GIT_BRANCH=dev
          export BUILD_TIME=1795382488927
          export NODELINK_SERVER_HOST=127.0.0.1
          export NODELINK_SERVER_PORT=2333
          export NODELINK_CLUSTER_ENABLED=false
          export NODELINK_CACHE_DISKENABLED=false
          export NODELINK_LOGGING_FILE_ENABLED=false
          export NODELINK_LOGGING_LEVEL=warn
          export NODELINK_SOURCEHEALTH_ACTIVEPROBES_ENABLED=false
          export NODELINK_API_ENABLETRACKSTREAMENDPOINT=true
          export NODELINK_API_ENABLELOADSTREAMENDPOINT=true
          export NODELINK_SOURCES_LOCAL_ENABLED=false
          export NODELINK_SOURCES_HTTP_ENABLED=false
          export NODELINK_SOURCES_FLOWERY_ENABLED=false
          export NODELINK_SOURCES_LAZYPYTTS_ENABLED=false
          export NODELINK_SOURCES_SPOTIFY_EXTERNALAUTHURL=
          export NODELINK_SERVER_AUTOUPDATE_ENABLED=false
          export NODELINK_SERVER_AUTOUPDATE_CHECKONBOOT=false
          export NODELINK_SERVER_AUTOUPDATE_CHECKINTERVAL=0
          exec ${nodejs}/bin/node --dns-result-order=ipv4first dist/src/index.js
        ''}
    '';
  };
}
