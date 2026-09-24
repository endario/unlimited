# Command Line Tools ship Swift Testing's macros outside the default plugin path.
PLUGINS := $(shell xcrun --find swift | xargs dirname)/../lib/swift/host/plugins/testing
APP := .build/Unlimited.app

test:
	swift test -Xswiftc -plugin-path -Xswiftc $(PLUGINS)

app:
	swift build -c release --product Unlimited
	rm -rf $(APP) && mkdir -p $(APP)/Contents/MacOS
	cp .build/release/Unlimited $(APP)/Contents/MacOS/
	cp Info.plist $(APP)/Contents/
	codesign --force --sign - $(APP)

.PHONY: test app
