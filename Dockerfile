# Nix builds the app in a throwaway stage; the image holds only its runtime closure.
# https://mitchellh.com/writing/nix-with-dockerfiles
FROM nixos/nix:2.34.9 AS builder
WORKDIR /src
COPY . .
RUN nix --extra-experimental-features "nix-command flakes" build .#default \
 && mkdir /closure && cp -R $(nix-store -qR result) /closure \
 && mkdir -p /rootfs/tmp /rootfs/data && chmod 1777 /rootfs/tmp && chown 65534:65534 /rootfs/data

FROM scratch
COPY --from=builder /closure /nix/store
COPY --from=builder /src/result /app
# /tmp for uploads spooled to disk; /data owned by the user below, so a new volume is too.
COPY --from=builder /rootfs /
ENV AXISPLIT_DATA=/data AXISPLIT_HOST=0.0.0.0 AXISPLIT_PORT=8000
VOLUME /data
EXPOSE 8000
USER 65534:65534
CMD ["/app/bin/axisplit-serve"]
