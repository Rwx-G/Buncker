FROM debian:12-slim

# Install runtime dependencies + SSH server
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-cryptography \
        ca-certificates \
        openssh-server \
    && rm -rf /var/lib/apt/lists/*

# Install buncker-fetch .deb (pre-built by make build-deb)
COPY dist/buncker-fetch_*_all.deb /tmp/buncker-fetch.deb
RUN dpkg -i /tmp/buncker-fetch.deb && rm /tmp/buncker-fetch.deb

# Configure SSH: root login with password
RUN mkdir -p /run/sshd && \
    echo 'root:buncker' | chpasswd && \
    sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config

EXPOSE 22

CMD ["/usr/sbin/sshd", "-D"]
