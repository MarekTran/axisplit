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
        ocrServer = pkgs.writeShellScriptBin "axisplit-ocr" ''
          set -euo pipefail
          # Keep model downloads in a user-owned cache (the system HF cache may
          # have been populated by root). -hf also fetches the vision projector.
          export HF_HOME="''${AXISPLIT_MODEL_CACHE:-''${XDG_CACHE_HOME:-$HOME/.cache}/axisplit-models}"
          mkdir -p "$HF_HOME"
          exec ${llama}/bin/llama-server \
            -hf "''${AXISPLIT_OCR_HF:-Qwen/Qwen3-VL-4B-Instruct-GGUF:Q4_K_M}" \
            --host 127.0.0.1 --port "''${AXISPLIT_OCR_PORT:-8081}" \
            --ctx-size 8192 --parallel 1 --n-gpu-layers 99 \
            --image-min-tokens 1024 "$@"
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
            echo "  axisplit-ocr                                # local vision server on :8081"
            echo "  uv sync --all-extras                        # install deps into .venv"
            echo "  uv run uvicorn axisplit.main:app --reload   # http://localhost:8000"
            echo "  nix build .#postgres-init && ./result/bin/pg-dev   # local Postgres on :5433"
          '';
        };

        packages.ocr-server = ocrServer;

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
