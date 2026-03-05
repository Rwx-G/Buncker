FROM debian:12-slim

# Install SSH server + Docker engine (from Debian repos)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        openssh-server \
        docker.io \
        ca-certificates \
        iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Configure SSH: root login with password
RUN mkdir -p /run/sshd && \
    echo 'root:buncker' | chpasswd && \
    sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config

# Configure Docker daemon: allow insecure registry on buncker-offline:5000
RUN mkdir -p /etc/docker && \
    echo '{"insecure-registries": ["buncker-offline:5000"]}' > /etc/docker/daemon.json

# Sample Dockerfile for testing docker build
COPY tests/integration/sample/Dockerfile.simple /workspace/Dockerfile

EXPOSE 22

# Drop default route to simulate air-gap, then start dockerd + sshd
ENTRYPOINT ["/bin/sh", "-c"]
CMD ["ip route del default 2>/dev/null; dockerd --host=unix:///var/run/docker.sock & exec /usr/sbin/sshd -D"]
