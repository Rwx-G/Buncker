FROM debian:12-slim

# Install runtime dependencies + SSH server
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-cryptography \
        ca-certificates \
        curl \
        openssh-server \
        iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Install buncker .deb (pre-built by make build-deb)
COPY dist/buncker_*_all.deb /tmp/buncker.deb
RUN dpkg -i /tmp/buncker.deb && rm /tmp/buncker.deb

# Configure SSH: root login with password
RUN mkdir -p /run/sshd && \
    echo 'root:buncker' | chpasswd && \
    sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config

EXPOSE 22

# Drop default route to simulate air-gap, then start sshd
ENTRYPOINT ["/bin/sh", "-c"]
CMD ["ip route del default 2>/dev/null; exec /usr/sbin/sshd -D"]
