# Preserve the vendor's exact binary bytes while installing a normal executable
# without inherited set-id bits or file capabilities. Our high-port service runs
# with all Linux capabilities dropped and no-new-privileges enabled.
FROM coturn/coturn@sha256:59af4221a2c45c1195a1a4a865542b3ea744be1dd8e228d11bde87c6631081cf
USER 0:0
RUN install -m 0755 /usr/bin/turnserver /usr/local/bin/mixel-turnserver \
    && cmp -s /usr/bin/turnserver /usr/local/bin/mixel-turnserver
USER 10003:10003
ENTRYPOINT ["/usr/local/bin/mixel-turnserver"]
CMD ["--version"]
