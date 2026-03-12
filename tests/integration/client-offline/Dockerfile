FROM debian:12-slim

# Lightweight LAN client for concurrency, timeout, and large transfer tests.
# No Docker engine needed - only curl and python3 for HTTP requests.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        openssh-server \
        curl \
        jq \
        ca-certificates \
        iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Configure SSH: root login with password
RUN mkdir -p /run/sshd && \
    echo 'root:buncker' | chpasswd && \
    sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config

EXPOSE 22

# Drop default route to simulate air-gap, then start sshd
ENTRYPOINT ["/bin/sh", "-c"]
CMD ["ip route del default 2>/dev/null; exec /usr/sbin/sshd -D"]
