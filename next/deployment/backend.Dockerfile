# Only the two service binaries are built. This does not build a Linux desktop
# client. Pin the inspected official compiler and runtime image manifests.
FROM rust:1.99.0-bookworm@sha256:b36c246742b4d323472588f789601be34eb4af053255e2af53969078ee8dcec7 AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY crates/ ./crates/
COPY vendor/ ./vendor/
RUN cargo build --release --locked -p signaling_server --bins

FROM debian:bookworm-slim@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/release/signaling_server /usr/local/bin/mixel-signaling
COPY --from=build /src/target/release/identity_authority /usr/local/bin/mixel-identity-authority
ENV TOKIO_WORKER_THREADS=2
USER 10001:10001
STOPSIGNAL SIGINT
ENTRYPOINT ["/usr/local/bin/mixel-signaling"]
