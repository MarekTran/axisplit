self:
{ config, lib, pkgs, ... }:
let
  cfg = config.services.axisplit;
  packages = self.packages.${pkgs.stdenv.hostPlatform.system};
  decidePort = 8082; # axisplit-decide's default
  hardening = {
    ProtectSystem = "strict";
    ProtectHome = true;
    PrivateTmp = true;
    NoNewPrivileges = true;
    ProtectKernelTunables = true;
    ProtectKernelModules = true;
    ProtectControlGroups = true;
    RestrictSUIDSGID = true;
    RestrictRealtime = true;
    LockPersonality = true;
    RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ];
    Restart = "on-failure";
  };
in
{
  options.services.axisplit = {
    enable = lib.mkEnableOption "axisplit, receipt-OCR bill splitting";
    host = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Address to listen on.";
    };
    port = lib.mkOption {
      type = lib.types.port;
      default = 8000;
      description = "Port to listen on.";
    };
    dataDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/axisplit";
      description = "Database and uploaded photos (AXISPLIT_DATA).";
    };
    environment = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      example = { DATABASE_URL = "postgresql+asyncpg://axisplit@/axisplit?host=/run/postgresql"; };
      description = "Extra environment for the app. Ends up in the Nix store: no secrets.";
    };
    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      example = "/run/secrets/axisplit.env";
      description = "Environment file for secrets, such as AXISPLIT_DECIDE_KEY, read when the service starts.";
    };
    openFirewall = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Open the port in the firewall.";
    };
    decide = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = ''
          Run the decision model that picks receipt totals, on the CPU next to the app (see "Self-hosting"
          in the README for its RAM and time). Without it totals come from the rules alone, or from a
          server of your own: set environment.AXISPLIT_DECIDE_URL.
        '';
      };
      threads = lib.mkOption {
        type = lib.types.nullOr lib.types.ints.positive;
        default = null;
        description = "CPU threads for the decision model. Null: one per CPU it may use (nproc).";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.axisplit = {
      isSystemUser = true;
      group = "axisplit";
    };
    users.groups.axisplit = { };
    systemd.tmpfiles.rules = [ "d ${cfg.dataDir} 0750 axisplit axisplit - -" ];
    networking.firewall.allowedTCPPorts = lib.mkIf cfg.openFirewall [ cfg.port ];

    systemd.services.axisplit = {
      description = "axisplit";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      environment = {
        AXISPLIT_DATA = cfg.dataDir;
        AXISPLIT_HOST = cfg.host;
        AXISPLIT_PORT = toString cfg.port;
        AXISPLIT_DECIDE_URL = if cfg.decide.enable then "http://127.0.0.1:${toString decidePort}/v1" else "none";
      } // cfg.environment;
      serviceConfig = hardening // {
        User = "axisplit";
        Group = "axisplit";
        ReadWritePaths = [ cfg.dataDir ];
        ExecStart = lib.getExe packages.serve;
      } // lib.optionalAttrs (cfg.environmentFile != null) {
        EnvironmentFile = cfg.environmentFile;
      };
    };

    # Its weights are in the Nix store: it reads nothing else and writes nothing.
    systemd.services.axisplit-decide = lib.mkIf cfg.decide.enable {
      description = "axisplit decision model";
      wantedBy = [ "multi-user.target" ];
      environment = {
        AXISPLIT_DECIDE_PORT = toString decidePort;
      } // lib.optionalAttrs (cfg.decide.threads != null) {
        AXISPLIT_DECIDE_THREADS = toString cfg.decide.threads;
      };
      serviceConfig = hardening // {
        DynamicUser = true;
        ExecStart = lib.getExe packages.decide-server;
        # Started once it answers. A model that hasn't loaded within a minute fails the start, and
        # it is tried again; meanwhile uploads take the rules' total.
        ExecStartPost = pkgs.writeShellScript "axisplit-decide-ready" ''
          for _ in $(seq 120); do ${packages.decide-server}/bin/axisplit-decide-health && exit 0; sleep 0.5; done
          exit 1
        '';
        RestartSec = 5;
      };
    };
  };
}
