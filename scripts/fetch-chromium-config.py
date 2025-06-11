#!/usr/bin/env python3

import os
import sys
import subprocess
import argparse
import shutil
from pathlib import Path

def log(message):
    """Print log message with timestamp"""
    import datetime
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")

def run_command(cmd, cwd=None, check=True):
    """Run a command and return the result"""
    log(f"Running: {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    result = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), 
                          capture_output=True, text=True, check=check)
    if result.returncode != 0 and check:
        log(f"Command failed with return code {result.returncode}")
        log(f"STDOUT: {result.stdout}")
        log(f"STDERR: {result.stderr}")
        sys.exit(1)
    return result

def fetch_chromium_source(chromium_dir, version="main"):
    """Fetch Chromium source code"""
    log(f"Fetching Chromium source (version: {version})...")
    
    if chromium_dir.exists():
        log("Chromium directory exists, updating...")
        os.chdir(chromium_dir)
        run_command(["git", "fetch", "origin"])
        run_command(["git", "checkout", version])
        run_command(["git", "pull", "origin", version])
    else:
        log("Cloning Chromium repository...")
        chromium_dir.parent.mkdir(parents=True, exist_ok=True)
        
        # Use shallow clone for main branch, full clone for specific versions
        if version == "main":
            run_command([
                "git", "clone", "--depth", "1", 
                "https://chromium.googlesource.com/chromium/src.git",
                str(chromium_dir)
            ])
        else:
            run_command([
                "git", "clone",
                "https://chromium.googlesource.com/chromium/src.git",
                str(chromium_dir)
            ])
            os.chdir(chromium_dir)
            run_command(["git", "checkout", version])

def extract_llvm_info(chromium_dir):
    """Extract LLVM information from Chromium DEPS"""
    log("Extracting LLVM information from Chromium DEPS...")
    
    deps_file = chromium_dir / "DEPS"
    if not deps_file.exists():
        log("ERROR: DEPS file not found in Chromium directory")
        sys.exit(1)
    
    with open(deps_file, 'r') as f:
        deps_content = f.read()
    
    # Extract LLVM revision
    llvm_revision = None
    lines = deps_content.split('\n')
    
    for i, line in enumerate(lines):
        if "'llvm_revision'" in line:
            # Extract the revision hash
            start = line.find("'") + 1
            end = line.rfind("'")
            llvm_revision = line[start:end]
            break
    
    if not llvm_revision:
        log("ERROR: Could not find LLVM revision in DEPS file")
        sys.exit(1)
    
    log(f"Found LLVM revision: {llvm_revision}")
    return llvm_revision

def copy_build_scripts(chromium_dir, repo_root):
    """Copy Chromium's LLVM build scripts to our repository"""
    log("Copying Chromium build scripts...")
    
    # Source directories in Chromium
    clang_scripts_dir = chromium_dir / "tools" / "clang" / "scripts"
    
    if not clang_scripts_dir.exists():
        log("ERROR: Chromium clang scripts directory not found")
        sys.exit(1)
    
    # Destination directory in our repo
    scripts_dir = repo_root / "chromium-scripts"
    scripts_dir.mkdir(exist_ok=True)
    
    # Copy build scripts
    important_files = [
        "build.py",
        "update.py",
        "package.py"
    ]
    
    for filename in important_files:
        src_file = clang_scripts_dir / filename
        dst_file = scripts_dir / filename
        
        if src_file.exists():
            shutil.copy2(src_file, dst_file)
            log(f"Copied {filename}")
        else:
            log(f"WARNING: {filename} not found in Chromium scripts")
    
    # Copy patches directory if it exists
    patches_src = clang_scripts_dir / "patches"
    patches_dst = scripts_dir / "patches"
    
    if patches_src.exists():
        if patches_dst.exists():
            shutil.rmtree(patches_dst)
        shutil.copytree(patches_src, patches_dst)
        log("Copied patches directory")
    else:
        log("No patches directory found")
    
    return scripts_dir

def create_build_config(repo_root, llvm_revision):
    """Create a build configuration file"""
    log("Creating build configuration...")
    
    config_file = repo_root / "build_config.sh"
    
    config_content = f"""#!/bin/bash
# Auto-generated build configuration
# Generated on: {datetime.datetime.now().isoformat()}

# LLVM Configuration
export LLVM_REVISION="{llvm_revision}"
export LLVM_REPO_URL="https://github.com/llvm/llvm-project.git"

# Build Configuration
export LLVM_TARGETS="AArch64;ARM;X86"
export LLVM_ENABLE_PROJECTS="clang;clang-tools-extra;lld;compiler-rt"
export LLVM_ENABLE_RUNTIMES="libcxx;libcxxabi;libunwind"

# Optimization flags
export CMAKE_BUILD_TYPE="Release"
export LLVM_ENABLE_ASSERTIONS="OFF"
export LLVM_OPTIMIZED_TABLEGEN="ON"

echo "Build configuration loaded:"
echo "  LLVM Revision: $LLVM_REVISION"
echo "  Targets: $LLVM_TARGETS"
echo "  Projects: $LLVM_ENABLE_PROJECTS"
echo "  Runtimes: $LLVM_ENABLE_RUNTIMES"
"""
    
    with open(config_file, 'w') as f:
        f.write(config_content)
    
    # Make it executable
    config_file.chmod(0o755)
    log(f"Created build configuration: {config_file}")

def main():
    parser = argparse.ArgumentParser(description="Fetch Chromium LLVM configuration")
    parser.add_argument("--version", default="main", 
                       help="Chromium version/branch to fetch (default: main)")
    parser.add_argument("--chromium-dir", 
                       help="Directory to store Chromium source (default: ./chromium)")
    
    args = parser.parse_args()
    
    # Setup paths
    repo_root = Path(__file__).parent.parent.resolve()
    chromium_dir = Path(args.chromium_dir) if args.chromium_dir else repo_root / "chromium"
    
    log(f"Repository root: {repo_root}")
    log(f"Chromium directory: {chromium_dir}")
    
    try:
        # Fetch Chromium source
        fetch_chromium_source(chromium_dir, args.version)
        
        # Extract LLVM information
        llvm_revision = extract_llvm_info(chromium_dir)
        
        # Copy build scripts
        scripts_dir = copy_build_scripts(chromium_dir, repo_root)
        
        # Create build configuration
        import datetime
        create_build_config(repo_root, llvm_revision)
        
        log("Successfully fetched Chromium configuration!")
        log(f"LLVM revision: {llvm_revision}")
        log(f"Scripts copied to: {scripts_dir}")
        
    except KeyboardInterrupt:
        log("Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        log(f"ERROR: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()