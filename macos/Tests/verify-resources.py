"""Build and relocate an app, then load its usage messages without the build tree."""

import argparse
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import tempfile


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--build-system", choices=["native", "swiftbuild"], default="native")
args = parser.parse_args()
macos = Path(__file__).resolve().parents[1]
canonical = macos.parent / "src/unlimited/usage_errors.json"
swift = shutil.which("swift")
compiler = shutil.which("swiftc")
assert swift and compiler, "Swift is required"

with tempfile.TemporaryDirectory(prefix="unlimited-portable-") as directory:
    root = Path(directory)
    package = root / "repo/macos"
    shutil.copytree(macos, package, ignore=shutil.ignore_patterns(".build", ".swiftpm"))
    source = root / "repo/src/unlimited/usage_errors.json"
    source.parent.mkdir(parents=True)
    shutil.copyfile(canonical, source)
    shim = root / "bin/swift"
    shim.parent.mkdir()
    shim.write_text(f"#!/bin/sh\nexec {shlex.quote(swift)} \"$@\" --build-system {args.build_system}\n")
    shim.chmod(0o755)
    subprocess.run(["make", "app"], cwd=package,
                   env=dict(os.environ, PATH=f"{shim.parent}:{os.environ['PATH']}"), check=True)
    built = package / ".build/Unlimited.app"
    subprocess.run(["codesign", "--verify", "--strict", str(built)], check=True)
    app = root / "relocated/Unlimited.app"
    shutil.copytree(built, app)
    assets = list((app / "Contents/Resources/Unlimited_UnlimitedKit.bundle").rglob("usage_errors.json"))
    assert len(assets) == 1, "Expected the packaged usage registry"
    asset = assets[0]
    assert not asset.is_symlink(), "Packaged messages must not depend on a source symlink"
    assert asset.read_bytes() == canonical.read_bytes(), "Packaged messages differ from the canonical registry"

    build = package / ".build"
    if args.build_system == "native":
        objects = list(build.glob("**/release/UnlimitedKit.build/*.o"))
        modules = next(build.glob("**/release/Modules/UnlimitedKit.swiftmodule")).parent
    else:
        objects = list(build.glob("**/Release/UnlimitedKit-t.build/Objects-normal/*/*.o"))
        modules = next(build.glob("**/Products/Release/UnlimitedKit.swiftmodule")).parent
    objects = sorted({obj.resolve() for obj in objects})
    assert objects, "UnlimitedKit object files were not found"
    probe = root / "Probe.swift"
    probe.write_text('''import Foundation
import UnlimitedKit
let data = Data("[{\\"schema\\":1,\\"vendor\\":\\"anthropic\\",\\"status\\":\\"unread\\",\\"why\\":\\"signed-out\\"}]".utf8)
let reading = try Reading.decode(data)[0]
print(reading.issue(now: Date())!.title)
''')
    executable = app / "Contents/MacOS/Unlimited"
    subprocess.run([compiler, "-I", str(modules), "-target", f"{platform.machine()}-apple-macosx14.0",
                    str(probe), *map(str, objects), "-o", str(executable)], check=True)
    # Remove fallback paths compiled into Bundle.module before exercising the relocated bundle.
    shutil.rmtree(package)
    result = subprocess.run([str(executable)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "Signed out", result.stdout + result.stderr
    print(f"PASS: {args.build_system} packaged messages load after removing the build tree")
