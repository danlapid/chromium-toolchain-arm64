#!/usr/bin/env python3

import sys
import subprocess
import argparse
import logging
from pathlib import Path
import re

# Setup basic logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def run_command(cmd, cwd=None, check=True, capture_output=False):
    """Run a command and return the result"""
    if not capture_output:
        logging.info(f"Running: {' '.join(cmd) if isinstance(cmd, list) else cmd}")

    result = subprocess.run(
        cmd,
        cwd=cwd,
        shell=isinstance(cmd, str),
        capture_output=capture_output,
        text=True,
        check=False,
    )

    if result.returncode != 0 and check:
        if not capture_output:
            logging.error(f"Command failed with return code {result.returncode}")
            if result.stdout:
                logging.error(f"STDOUT: {result.stdout}")
            if result.stderr:
                logging.error(f"STDERR: {result.stderr}")
        raise subprocess.CalledProcessError(result.returncode, cmd)

    return result


def get_chromium_and_llvm_info(chromium_version="main"):
    """Fetch Chromium source and extract LLVM revision"""
    script_dir = Path(__file__).parent.resolve()
    chromium_dir = script_dir.parent / "chromium"

    logging.info(f"Fetching Chromium source (version: {chromium_version})...")

    if chromium_dir.exists():
        logging.info("Chromium directory exists, updating...")
        run_command(["git", "fetch", "origin"], cwd=chromium_dir)
        run_command(["git", "checkout", chromium_version], cwd=chromium_dir)
        run_command(["git", "pull", "origin", chromium_version], cwd=chromium_dir)
    else:
        logging.info("Cloning Chromium repository...")
        run_command(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "https://chromium.googlesource.com/chromium/src.git",
                str(chromium_dir),
            ]
        )

    # Extract LLVM revision
    logging.info("Extracting LLVM revision from Chromium DEPS...")
    deps_file = chromium_dir / "DEPS"
    if not deps_file.exists():
        logging.error("DEPS file not found in Chromium directory")
        sys.exit(1)

    with open(deps_file, "r") as f:
        deps_content = f.read()

    # Extract LLVM revision from clang package name
    pattern = r"clang-llvmorg-(\d+)-init-(\d+)-([a-zA-Z0-9]{8,})-\d+"
    match = re.search(pattern, deps_content)

    if match:
        llvm_revision = match.group(3)
        logging.info(f"Found LLVM revision: {llvm_revision}")
    else:
        # Fallback pattern
        pattern = r"clang-llvmorg-\d+-init-\d+-([a-zA-Z0-9]{8,})-\d+"
        match = re.search(pattern, deps_content)
        if match:
            llvm_revision = match.group(1)
            logging.info(f"Found LLVM revision (fallback): {llvm_revision}")
        else:
            logging.error("Could not find LLVM revision in DEPS file")
            sys.exit(1)

    return chromium_dir, llvm_revision


def build_llvm(chromium_dir):
    """Build LLVM using Chromium's build.py script"""
    logging.info("Building LLVM using Chromium's build.py script...")

    scripts_dir = chromium_dir / "tools/clang/scripts"

    # Build command
    build_cmd = [
        sys.executable,
        "build.py",
        "--without-android",
        "--without-fuchsia",
        "--with-ccache",
        "--use-system-cmake",
        "--host-cc", "clang",
        "--host-cxx", "clang++",
        "--disable-asserts",
        "--with-ml-inliner-model", "",
    ]

    try:
        run_command(build_cmd, cwd=scripts_dir)
    except subprocess.CalledProcessError:
        logging.error("Chromium build.py script failed")
        sys.exit(1)


def verify_toolchain(chromium_dir):
    """Verify the built toolchain"""
    logging.info("Verifying built toolchain...")

    # Find the LLVM installation directory
    bootstrap_dir = chromium_dir / "third_party/llvm-bootstrap-install"
    llvm_dir = chromium_dir / "third_party/llvm-build/Release+Asserts"

    if bootstrap_dir.exists() and (bootstrap_dir / "bin").exists():
        logging.info("Found bootstrap toolchain installation")
        toolchain_dir = bootstrap_dir
    elif llvm_dir.exists() and (llvm_dir / "bin").exists():
        logging.info("Found LLVM build installation")
        toolchain_dir = llvm_dir
    else:
        logging.error("No LLVM installation found")
        sys.exit(1)

    logging.info("Toolchain verification completed successfully!")
    return toolchain_dir


def build_toolchain(chromium_version="main"):
    """Main build process"""
    logging.info("Starting Chromium LLVM toolchain build...")

    try:
        chromium_dir, _ = get_chromium_and_llvm_info(chromium_version)
        build_llvm(chromium_dir)
        toolchain_dir = verify_toolchain(chromium_dir)

        logging.info("Chromium LLVM toolchain build completed successfully!")
        logging.info(f"Toolchain available at: {toolchain_dir}")

    except KeyboardInterrupt:
        logging.info("Build cancelled by user")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Build failed: {str(e)}")
        sys.exit(1)


def package_toolchain(chromium_dir):
    """Package the built toolchain into a tar.xz archive - matches package.py logic exactly"""
    import tarfile
    import lzma
    import fnmatch
    import itertools
    import shutil
    import subprocess
    import platform
    
    logging.info("Packaging built toolchain...")
    
    # Import update.py to get package version info
    sys.path.insert(0, str(chromium_dir / "tools/clang/scripts"))
    try:
        from update import PACKAGE_VERSION, RELEASE_VERSION, STAMP_FILE, STAMP_FILENAME
        package_name = f"clang-{PACKAGE_VERSION}"
    except ImportError:
        logging.error("Could not import update.py - toolchain may not be built correctly")
        sys.exit(1)
    
    # Define paths exactly as package.py does
    LLVM_RELEASE_DIR = chromium_dir / "third_party/llvm-build/Release+Asserts"
    
    if not LLVM_RELEASE_DIR.exists():
        logging.error(f"LLVM release directory not found: {LLVM_RELEASE_DIR}")
        sys.exit(1)
    
    logging.info(f"Packaging toolchain from: {LLVM_RELEASE_DIR}")
    
    # Check stamp file matches expected
    stamp = open(STAMP_FILE).read().rstrip()
    if stamp != PACKAGE_VERSION:
        logging.error(f'Actual stamp ({stamp}) != expected stamp ({PACKAGE_VERSION})')
        sys.exit(1)
    
    # Clean up any existing package directory
    if Path(package_name).exists():
        shutil.rmtree(package_name)
    
    # Define wanted files exactly as package.py does
    exe_ext = '.exe' if sys.platform == 'win32' else ''
    want = set([
        STAMP_FILENAME,
        'bin/llvm-pdbutil' + exe_ext,
        'bin/llvm-symbolizer' + exe_ext,
        'bin/llvm-undname' + exe_ext,
        # Copy built-in headers (lib/clang/3.x.y/include).
        'lib/clang/$V/include/*',
        'lib/clang/$V/share/asan_*list.txt',
        'lib/clang/$V/share/cfi_*list.txt',
    ])
    
    if sys.platform == 'win32':
        want.update([
            'bin/clang-cl.exe',
            'bin/lld-link.exe',
            'bin/llvm-ml.exe',
        ])
    else:
        want.update([
            'bin/clang',
            # Add LLD.
            'bin/lld',
            # Add llvm-ar for LTO.
            'bin/llvm-ar',
            # llvm-ml for Windows cross builds.
            'bin/llvm-ml',
            # Add llvm-readobj (symlinked from llvm-readelf) for extracting SONAMEs.
            'bin/llvm-readobj',
        ])
        if sys.platform != 'darwin':
            # The Fuchsia runtimes are only built on non-Mac platforms.
            want.update([
                'lib/clang/$V/lib/aarch64-unknown-fuchsia/libclang_rt.builtins.a',
                'lib/clang/$V/lib/x86_64-unknown-fuchsia/libclang_rt.builtins.a',
                'lib/clang/$V/lib/x86_64-unknown-fuchsia/libclang_rt.profile.a',
                'lib/clang/$V/lib/x86_64-unknown-fuchsia/libclang_rt.asan.so',
                'lib/clang/$V/lib/x86_64-unknown-fuchsia/libclang_rt.asan-preinit.a',
                'lib/clang/$V/lib/x86_64-unknown-fuchsia/libclang_rt.asan_static.a',
            ])
    
    if sys.platform.startswith('linux'):
        want.update([
            # Add llvm-objcopy for partition extraction on Android.
            'bin/llvm-objcopy',
            # Add llvm-nm.
            'bin/llvm-nm',
            # AddressSanitizer C runtime (pure C won't link with *_cxx).
            'lib/clang/$V/lib/aarch64-unknown-linux-gnu/libclang_rt.asan.a',
            'lib/clang/$V/lib/aarch64-unknown-linux-gnu/libclang_rt.asan.a.syms',
            'lib/clang/$V/lib/i386-unknown-linux-gnu/libclang_rt.asan.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.asan.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.asan.a.syms',
            'lib/clang/$V/lib/aarch64-unknown-linux-gnu/libclang_rt.asan_static.a',
            'lib/clang/$V/lib/i386-unknown-linux-gnu/libclang_rt.asan_static.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.asan_static.a',
            # AddressSanitizer C++ runtime.
            'lib/clang/$V/lib/aarch64-unknown-linux-gnu/libclang_rt.asan_cxx.a',
            'lib/clang/$V/lib/aarch64-unknown-linux-gnu/libclang_rt.asan_cxx.a.syms',
            'lib/clang/$V/lib/i386-unknown-linux-gnu/libclang_rt.asan_cxx.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.asan_cxx.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.asan_cxx.a.syms',
            # AddressSanitizer Android runtime.
            'lib/clang/$V/lib/linux/libclang_rt.asan-aarch64-android.so',
            'lib/clang/$V/lib/linux/libclang_rt.asan-arm-android.so',
            'lib/clang/$V/lib/linux/libclang_rt.asan-i686-android.so',
            'lib/clang/$V/lib/linux/libclang_rt.asan-riscv64-android.so',
            'lib/clang/$V/lib/linux/libclang_rt.asan_static-aarch64-android.a',
            'lib/clang/$V/lib/linux/libclang_rt.asan_static-arm-android.a',
            'lib/clang/$V/lib/linux/libclang_rt.asan_static-i686-android.a',
            'lib/clang/$V/lib/linux/libclang_rt.asan_static-riscv64-android.a',
            # Builtins for Android.
            'lib/clang/$V/lib/linux/libclang_rt.builtins-aarch64-android.a',
            'lib/clang/$V/lib/linux/libclang_rt.builtins-arm-android.a',
            'lib/clang/$V/lib/linux/libclang_rt.builtins-i686-android.a',
            'lib/clang/$V/lib/linux/libclang_rt.builtins-x86_64-android.a',
            'lib/clang/$V/lib/linux/libclang_rt.builtins-riscv64-android.a',
            # Builtins for Linux.
            'lib/clang/$V/lib/aarch64-unknown-linux-gnu/libclang_rt.builtins.a',
            'lib/clang/$V/lib/armv7-unknown-linux-gnueabihf/libclang_rt.builtins.a',
            'lib/clang/$V/lib/i386-unknown-linux-gnu/libclang_rt.builtins.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.builtins.a',
            # crtstart/crtend for Linux.
            'lib/clang/$V/lib/aarch64-unknown-linux-gnu/clang_rt.crtbegin.o',
            'lib/clang/$V/lib/aarch64-unknown-linux-gnu/clang_rt.crtend.o',
            'lib/clang/$V/lib/armv7-unknown-linux-gnueabihf/clang_rt.crtbegin.o',
            'lib/clang/$V/lib/armv7-unknown-linux-gnueabihf/clang_rt.crtend.o',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/clang_rt.crtbegin.o',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/clang_rt.crtend.o',
            # HWASAN Android runtime.
            'lib/clang/$V/lib/linux/libclang_rt.hwasan-aarch64-android.so',
            'lib/clang/$V/lib/linux/libclang_rt.hwasan-preinit-aarch64-android.a',
            'lib/clang/$V/lib/linux/libclang_rt.hwasan-riscv64-android.so',
            'lib/clang/$V/lib/linux/libclang_rt.hwasan-preinit-riscv64-android.a',
            # MemorySanitizer C runtime (pure C won't link with *_cxx).
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.msan.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.msan.a.syms',
            # MemorySanitizer C++ runtime.
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.msan_cxx.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.msan_cxx.a.syms',
            # Profile runtime (used by profiler and code coverage).
            'lib/clang/$V/lib/aarch64-unknown-linux-gnu/libclang_rt.profile.a',
            'lib/clang/$V/lib/armv7-unknown-linux-gnueabihf/libclang_rt.profile.a',
            'lib/clang/$V/lib/i386-unknown-linux-gnu/libclang_rt.profile.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.profile.a',
            'lib/clang/$V/lib/linux/libclang_rt.profile-i686-android.a',
            'lib/clang/$V/lib/linux/libclang_rt.profile-x86_64-android.a',
            'lib/clang/$V/lib/linux/libclang_rt.profile-aarch64-android.a',
            'lib/clang/$V/lib/linux/libclang_rt.profile-arm-android.a',
            'lib/clang/$V/lib/linux/libclang_rt.profile-riscv64-android.a',
            # ThreadSanitizer C runtime (pure C won't link with *_cxx).
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.tsan.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.tsan.a.syms',
            # ThreadSanitizer C++ runtime.
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.tsan_cxx.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.tsan_cxx.a.syms',
            # UndefinedBehaviorSanitizer C runtime (pure C won't link with *_cxx).
            'lib/clang/$V/lib/aarch64-unknown-linux-gnu/libclang_rt.ubsan_standalone.a',
            'lib/clang/$V/lib/aarch64-unknown-linux-gnu/libclang_rt.ubsan_standalone.a.syms',
            'lib/clang/$V/lib/i386-unknown-linux-gnu/libclang_rt.ubsan_standalone.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.ubsan_standalone.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.ubsan_standalone.a.syms',
            # UndefinedBehaviorSanitizer C++ runtime.
            'lib/clang/$V/lib/aarch64-unknown-linux-gnu/libclang_rt.ubsan_standalone_cxx.a',
            'lib/clang/$V/lib/aarch64-unknown-linux-gnu/libclang_rt.ubsan_standalone_cxx.a.syms',
            'lib/clang/$V/lib/i386-unknown-linux-gnu/libclang_rt.ubsan_standalone_cxx.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.ubsan_standalone_cxx.a',
            'lib/clang/$V/lib/x86_64-unknown-linux-gnu/libclang_rt.ubsan_standalone_cxx.a.syms',
            # UndefinedBehaviorSanitizer Android runtime, needed for CFI.
            'lib/clang/$V/lib/linux/libclang_rt.ubsan_standalone-aarch64-android.so',
            'lib/clang/$V/lib/linux/libclang_rt.ubsan_standalone-arm-android.so',
            'lib/clang/$V/lib/linux/libclang_rt.ubsan_standalone-riscv64-android.so',
            # Ignorelist for MemorySanitizer (used on Linux only).
            'lib/clang/$V/share/msan_*list.txt',
        ])
    
    # Replace $V with RELEASE_VERSION
    want = set([w.replace('$V', RELEASE_VERSION) for w in want])
    
    # Check that all non-glob wanted files exist on disk
    found_all_wanted_files = True
    for w in want:
        if '*' in w: continue
        if (LLVM_RELEASE_DIR / w).exists(): continue
        logging.error(f'wanted file "{w}" but it did not exist')
        found_all_wanted_files = False
    
    if not found_all_wanted_files:
        logging.error("Not all required files found")
        sys.exit(1)
    
    # Copy files exactly as package.py does
    for root, dirs, files in LLVM_RELEASE_DIR.rglob('*'):
        if root.is_file():
            continue
        dirs_list = [d.name for d in root.iterdir() if d.is_dir()]
        dirs_list.sort()  # Walk dirs in sorted order
        rel_root = str(root.relative_to(LLVM_RELEASE_DIR))
        if rel_root == '.':
            rel_root = ''
        rel_files = [str(Path(rel_root) / f.name) if rel_root else f.name for f in root.iterdir() if f.is_file()]
        wanted_files = list(set(itertools.chain.from_iterable(
            fnmatch.filter(rel_files, p) for p in want)))
        if wanted_files:
            # Create directory in package
            package_dir = Path(package_name) / rel_root if rel_root else Path(package_name)
            package_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(wanted_files):
            src = LLVM_RELEASE_DIR / f
            dest = Path(package_name) / f
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            logging.info(f"Adding {f}")
            
            # Strip libraries exactly as package.py does
            if 'libclang_rt.builtins' in f and 'android' in f:
                # Keep the builtins' DWARF info for unwinding.
                pass
            elif (sys.platform.startswith('linux') and 
                  dest.suffix in ['.so', '.a']):
                try:
                    subprocess.call(['strip', '-g', str(dest)])
                except:
                    pass  # strip might not be available or file might not be strippable
    
    # Set up symlinks exactly as package.py does
    if sys.platform != 'win32':
        bin_dir = Path(package_name) / 'bin'
        try:
            (bin_dir / 'clang++').symlink_to('clang')
            (bin_dir / 'clang-cl').symlink_to('clang')
            if (bin_dir / 'lld').exists():
                (bin_dir / 'ld.lld').symlink_to('lld')
                (bin_dir / 'ld64.lld').symlink_to('lld')
                (bin_dir / 'lld-link').symlink_to('lld')
                (bin_dir / 'wasm-ld').symlink_to('lld')
            if (bin_dir / 'llvm-readobj').exists():
                (bin_dir / 'llvm-readelf').symlink_to('llvm-readobj')
        except FileExistsError:
            pass  # Symlinks might already exist
    
    if sys.platform.startswith('linux'):
        bin_dir = Path(package_name) / 'bin'
        try:
            if (bin_dir / 'llvm-objcopy').exists():
                (bin_dir / 'llvm-strip').symlink_to('llvm-objcopy')
                (bin_dir / 'llvm-install-name-tool').symlink_to('llvm-objcopy')
        except FileExistsError:
            pass
        
        # Make `--target=*-cros-linux-gnu` work
        for arch, abi in [('armv7', 'gnueabihf'), ('aarch64', 'gnu'), ('x86_64', 'gnu')]:
            old = f'{arch}-unknown-linux-{abi}'
            new = old.replace('unknown', 'cros').replace('armv7', 'armv7a')
            old_path = Path(package_name) / 'lib' / 'clang' / RELEASE_VERSION / 'lib' / old
            new_path = Path(package_name) / 'lib' / 'clang' / RELEASE_VERSION / 'lib' / new
            if old_path.exists() and not new_path.exists():
                try:
                    new_path.symlink_to(old)
                except FileExistsError:
                    pass
    
    # Create tar.xz archive exactly as package.py does
    archive_path = f"{package_name}.tar.xz"
    with tarfile.open(archive_path, 'w:xz', preset=9 | lzma.PRESET_EXTREME) as tar:
        for f in sorted(Path(package_name).rglob('*')):
            if f.is_file() or f.is_symlink():
                arcname = str(f.relative_to('.'))
                tar.add(f, arcname=arcname)
                logging.info(f"Archiving {arcname}")
    
    # Clean up package directory
    shutil.rmtree(package_name)
    
    logging.info(f"Created package: {archive_path}")
    return archive_path


def main():
    parser = argparse.ArgumentParser(description="Build Chromium LLVM toolchain")
    parser.add_argument(
        "--version",
        default="main",
        help="Chromium version/branch to use (default: main)",
    )
    parser.add_argument(
        "--get-llvm-revision",
        action="store_true",
        help="Just extract and print the LLVM revision, then exit",
    )
    parser.add_argument(
        "--package-only",
        action="store_true",
        help="Only package existing toolchain, don't build",
    )

    args = parser.parse_args()

    if args.get_llvm_revision:
        _, llvm_revision = get_chromium_and_llvm_info(args.version)
        print(llvm_revision)
    elif args.package_only:
        chromium_dir, _ = get_chromium_and_llvm_info(args.version)
        package_toolchain(chromium_dir)
    else:
        build_toolchain(args.version)


if __name__ == "__main__":
    main()
