.SECONDARY:
.PHONY: backup extension extension-install

HOST ?= remarkable.usb
BIN_DIR ?= /home/root/bin

build/%: cmd/%/*.go
	CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 go build -trimpath -ldflags="-s -w" -o $@ ./cmd/$*

deploy-%: build/%
	ssh $(HOST) mkdir -p $(BIN_DIR)
	scp -O build/$* $(HOST):$(BIN_DIR)/$*

run-%: deploy-%
	ssh $(HOST) $(BIN_DIR)/$*

SDK ?= $(HOME)/.local/opt/remarkable-sdk/3.28.0.172

qt-build-%:
	unset LD_LIBRARY_PATH; . $(SDK)/environment-setup-cortexa7hf-neon-remarkable-linux-gnueabi && cmake -S apps/$* -B build/apps/$* && cmake --build build/apps/$*

qt-deploy-%: qt-build-%
	ssh $(HOST) mkdir -p $(BIN_DIR)
	scp -O build/apps/$*/$* $(HOST):$(BIN_DIR)/$*

qt-run-%: qt-deploy-%
	ssh $(HOST) 'systemctl mask --runtime remarkable-fail.service; systemctl stop xochitl; QT_QUICK_BACKEND=epaper QT_QPA_EVDEV_TOUCHSCREEN_PARAMETERS=rotate=180:invertx $(BIN_DIR)/$* -platform epaper; systemctl unmask --runtime remarkable-fail.service; systemctl start xochitl'

backup:
	rsync -a $(HOST):/home/root/.local/share/remarkable/xochitl/ backup/xochitl/

EXTENSION := remarkable-pc@techthos.net

extension: qt-build-frame build/privtmp build/evgrab
	cp build/apps/frame/frame build/privtmp build/evgrab extension/$(EXTENSION)/
	gnome-extensions pack --force --extra-source=rmpc.py --extra-source=tabletMenu.js --extra-source=screencast.py --extra-source=frame --extra-source=privtmp --extra-source=evgrab --out-dir=build extension/$(EXTENSION)

# gnome-extensions install fails here with "Can't recursively copy directory",
# so the zip is unpacked into place directly and its schema compiled, which is all install does.
extension-install: extension
	rm -rf ~/.local/share/gnome-shell/extensions/$(EXTENSION)
	unzip -q build/$(EXTENSION).shell-extension.zip -d ~/.local/share/gnome-shell/extensions/$(EXTENSION)
	glib-compile-schemas ~/.local/share/gnome-shell/extensions/$(EXTENSION)/schemas
