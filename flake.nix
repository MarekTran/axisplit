{
  description = "axisplit - receipt OCR bill splitting";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
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
      });
}
