{
  description = "axisplit - receipt OCR bill splitting";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, pyproject-nix, uv2nix, pyproject-build-systems, ... }:
    let
      # The app's Python environment, built from uv.lock (wheels, as uv installs them).
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
      overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
    in
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        inherit (nixpkgs) lib;
        pythonSet = (pkgs.callPackage pyproject-nix.build.packages { python = pkgs.python312; })
          .overrideScope (nixpkgs.lib.composeManyExtensions [ pyproject-build-systems.overlays.wheel overlay ]);
        venv = pythonSet.mkVirtualEnv "axisplit-env" workspace.deps.default;
        # RapidOCR's wheel ships the PP-OCRv6 models the app uses: read them there, no download.
        ocrModels = pkgs.linkFarm "axisplit-ocr-models" {
          rapidocr = "${venv}/${pythonSet.python.sitePackages}/rapidocr/models";
        };
        serve = pkgs.writeShellScriptBin "axisplit-serve" ''
          set -euo pipefail
          # The default data dir sits next to the source, read-only in the Nix store.
          export AXISPLIT_DATA="''${AXISPLIT_DATA:-''${XDG_DATA_HOME:-$HOME/.local/share}/axisplit}"
          export AXISPLIT_MODEL_CACHE="''${AXISPLIT_MODEL_CACHE:-${ocrModels}}"
          exec ${venv}/bin/uvicorn axisplit.main:app \
            --host "''${AXISPLIT_HOST:-127.0.0.1}" --port "''${AXISPLIT_PORT:-8000}" "$@"
        '';
        llama = pkgs.llama-cpp.override { vulkanSupport = pkgs.stdenv.hostPlatform.isLinux; };
        # The decision model that picks each receipt's total: vllm-d3-lite, pinned. On the unseen
        # receipts it gets nano's totals right in about half the CPU time and RAM (docs/benchmarks.md).
        # Q8_0 is the smallest quantization published.
        decideModel = pkgs.fetchurl {
          url = "https://huggingface.co/ggml-org/vllm-d3-lite-GGUF/resolve/d4d6ebde7717eb37014a1c7401127fb48f26da42/vllm-d3-lite-Q8_0.gguf";
          hash = "sha256-/+0EqWBwNNZBuVGB15q/Nr7DoD7WR5SbrYJ30OKYTd8=";
        };
        # It answers /v1/systemone questions with probabilities in one pass. On the CPU unless
        # AXISPLIT_DECIDE_DEVICE names a GPU, in llama.cpp's Vulkan build: the plain build links BLAS,
        # which made the CPU 1.5x slower here. Its attention is non-causal, so a whole question must
        # fit in one micro-batch (-ub); the longest of the 46 labelled receipts took 2176 tokens. Not
        # 8192: lite reserves address space for its graph in proportion, and a 4 GB machine refused
        # 8192's 6.3 GB. --cache-ram 0: the prompt cache never hits, every receipt differs.
        # Threads: the CPUs it may use (nproc follows cpusets, not a container's --cpus quota);
        # llama.cpp itself would count the host's cores.
        decideServer = pkgs.writeShellScriptBin "axisplit-decide" ''
          set -euo pipefail
          exec ${llama}/bin/llama-server -m ${decideModel} --device "''${AXISPLIT_DECIDE_DEVICE:-none}" \
            --host "''${AXISPLIT_DECIDE_HOST:-127.0.0.1}" --port "''${AXISPLIT_DECIDE_PORT:-8082}" \
            --threads "''${AXISPLIT_DECIDE_THREADS:-$(${pkgs.coreutils}/bin/nproc)}" \
            --ctx-size 4096 -b 4096 -ub 4096 --parallel 1 --cache-ram 0 "$@"
        '';
        # Succeeds once the model has loaded (llama-server answers 503 while loading). Bash builtins
        # only: the image has no curl.
        decideHealth = pkgs.writeShellScriptBin "axisplit-decide-health" ''
          { exec 3<>/dev/tcp/127.0.0.1/''${AXISPLIT_DECIDE_PORT:-8082}; } 2>/dev/null || exit 1
          printf 'GET /health HTTP/1.0\r\n\r\n' >&3
          read -rt 5 _ status _ <&3 2>/dev/null
          [ "$status" = 200 ]
        '';
        decide = pkgs.symlinkJoin {
          name = "axisplit-decide";
          paths = [ decideServer decideHealth ];
          meta.mainProgram = "axisplit-decide";
        };
        # `nix run`: the app and, unless AXISPLIT_DECIDE_URL names another server (or is "none", for
        # the rules alone), the decision model next to it. Either one stopping stops the other.
        launcher = pkgs.writeShellScriptBin "axisplit" ''
          set -uo pipefail
          if [ -z "''${AXISPLIT_DECIDE_URL:-}" ]; then
            export AXISPLIT_DECIDE_URL="http://127.0.0.1:''${AXISPLIT_DECIDE_PORT:-8082}/v1"
            ${lib.getExe decide} &
          fi
          ${lib.getExe serve} "$@" &
          trap 'kill $(jobs -p) 2>/dev/null; wait' EXIT
          trap 'exit 130' INT
          trap 'exit 143' TERM
          wait -n
        '';
        ocrServer = pkgs.writeShellScriptBin "axisplit-ocr" ''
          set -euo pipefail
          # Keep model downloads in a user-owned cache (the system HF cache may
          # have been populated by root). -hf also fetches the vision projector.
          # --cache-ram 0: llama.cpp's RAM prompt cache (8 GB by default) never hits for
          # receipts (every photo differs) and only grows; Qwen3-VL-4B reached 8.4 GB without it.
          export HF_HOME="''${AXISPLIT_MODEL_CACHE:-''${XDG_CACHE_HOME:-$HOME/.cache}/axisplit-models}"
          mkdir -p "$HF_HOME"
          exec ${llama}/bin/llama-server \
            -hf "''${AXISPLIT_OCR_HF:-Qwen/Qwen3-VL-4B-Instruct-GGUF:Q4_K_M}" \
            --host 127.0.0.1 --port "''${AXISPLIT_OCR_PORT:-8081}" \
            --ctx-size 8192 --parallel 1 --n-gpu-layers 99 \
            --image-min-tokens 1024 \
            --cache-ram 0 "$@"
        '';
      in
      {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            python312
            uv
            postgresql
            git
            curl
            jq
            ocrServer
          ];

          # Let uv find libpg etc. if needed; keep a local pg data dir out of git.
          AXISPLIT_DEV = "1";

          shellHook = ''
            echo "axisplit dev shell — typical flow:"
            echo "  nix run .#decide-server                     # decision model for totals on :8082"
            echo "  uv sync --all-extras                        # install deps into .venv"
            echo "  uv run uvicorn axisplit.main:app --reload   # http://localhost:8000"
            echo "  nix build .#postgres-init && ./result/bin/pg-dev   # local Postgres on :5433"
          '';
        };

        packages.ocr-server = ocrServer;
        packages.decide-server = decide;
        packages.serve = serve;

        packages.postgres-init = pkgs.writeShellScriptBin "pg-dev" ''
          set -euo pipefail
          PGDATA=''${AXISPLIT_PGDATA:-$PWD/.pgdata}
          if [ ! -d "$PGDATA" ]; then
            ${pkgs.postgresql}/bin/initdb -D "$PGDATA" -A trust
          fi
          ${pkgs.postgresql}/bin/pg_ctl -D "$PGDATA" -l "$PGDATA/log" -o "-k $PGDATA -p 5433" start
          ${pkgs.postgresql}/bin/createdb -h "$PGDATA" -p 5433 axisplit 2>/dev/null || true
          echo "postgres up: DATABASE_URL=postgresql+asyncpg://$USER@localhost:5433/axisplit"
          echo "stop with: ${pkgs.postgresql}/bin/pg_ctl -D \"$PGDATA\" stop"
        '';

        packages.default = launcher;
        apps.default = {
          type = "app";
          program = lib.getExe launcher;
          meta.description = "axisplit on http://127.0.0.1:8000, with its decision model";
        };
      }) // {
      nixosModules.default = import ./nix/module.nix self;
    };
}
