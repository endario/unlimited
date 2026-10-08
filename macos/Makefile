# Command Line Tools ship Swift Testing's macros outside the default plugin path.
PLUGINS := $(shell xcrun --find swift | xargs dirname)/../lib/swift/host/plugins/testing
APP := .build/Unlimited.app

test:
	python3 -m unittest discover -s Tests -p 'test_*.py'
	@report=$$(mktemp); output=$$(mktemp); trap 'rm -f "$$report" "$$output"' EXIT; set -o pipefail; \
		swift test -Xswiftc -plugin-path -Xswiftc $(PLUGINS) --xunit-output "$$report" 2>&1 | tee "$$output" && \
		python3 Tests/verify-host-tests.py "$$report" --output "$$output"

app:
	swift build -c release --product Unlimited
	rm -rf $(APP) && mkdir -p $(APP)/Contents/MacOS
	cp .build/release/Unlimited $(APP)/Contents/MacOS/
	cp Info.plist $(APP)/Contents/
	mkdir -p $(APP)/Contents/Resources
	cp -R .build/release/Unlimited_UnlimitedKit.bundle $(APP)/Contents/Resources/
	codesign --force --sign - $(APP)

.PHONY: test app
