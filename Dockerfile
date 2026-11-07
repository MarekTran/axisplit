# Nix builds the app and its decision model in a throwaway stage; each image holds only its own
# runtime closure. Two targets, one build: `decide` and `app` (the default).
# https://mitchellh.com/writing/nix-with-dockerfiles
FROM nixos/nix:2.34.9 AS builder
WORKDIR /src
COPY . .
RUN nix --extra-experimental-features "nix-command flakes" build -o app .#serve \
 && nix --extra-experimental-features "nix-command flakes" build -o decide .#decide-server \
 && mkdir /app-closure /decide-closure \
 && cp -R $(nix-store -qR app) /app-closure && cp -R $(nix-store -qR decide) /decide-closure \
 && mkdir -p /rootfs/tmp /rootfs/data && chmod 1777 /rootfs/tmp && chown 65534:65534 /rootfs/data

# The decision model on the CPU, weights included. Reached by the app only, on Compose's network.
FROM scratch AS decide
COPY --from=builder /decide-closure /nix/store
COPY --from=builder /src/decide /app
ENV AXISPLIT_DECIDE_HOST=0.0.0.0 AXISPLIT_DECIDE_PORT=8082
EXPOSE 8082
USER 65534:65534
HEALTHCHECK --interval=10s --start-period=60s CMD ["/app/bin/axisplit-decide-health"]
CMD ["/app/bin/axisplit-decide"]

FROM scratch AS app
COPY --from=builder /app-closure /nix/store
COPY --from=builder /src/app /app
# /tmp for uploads spooled to disk; /data owned by the user below, so a new volume is too.
COPY --from=builder /rootfs /
ENV AXISPLIT_DATA=/data AXISPLIT_HOST=0.0.0.0 AXISPLIT_PORT=8000
VOLUME /data
EXPOSE 8000
USER 65534:65534
CMD ["/app/bin/axisplit-serve"]
