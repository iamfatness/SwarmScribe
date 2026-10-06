# A machine with systemd as PID 1, for the test of the native Linux install
# (e2e/follower-systemd/run_e2e.py). It holds systemd, curl and uv, and nothing of SwarmScribe:
# the test installs the follower in it the way the README tells an operator to.
FROM debian:12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends systemd systemd-sysv ca-certificates curl procps \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/0.12.22/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_UNMANAGED_INSTALL=1 sh \
    && systemctl mask systemd-logind.service getty.target console-getty.service
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
