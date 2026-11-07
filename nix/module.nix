self:
{ config, lib, pkgs, ... }:
let
  cfg = config.services.axisplit;
  packages = self.packages.${pkgs.stdenv.hostPlatform.system};
  decidePort = 8082; # axisplit-decide's default
  # Both services run as one system user, so the decision model's downloads can live in dataDir.
  common = {
    User = "axisplit";
    Group = "axisplit";
    ProtectSystem = "strict";
    ReadWritePaths = [ cfg.dataDir ];
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
      description = "Database and uploaded photos (AXISPLIT_DATA); the decision model's downloads go to models/.";
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
      enable = lib.mkEnableOption "the decision model that picks receipt totals (llama.cpp; the first start downloads 2.6 GB). Without it totals come from the rules alone";
      device = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        example = "Vulkan1";
        description = "llama.cpp --device (llama-server --list-devices); \"none\" runs it on the CPU, about 2 s per receipt instead of 0.1 s. Null lets llama.cpp choose.";
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
      } // lib.optionalAttrs cfg.decide.enable {
        AXISPLIT_DECIDE_URL = "http://127.0.0.1:${toString decidePort}/v1";
      } // cfg.environment;
      serviceConfig = common // {
        ExecStart = lib.getExe packages.default;
      } // lib.optionalAttrs (cfg.environmentFile != null) {
        EnvironmentFile = cfg.environmentFile;
      };
    };

    systemd.services.axisplit-decide = lib.mkIf cfg.decide.enable {
      description = "axisplit decision model";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ];
      environment = {
        AXISPLIT_MODEL_CACHE = "${cfg.dataDir}/models";
        AXISPLIT_DECIDE_PORT = toString decidePort;
      };
      serviceConfig = common // {
        ExecStart = lib.escapeShellArgs ([ (lib.getExe packages.decide-server) ]
          ++ lib.optionals (cfg.decide.device != null) [ "--device" cfg.decide.device ]);
        SupplementaryGroups = [ "render" "video" ]; # the GPU, for Vulkan
      };
    };
  };
}
