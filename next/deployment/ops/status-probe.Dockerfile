# Synthetic connection check of the Mixel Remote status monitor (examples/status_probe.rs).
# Built on mixel-vps from a tar of next/ (same inputs as backend.Dockerfile):
#   docker build -f deployment/ops/status-probe.Dockerfile -t mixel-remote-status-probe:<commit> .
FROM rust:1.99.0-bookworm@sha256:b36c246742b4d323472588f789601be34eb4af053255e2af53969078ee8dcec7 AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY crates/ ./crates/
COPY vendor/ ./vendor/
RUN cargo build --release --locked -p signaling_server --example status_probe

FROM debian:bookworm-slim@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/release/examples/status_probe /usr/local/bin/mixel-status-probe
USER 10004:10004
ENTRYPOINT ["/usr/local/bin/mixel-status-probe"]
