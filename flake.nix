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
        # Decision model for the receipt total: answers /v1/systemone questions with
        # probabilities in one pass. Its attention is non-causal, so a whole request must
        # fit in one micro-batch (-ub). Text only (--no-mmproj): it reads the OCR lines.
        # --cache-ram 0: the RAM prompt cache never hits, every receipt differs.
        decideServer = pkgs.writeShellScriptBin "axisplit-decide" ''
          set -euo pipefail
          export HF_HOME="''${AXISPLIT_MODEL_CACHE:-''${XDG_CACHE_HOME:-$HOME/.cache}/axisplit-models}"
          mkdir -p "$HF_HOME"
          exec ${llama}/bin/llama-server \
            -hf "''${AXISPLIT_DECIDE_HF:-ggml-org/vllm-d3-nano-GGUF:Q8_0}" --no-mmproj \
            --host 127.0.0.1 --port "''${AXISPLIT_DECIDE_PORT:-8082}" \
            --ctx-size 8192 -b 8192 -ub 8192 --parallel 1 --n-gpu-layers 99 \
            --cache-ram 0 "$@"
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
            decideServer
          ];

          # Let uv find libpg etc. if needed; keep a local pg data dir out of git.
          AXISPLIT_DEV = "1";

          shellHook = ''
            echo "axisplit dev shell — typical flow:"
            echo "  axisplit-decide                             # decision model for totals on :8082"
            echo "  uv sync --all-extras                        # install deps into .venv"
            echo "  uv run uvicorn axisplit.main:app --reload   # http://localhost:8000"
            echo "  nix build .#postgres-init && ./result/bin/pg-dev   # local Postgres on :5433"
          '';
        };

        packages.ocr-server = ocrServer;
        packages.decide-server = decideServer;

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

        packages.default = serve;
        apps.default = {
          type = "app";
          program = "${serve}/bin/axisplit-serve";
          meta.description = "axisplit on http://127.0.0.1:8000";
        };
      }) // {
      nixosModules.default = import ./nix/module.nix self;
    };
}
