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
          ];

          # Let uv find libpg etc. if needed; keep a local pg data dir out of git.
          AXISPLIT_DEV = "1";

          shellHook = ''
            echo "axisplit dev shell — typical flow:"
            echo "  uv sync --all-extras                        # install deps into .venv"
            echo "  uv run uvicorn axisplit.main:app --reload   # http://localhost:8000"
            echo "  nix build .#postgres-init && ./result/bin/pg-dev   # local Postgres on :5433"
          '';
        };

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
