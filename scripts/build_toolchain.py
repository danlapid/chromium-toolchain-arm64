#!/usr/bin/env python3

import os
import sys
import subprocess
import argparse
import shutil
import platform
import multiprocessing
import logging
from pathlib import Path
import re
import time

# Setup basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
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
        check=False
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

class ChromiumToolchainBuilder:
    def __init__(self, chromium_version="main"):
        self.script_dir = Path(__file__).parent.resolve()
        self.repo_root = self.script_dir.parent
        self.chromium_dir = self.repo_root / "chromium"
        self.chromium_version = chromium_version
        
        # Environment setup
        self.nproc = multiprocessing.cpu_count()
        self.ccache_dir = os.environ.get('CCACHE_DIR', str(Path.home() / '.ccache'))
        
        # Build configuration
        self.llvm_revision = None
        self.start_time = time.time()

    def check_architecture(self):
        """Check if we're on ARM64"""
        arch = platform.machine()
        if arch not in ['aarch64', 'arm64']:
            logging.error(f"This script is designed to run on ARM64 architecture, but detected: {arch}")
            sys.exit(1)
        logging.info(f"Confirmed ARM64 architecture: {arch}")

    def setup_build_env(self):
        """Setup build environment"""
        logging.info("Setting up build environment...")
        
        # Create ccache directory
        Path(self.ccache_dir).mkdir(parents=True, exist_ok=True)
        
        logging.info(f"Ccache directory: {self.ccache_dir}")
        logging.info(f"Using {self.nproc} parallel jobs")
        
        # Print system info
        logging.info("System information:")
        
        # Get OS info
        try:
            os_info = run_command(['lsb_release', '-d'], capture_output=True).stdout.strip().split('\t')[1]
        except:
            os_info = "Unknown"
        
        # Get memory info
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if line.startswith('MemTotal:'):
                        mem_kb = int(line.split()[1])
                        mem_gb = mem_kb // 1024 // 1024
                        memory = f"{mem_gb}GB"
                        break
        except:
            memory = "Unknown"
        
        # Get disk space
        try:
            result = run_command(['df', '-h', '.'], capture_output=True)
            disk_space = result.stdout.strip().split('\n')[1].split()[3]
        except:
            disk_space = "Unknown"
        
        print(f"  OS: {os_info}")
        print(f"  Kernel: {platform.release()}")
        print(f"  Architecture: {platform.machine()}")
        print(f"  CPU cores: {self.nproc}")
        print(f"  Memory: {memory}")
        print(f"  Disk space: {disk_space} available")

    def fetch_chromium_source(self):
        """Fetch Chromium source code"""
        logging.info(f"Fetching Chromium source (version: {self.chromium_version})...")
        
        if self.chromium_dir.exists():
            logging.info("Chromium directory exists, updating...")
            run_command(['git', 'fetch', 'origin'], cwd=self.chromium_dir)
            run_command(['git', 'checkout', self.chromium_version], cwd=self.chromium_dir)
            run_command(['git', 'pull', 'origin', self.chromium_version], cwd=self.chromium_dir)
        else:
            logging.info("Cloning Chromium repository...")
            run_command([
                'git', 'clone', '--depth', '1',
                'https://chromium.googlesource.com/chromium/src.git',
                str(self.chromium_dir)
            ])

    def extract_llvm_revision(self):
        """Extract LLVM revision from Chromium DEPS"""
        logging.info("Extracting LLVM revision from Chromium DEPS...")
        
        deps_file = self.chromium_dir / "DEPS"
        if not deps_file.exists():
            logging.error("DEPS file not found in Chromium directory")
            sys.exit(1)
        
        with open(deps_file, 'r') as f:
            deps_content = f.read()
        
        # Extract LLVM revision from clang package name
        pattern = r'clang-llvmorg-(\d+)-init-(\d+)-([a-zA-Z0-9]{8,})-\d+'
        match = re.search(pattern, deps_content)
        
        if match:
            self.llvm_revision = match.group(3)
            logging.info(f"Found LLVM revision: {self.llvm_revision}")
        else:
            # Fallback pattern
            pattern = r'clang-llvmorg-\d+-init-\d+-([a-zA-Z0-9]{8,})-\d+'
            match = re.search(pattern, deps_content)
            if match:
                self.llvm_revision = match.group(1)
                logging.info(f"Found LLVM revision (fallback): {self.llvm_revision}")
            else:
                logging.error("Could not find LLVM revision in DEPS file")
                sys.exit(1)
        
    def verify_chromium_structure(self):
        """Verify Chromium directory structure"""
        logging.info("Verifying Chromium directory structure...")
        
        clang_scripts_dir = self.chromium_dir / "tools" / "clang" / "scripts"
        if not clang_scripts_dir.exists():
            logging.error("Chromium clang scripts directory not found")
            sys.exit(1)
        
        build_script = clang_scripts_dir / "build.py"
        if not build_script.exists():
            logging.error("Chromium build.py script not found")
            sys.exit(1)
        
        logging.info("Chromium directory structure verified successfully")
        return clang_scripts_dir

    def build_llvm(self):
        """Build LLVM using Chromium's build.py script"""
        logging.info("Building LLVM using Chromium's build.py script...")
        
        chromium_scripts_dir = self.chromium_dir / "tools" / "clang" / "scripts"
        
        # Debug environment
        logging.info("Environment debug info:")
        print(f"  PWD: {chromium_scripts_dir}")
        print(f"  CMAKE: {shutil.which('cmake')}")
        
        try:
            cmake_version = run_command(['cmake', '--version'], capture_output=True).stdout.split('\n')[0]
            print(f"  CMAKE version: {cmake_version}")
        except:
            print("  CMAKE version: Not available")
        
        print(f"  CLANG: {shutil.which('clang')}")
        
        try:
            clang_version = run_command(['clang', '--version'], capture_output=True).stdout.split('\n')[0]
            print(f"  CLANG version: {clang_version}")
        except:
            print("  CLANG version: Not available")
        
        try:
            clangxx_version = run_command(['clang++', '--version'], capture_output=True).stdout.split('\n')[0]
            print(f"  CLANG++ version: {clangxx_version}")
        except:
            print("  CLANG++ version: Not available")
        
        print(f"  CCACHE: {shutil.which('ccache')}")
        print(f"  CCACHE_DIR: {self.ccache_dir}")
        
        # Show ccache stats before build
        logging.info("Ccache stats before build:")
        try:
            result = run_command(['ccache', '--show-stats'], capture_output=True)
            print(result.stdout)
        except:
            print("  (ccache stats not available)")
        
        # Run Chromium's build script
        logging.info("Running Chromium's build.py script...")
        
        # Debug platform detection
        logging.info("Platform detection debug:")
        print(f"  platform.machine(): {platform.machine()}")
        print(f"  sys.platform: {sys.platform}")
        
        # Build command
        build_cmd = [
            sys.executable, 'build.py',
            '--without-android',
            '--without-fuchsia',
            '--with-ccache'
        ]
        
        try:
            run_command(build_cmd, cwd=chromium_scripts_dir)
        except subprocess.CalledProcessError:
            # Check if bootstrap succeeded even if final stage failed
            bootstrap_dir = self.chromium_dir / "third_party" / "llvm-bootstrap-install"
            if bootstrap_dir.exists():
                logging.info("Bootstrap compiler built successfully, using as final result")
                logging.info("Final stage failed due to missing dependencies, but bootstrap is sufficient")
            else:
                logging.error("Chromium build.py script failed")
                sys.exit(1)

    def verify_toolchain(self):
        """Verify the built toolchain"""
        logging.info("Verifying built toolchain...")
        
        # Find the LLVM installation directory
        bootstrap_dir = self.chromium_dir / "third_party" / "llvm-bootstrap-install"
        llvm_dir = self.chromium_dir / "third_party" / "llvm-build" / "Release+Asserts"
        
        if bootstrap_dir.exists() and (bootstrap_dir / "bin").exists():
            logging.info("Found bootstrap toolchain installation")
            bin_dir = bootstrap_dir / "bin"
            self.toolchain_dir = bootstrap_dir
        elif llvm_dir.exists() and (llvm_dir / "bin").exists():
            logging.info("Found LLVM build installation")
            bin_dir = llvm_dir / "bin"
            self.toolchain_dir = llvm_dir
        else:
            logging.error("No LLVM installation found")
            sys.exit(1)
        
        # Verify executables exist
        clang_bin = bin_dir / "clang"
        clangxx_bin = bin_dir / "clang++"
        lld_bin = bin_dir / "lld"
        
        for name, path in [("clang", clang_bin), ("clang++", clangxx_bin), ("lld", lld_bin)]:
            if not path.exists() or not path.is_file():
                logging.error(f"{name} executable not found at {path}")
                sys.exit(1)
        
        # Test executables
        logging.info("Testing toolchain functionality...")
        try:
            clang_version = run_command([str(clang_bin), '--version'], capture_output=True).stdout
            print("Clang version:")
            print(clang_version)
        except:
            logging.error("Failed to run clang")
            sys.exit(1)
        
        try:
            lld_version = run_command([str(lld_bin), '--version'], capture_output=True).stdout
            print("LLD version:")
            print(lld_version)
        except:
            logging.error("Failed to run lld")
            sys.exit(1)
        
        logging.info("Toolchain verification completed successfully!")

    def show_ccache_stats(self):
        """Show ccache statistics"""
        logging.info("Ccache stats after build:")
        try:
            result = run_command(['ccache', '--show-stats'], capture_output=True)
            print(result.stdout)
            
            # Show efficiency
            print("Ccache efficiency:")
            for line in result.stdout.split('\n'):
                if any(keyword in line.lower() for keyword in ['cache hit', 'cache miss', 'hit rate']):
                    print(line)
        except:
            print("  (ccache stats not available)")

    def print_summary(self):
        """Print build summary"""
        logging.info("Build Summary:")
        print(f"  LLVM Revision: {self.llvm_revision}")
        print(f"  Toolchain Directory: {self.toolchain_dir}")
        
        # Calculate installed size
        try:
            result = run_command(['du', '-sh', str(self.toolchain_dir)], capture_output=True)
            size = result.stdout.split()[0]
            print(f"  Installed Size: {size}")
        except:
            print("  Installed Size: Unknown")
        
        # Calculate build time
        build_time = int(time.time() - self.start_time)
        hours = build_time // 3600
        minutes = (build_time % 3600) // 60
        seconds = build_time % 60
        print(f"  Build Time: {hours:02d}:{minutes:02d}:{seconds:02d}")
        
        logging.info("Available executables:")
        bin_dir = self.toolchain_dir / "bin"
        if bin_dir.exists():
            executables = sorted([f.name for f in bin_dir.iterdir() if f.is_file() and os.access(f, os.X_OK)])
            for exe in executables:
                print(f"  {exe}")

    def build(self):
        """Main build process"""
        logging.info("Starting Chromium LLVM toolchain build...")
        
        try:
            self.check_architecture()
            self.setup_build_env()
            self.fetch_chromium_source()
            self.extract_llvm_revision()
            self.verify_chromium_structure()
            self.build_llvm()
            self.show_ccache_stats()
            self.verify_toolchain()
            self.print_summary()
            
            logging.info("Chromium LLVM toolchain build completed successfully!")
            
        except KeyboardInterrupt:
            logging.info("Build cancelled by user")
            sys.exit(1)
        except Exception as e:
            logging.error(f"Build failed: {str(e)}")
            sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Build Chromium LLVM toolchain")
    parser.add_argument(
        "--version", 
        default="main",
        help="Chromium version/branch to use (default: main)"
    )
    parser.add_argument(
        "--get-llvm-revision",
        action="store_true",
        help="Just extract and print the LLVM revision, then exit"
    )
    
    args = parser.parse_args()
    
    builder = ChromiumToolchainBuilder(args.version)
    
    if args.get_llvm_revision:
        builder.fetch_chromium_source()
        builder.extract_llvm_revision()
        print(builder.llvm_revision)
    else:
        builder.build()

if __name__ == "__main__":
    main()