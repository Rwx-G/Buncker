.PHONY: lint test build build-deb clean

VERSION := $(shell python3 -c "import buncker; print(buncker.__version__)" 2>/dev/null || echo "0.5.0")
DIST := dist

lint:
	ruff check . && ruff format --check .

test:
	pytest

build: build-deb

build-deb: $(DIST)/buncker_$(VERSION)_all.deb $(DIST)/buncker-fetch_$(VERSION)_all.deb
	@echo "Built .deb packages in $(DIST)/"

$(DIST)/buncker_$(VERSION)_all.deb:
	@mkdir -p $(DIST)
	$(call build_pkg,buncker,buncker,buncker)

$(DIST)/buncker-fetch_$(VERSION)_all.deb:
	@mkdir -p $(DIST)
	$(call build_pkg,buncker-fetch,buncker_fetch,buncker-fetch)

# build_pkg(pkg_name, python_pkg, deb_dir)
# Assembles a .deb package from source files and packaging metadata.
define build_pkg
	$(eval PKG := $(1))
	$(eval PYPKG := $(2))
	$(eval DEBDIR := $(3))
	$(eval STAGE := $(DIST)/.stage-$(PKG))
	@rm -rf $(STAGE)
	@# Debian control files
	@mkdir -p $(STAGE)/DEBIAN
	@sed 's/\r$$//' packaging/$(DEBDIR)/debian/control > $(STAGE)/DEBIAN/control
	@sed -i "s/^Version:.*/Version: $(VERSION)/" $(STAGE)/DEBIAN/control
	@if [ -f packaging/$(DEBDIR)/debian/conffiles ]; then \
		sed 's/\r$$//' packaging/$(DEBDIR)/debian/conffiles > $(STAGE)/DEBIAN/conffiles; \
	fi
	@if [ -f packaging/$(DEBDIR)/debian/postinst ]; then \
		sed 's/\r$$//' packaging/$(DEBDIR)/debian/postinst > $(STAGE)/DEBIAN/postinst; \
		chmod 0755 $(STAGE)/DEBIAN/postinst; \
	fi
	@# Entry point
	@mkdir -p $(STAGE)/usr/bin
	@sed 's/\r$$//' packaging/$(DEBDIR)/usr/bin/$(PKG) > $(STAGE)/usr/bin/$(PKG)
	@chmod 0755 $(STAGE)/usr/bin/$(PKG)
	@# Python package
	@mkdir -p $(STAGE)/usr/lib/$(PKG)/$(PYPKG)
	@cp $(PYPKG)/*.py $(STAGE)/usr/lib/$(PKG)/$(PYPKG)/
	@# Shared modules
	@mkdir -p $(STAGE)/usr/lib/$(PKG)/shared
	@cp shared/*.py $(STAGE)/usr/lib/$(PKG)/shared/
	@# Package-specific extras
	@if [ -d packaging/$(DEBDIR)/etc ]; then \
		cp -r packaging/$(DEBDIR)/etc $(STAGE)/; \
	fi
	@if [ -f packaging/$(DEBDIR)/debian/$(PKG).service ]; then \
		mkdir -p $(STAGE)/lib/systemd/system; \
		sed 's/\r$$//' packaging/$(DEBDIR)/debian/$(PKG).service > $(STAGE)/lib/systemd/system/$(PKG).service; \
	fi
	@# Build
	@dpkg-deb --build --root-owner-group $(STAGE) $(DIST)/$(PKG)_$(VERSION)_all.deb
	@rm -rf $(STAGE)
endef

clean:
	rm -rf $(DIST)
