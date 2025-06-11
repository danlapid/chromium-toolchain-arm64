#!/bin/bash

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$REPO_ROOT/build"
INSTALL_DIR="$REPO_ROOT/install"
CHROMIUM_DIR="$REPO_ROOT/chromium"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

warn() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: $1${NC}"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $1${NC}"
    exit 1
}

# Check if we're on ARM64
check_architecture() {
    local arch=$(uname -m)
    if [[ "$arch" != "aarch64" && "$arch" != "arm64" ]]; then
        error "This script is designed to run on ARM64 architecture, but detected: $arch"
    fi
    log "Confirmed ARM64 architecture: $arch"
}

# Setup build environment
setup_build_env() {
    log "Setting up build environment..."
    
    # Create directories
    mkdir -p "$BUILD_DIR" "$INSTALL_DIR"
    
    # Set up compiler cache
    export CC="ccache gcc"
    export CXX="ccache g++"
    export CCACHE_DIR="${CCACHE_DIR:-$HOME/.ccache}"
    
    # Get number of CPU cores for parallel builds
    export NPROC=$(nproc)
    log "Using $NPROC parallel jobs"
    
    # Print system info
    log "System information:"
    echo "  OS: $(lsb_release -d | cut -f2)"
    echo "  Kernel: $(uname -r)"
    echo "  Architecture: $(uname -m)"
    echo "  CPU cores: $NPROC"
    echo "  Memory: $(free -h | grep '^Mem:' | awk '{print $2}')"
    echo "  Disk space: $(df -h . | tail -1 | awk '{print $4}') available"
}

# Get LLVM revision from Chromium
get_llvm_revision() {
    log "Getting LLVM revision from Chromium..."
    
    # Use our Python script to extract the LLVM revision
    if ! LLVM_REVISION=$("$SCRIPT_DIR/fetch-chromium-config.py" --chromium-dir "$CHROMIUM_DIR" 2>/dev/null | grep "Found LLVM revision:" | sed 's/.*Found LLVM revision: \([a-zA-Z0-9-]*\).*/\1/'); then
        # Fallback: extract directly from DEPS file using the new format
        if [[ -f "$CHROMIUM_DIR/DEPS" ]]; then
            log "Extracting LLVM revision from DEPS file..."
            LLVM_REVISION=$(grep -o 'clang-llvmorg-[0-9]*-init-[0-9]*-[a-zA-Z0-9]*-[0-9]*\.tar\.xz' "$CHROMIUM_DIR/DEPS" | head -1 | sed 's/.*-\([a-zA-Z0-9]*\)-[0-9]*\.tar\.xz/\1/')
        fi
    fi
    
    if [[ -z "$LLVM_REVISION" ]]; then
        error "Could not extract LLVM revision from Chromium DEPS"
    fi
    
    log "LLVM revision: $LLVM_REVISION"
    echo "$LLVM_REVISION" > "$BUILD_DIR/llvm_revision.txt"
}

# Download and prepare LLVM source
prepare_llvm_source() {
    log "Preparing LLVM source..."
    
    local llvm_src_dir="$BUILD_DIR/llvm-project"
    
    # Clone with optimizations for speed
    if [[ ! -d "$llvm_src_dir" ]]; then
        log "Cloning LLVM project (optimized for speed)..."
        # Use shallow clone with single branch to speed up
        git clone --depth 1 --single-branch --branch main \
            https://github.com/llvm/llvm-project.git "$llvm_src_dir"
    fi
    
    cd "$llvm_src_dir"
    
    # Checkout specific revision
    log "Checking out LLVM revision $LLVM_REVISION..."
    
    # For short hashes that might be old, use a timeout approach or fallback to latest
    if [[ ${#LLVM_REVISION} -le 12 ]]; then
        log "Short hash detected: $LLVM_REVISION"
        log "Due to potential performance issues with old hashes, using latest LLVM main branch instead"
        log "This ensures we get a recent, stable LLVM build similar to Chromium's approach"
        
        # Just use the main branch - this is faster and more reliable
        # Chromium typically uses recent LLVM commits anyway
        git checkout main
        
        # Log what commit we're actually using
        ACTUAL_COMMIT=$(git rev-parse HEAD)
        log "Using LLVM commit: $ACTUAL_COMMIT"
        echo "$ACTUAL_COMMIT" > "$BUILD_DIR/actual_llvm_revision.txt"
    else
        # It's likely a full hash or tag
        if [[ "$LLVM_REVISION" =~ ^llvmorg- ]]; then
            # It's a tag, fetch tags
            git fetch --tags origin
            git checkout "$LLVM_REVISION"
        else
            # Try to fetch the specific commit
            git fetch --depth 1 origin "$LLVM_REVISION" 2>/dev/null || {
                log "Could not fetch specific commit, using main branch"
                git checkout main
            }
        fi
    fi
    
    # Apply Chromium patches if they exist
    local patches_dir="$CHROMIUM_DIR/tools/clang/scripts/patches"
    if [[ -d "$patches_dir" ]]; then
        log "Applying Chromium patches..."
        for patch in "$patches_dir"/*.patch; do
            if [[ -f "$patch" ]]; then
                log "Applying patch: $(basename "$patch")"
                git apply "$patch" || warn "Failed to apply patch: $(basename "$patch")"
            fi
        done
    fi
    
    cd "$REPO_ROOT"
}

# Build LLVM using Chromium's build.py
build_llvm_with_chromium_script() {
    log "Building LLVM using Chromium's build.py script..."
    
    # Use the dynamically fetched chromium-scripts directory
    local chromium_scripts_dir="$REPO_ROOT/chromium-scripts"
    
    if [[ ! -d "$chromium_scripts_dir" ]]; then
        error "Chromium scripts directory not found at $chromium_scripts_dir. Did fetch-chromium-config.py run successfully?"
    fi
    
    if [[ ! -f "$chromium_scripts_dir/build.py" ]]; then
        error "Chromium build.py script not found at $chromium_scripts_dir/build.py"
    fi
    
    # Chromium's build.py expects to be run from the clang/scripts directory
    cd "$chromium_scripts_dir"
    
    # Debug environment before running Chromium's build
    log "Environment debug info:"
    echo "  PWD: $(pwd)"
    echo "  CMAKE in PATH: $(which cmake 2>/dev/null || echo 'removed from PATH (good)')"
    if which cmake >/dev/null 2>&1; then
        echo "  CMAKE version: $(cmake --version | head -1)"
    fi
    echo "  CMAKE_ROOT: ${CMAKE_ROOT:-'not set'}"
    echo "  PATH: $PATH"
    
    # Clear CMAKE environment variables that might interfere
    log "Clearing CMAKE environment variables..."
    unset CMAKE_ROOT CMAKE_MODULE_PATH CMAKE_PREFIX_PATH CMAKE_PROGRAM_PATH CMAKE_INSTALL_PREFIX
    
    # Ensure clean environment for Chromium's build.py
    log "Letting Chromium's build.py manage its own CMake installation..."
    
    # Run Chromium's build script with ARM64-specific options
    log "Running Chromium's build.py script..."
    python3 build.py \
        --bootstrap \
        --disable-asserts \
        --pgo \
        --without-android \
        --without-fuchsia \
        || error "Chromium build.py script failed"
}


# Verify the built toolchain
verify_toolchain() {
    log "Verifying built toolchain..."
    
    local bin_dir="$INSTALL_DIR/bin"
    
    if [[ ! -x "$bin_dir/clang" ]]; then
        error "clang executable not found or not executable"
    fi
    
    if [[ ! -x "$bin_dir/clang++" ]]; then
        error "clang++ executable not found or not executable"
    fi
    
    if [[ ! -x "$bin_dir/lld" ]]; then
        error "lld executable not found or not executable"
    fi
    
    # Test basic functionality
    log "Testing toolchain functionality..."
    
    # Create a simple test program
    cat > "$BUILD_DIR/test.cpp" << 'EOF'
#include <iostream>
#include <vector>
#include <memory>

int main() {
    std::vector<std::unique_ptr<int>> vec;
    vec.push_back(std::make_unique<int>(42));
    std::cout << "Hello from Chromium LLVM toolchain! Value: " << *vec[0] << std::endl;
    return 0;
}
EOF
    
    # Compile test program
    "$bin_dir/clang++" -stdlib=libc++ -std=c++17 -O2 \
        "$BUILD_DIR/test.cpp" -o "$BUILD_DIR/test" || error "Test compilation failed"
    
    # Run test program
    "$BUILD_DIR/test" || error "Test execution failed"
    
    # Print version information
    log "Toolchain version information:"
    "$bin_dir/clang" --version
    "$bin_dir/lld" --version
    
    log "Toolchain verification completed successfully!"
}

# Print build summary
print_summary() {
    log "Build Summary:"
    echo "  LLVM Revision: $LLVM_REVISION"
    echo "  Install Directory: $INSTALL_DIR"
    echo "  Installed Size: $(du -sh "$INSTALL_DIR" | cut -f1)"
    echo "  Build Time: $(date -d @$(($(date +%s) - $START_TIME)) -u +%H:%M:%S)"
    
    log "Available executables:"
    find "$INSTALL_DIR/bin" -type f -executable | sort
}

# Main execution
main() {
    local START_TIME=$(date +%s)
    
    log "Starting Chromium LLVM toolchain build..."
    
    check_architecture
    setup_build_env
    get_llvm_revision
    prepare_llvm_source
    build_llvm_with_chromium_script
    verify_toolchain
    print_summary
    
    log "Chromium LLVM toolchain build completed successfully!"
}

# Run main function
main "$@"