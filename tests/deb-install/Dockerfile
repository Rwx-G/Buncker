FROM debian:12-slim

# Install minimal dependencies for .deb packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-cryptography \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy .deb packages (pre-built by CI or make build-deb)
COPY dist/buncker_*_all.deb /tmp/buncker.deb
COPY dist/buncker-fetch_*_all.deb /tmp/buncker-fetch.deb

# Install packages
RUN dpkg -i /tmp/buncker.deb /tmp/buncker-fetch.deb

# Copy verification script
COPY tests/deb-install/verify.sh /verify.sh
RUN chmod +x /verify.sh

CMD ["/verify.sh"]
