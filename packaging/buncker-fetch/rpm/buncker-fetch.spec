Name:           buncker-fetch
Version:        1.0.1
Release:        1%{?dist}
Summary:        Online fetch tool for Buncker air-gapped Docker sync
License:        Apache-2.0
URL:            https://github.com/Rwx-G/Buncker
BuildArch:      noarch

Requires:       python3 >= 3.11
Requires:       python3-cryptography

%description
Buncker-fetch is the online companion to Buncker. It fetches Docker
images from public registries, encrypts them with AES-256-GCM, and
packages them for secure USB transfer to air-gapped environments.

%install
rm -rf %{buildroot}

# Entry point
install -D -m 0755 %{_sourcedir}/packaging/buncker-fetch/usr/bin/buncker-fetch \
    %{buildroot}/usr/bin/buncker-fetch

# Python package
mkdir -p %{buildroot}/usr/lib/buncker-fetch/buncker_fetch
cp %{_sourcedir}/buncker_fetch/*.py \
    %{buildroot}/usr/lib/buncker-fetch/buncker_fetch/

# Shared modules
mkdir -p %{buildroot}/usr/lib/buncker-fetch/shared
cp %{_sourcedir}/shared/*.py %{buildroot}/usr/lib/buncker-fetch/shared/

%files
/usr/bin/buncker-fetch
/usr/lib/buncker-fetch/
