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
    """Package the built toolchain by monkeypatching package.py to skip the build step"""
    logging.info("Packaging built toolchain using package.py with monkeypatch...")
    
    # Add scripts directory to path so we can import package
    scripts_dir = chromium_dir / "tools/clang/scripts"
    sys.path.insert(0, str(scripts_dir))
    
    try:
        # Import package module
        import package
        
        # Monkeypatch TeeCmd to skip the build command but allow everything else
        original_tee_cmd = package.TeeCmd
        
        def mock_tee_cmd(cmd, logfile, fail_hard=True):
            # Check if this is the build command we want to skip
            if len(cmd) >= 2 and cmd[1].endswith('build.py'):
                logging.info(f"Skipping build command: {' '.join(cmd)}")
                # Just write to the log that we skipped it
                logfile.write(f"Skipped build command: {' '.join(cmd)}\n")
                return  # Skip the actual build
            else:
                # For any other commands, run them normally
                return original_tee_cmd(cmd, logfile, fail_hard)
        
        # Apply the monkeypatch
        package.TeeCmd = mock_tee_cmd
        
        # Run package.py's main function
        logging.info("Running package.py main function...")
        result = package.main()
        
        # Restore original function
        package.TeeCmd = original_tee_cmd
        
        if result != 0:
            logging.error(f"package.py failed with exit code {result}")
            sys.exit(1)
        
        logging.info("Package.py completed successfully")
        
    finally:
        # Remove scripts directory from path
        if str(scripts_dir) in sys.path:
            sys.path.remove(str(scripts_dir))


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
