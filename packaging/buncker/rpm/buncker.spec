Name:           buncker
Version:        1.0.0
Release:        1%{?dist}
Summary:        Offline Docker registry for air-gapped environments
License:        Apache-2.0
URL:            https://github.com/Rwx-G/Buncker
BuildArch:      noarch

Requires:       python3 >= 3.11
Requires:       python3-cryptography
Requires:       python3-pyyaml

%description
Buncker is an offline OCI-compliant registry daemon that serves
Docker images in air-gapped networks. It uses AES-256-GCM encrypted
USB transfers with BIP-39 mnemonic pairing for secure image delivery.

%install
rm -rf %{buildroot}

# Entry point
install -D -m 0755 %{_sourcedir}/packaging/buncker/usr/bin/buncker \
    %{buildroot}/usr/bin/buncker

# Python package
mkdir -p %{buildroot}/usr/lib/buncker/buncker
cp %{_sourcedir}/buncker/*.py %{buildroot}/usr/lib/buncker/buncker/

# Shared modules
mkdir -p %{buildroot}/usr/lib/buncker/shared
cp %{_sourcedir}/shared/*.py %{buildroot}/usr/lib/buncker/shared/

# systemd service
install -D -m 0644 %{_sourcedir}/packaging/buncker/debian/buncker.service \
    %{buildroot}/usr/lib/systemd/system/buncker.service

# logrotate config
install -D -m 0644 %{_sourcedir}/packaging/buncker/logrotate \
    %{buildroot}/etc/logrotate.d/buncker

%post
# Create buncker system user and group if they do not exist
getent group buncker > /dev/null 2>&1 || groupadd -r buncker
getent passwd buncker > /dev/null 2>&1 || \
    useradd -r -g buncker -d /var/lib/buncker -s /sbin/nologin \
    -c "Buncker registry daemon" buncker

# Create required directories
install -d -o buncker -g buncker -m 0750 /var/lib/buncker
install -d -o buncker -g buncker -m 0750 /var/log/buncker
install -d -o root -g buncker -m 0750 /etc/buncker

%files
/usr/bin/buncker
/usr/lib/buncker/
/usr/lib/systemd/system/buncker.service
%config(noreplace) /etc/logrotate.d/buncker
