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
    if ! LLVM_REVISION=$("$SCRIPT_DIR/fetch-chromium-config.py" --chromium-dir "$CHROMIUM_DIR" 2>/dev/null | grep "Found LLVM revision:" | sed 's/.*Found LLVM revision: \([a-zA-Z0-9]*\).*/\1/'); then
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
    
    if [[ ! -d "$llvm_src_dir" ]]; then
        log "Cloning LLVM project..."
        git clone --depth 1 https://github.com/llvm/llvm-project.git "$llvm_src_dir"
    fi
    
    cd "$llvm_src_dir"
    
    # Checkout specific revision
    log "Checking out LLVM revision $LLVM_REVISION..."
    
    # Try to fetch the revision/tag
    if [[ "$LLVM_REVISION" =~ ^llvmorg- ]]; then
        # It's a tag, fetch all tags
        log "Detected LLVM tag format, fetching tags..."
        git fetch --tags origin
        git checkout "$LLVM_REVISION"
    elif [[ ${#LLVM_REVISION} -le 12 ]]; then
        # It's a short hash, we need to fetch more to resolve it
        log "Short hash detected, fetching more commits to resolve full hash..."
        git fetch --unshallow origin main || git fetch --depth 10000 origin main
        
        # Find the full hash
        FULL_HASH=$(git rev-parse --verify "${LLVM_REVISION}^{commit}" 2>/dev/null || echo "")
        if [[ -n "$FULL_HASH" ]]; then
            log "Resolved short hash $LLVM_REVISION to full hash: $FULL_HASH"
            LLVM_REVISION="$FULL_HASH"
            git checkout "$LLVM_REVISION"
        else
            log "Could not resolve short hash, trying to fetch all commits..."
            git fetch --unshallow origin || true
            FULL_HASH=$(git rev-parse --verify "${LLVM_REVISION}^{commit}" 2>/dev/null || echo "")
            if [[ -n "$FULL_HASH" ]]; then
                log "Resolved short hash $LLVM_REVISION to full hash: $FULL_HASH"
                LLVM_REVISION="$FULL_HASH"
                git checkout "$LLVM_REVISION"
            else
                error "Could not resolve LLVM revision: $LLVM_REVISION"
            fi
        fi
    else
        # It's likely a full hash
        git fetch --depth 1 origin "$LLVM_REVISION" || git fetch --unshallow origin
        git checkout "$LLVM_REVISION"
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

# Configure LLVM build
configure_llvm() {
    log "Configuring LLVM build..."
    
    local llvm_src_dir="$BUILD_DIR/llvm-project"
    local llvm_build_dir="$BUILD_DIR/llvm-build"
    
    mkdir -p "$llvm_build_dir"
    cd "$llvm_build_dir"
    
    # CMake configuration based on Chromium's build.py
    local cmake_args=(
        -G Ninja
        -DCMAKE_BUILD_TYPE=Release
        -DCMAKE_INSTALL_PREFIX="$INSTALL_DIR"
        -DLLVM_ENABLE_PROJECTS="clang;clang-tools-extra;lld;compiler-rt"
        -DLLVM_ENABLE_RUNTIMES="libcxx;libcxxabi;libunwind"
        -DLLVM_TARGETS_TO_BUILD="AArch64;ARM;X86"
        -DLLVM_ENABLE_ASSERTIONS=OFF
        -DLLVM_ENABLE_BACKTRACES=ON
        -DLLVM_ENABLE_CRASH_OVERRIDES=OFF
        -DLLVM_ENABLE_DIA_SDK=OFF
        -DLLVM_ENABLE_DUMP=OFF
        -DLLVM_ENABLE_EXPENSIVE_CHECKS=OFF
        -DLLVM_ENABLE_PDB=OFF
        -DLLVM_ENABLE_TERMINFO=ON
        -DLLVM_ENABLE_THREADS=ON
        -DLLVM_ENABLE_WARNINGS=OFF
        -DLLVM_ENABLE_ZLIB=ON
        -DLLVM_OPTIMIZED_TABLEGEN=ON
        -DLLVM_USE_LINKER=lld
        -DCLANG_DEFAULT_LINKER=lld
        -DCLANG_DEFAULT_CXX_STDLIB=libc++
        -DCLANG_DEFAULT_RTLIB=compiler-rt
        -DCOMPILER_RT_BUILD_BUILTINS=ON
        -DCOMPILER_RT_BUILD_SANITIZERS=ON
        -DCOMPILER_RT_BUILD_XRAY=ON
        -DCOMPILER_RT_BUILD_LIBFUZZER=ON
        -DCOMPILER_RT_BUILD_PROFILE=ON
        -DLIBCXX_ENABLE_SHARED=ON
        -DLIBCXX_ENABLE_STATIC=ON
        -DLIBCXXABI_ENABLE_SHARED=ON
        -DLIBCXXABI_ENABLE_STATIC=ON
        -DLLVM_PARALLEL_LINK_JOBS=2
        -DCMAKE_C_COMPILER_LAUNCHER=ccache
        -DCMAKE_CXX_COMPILER_LAUNCHER=ccache
    )
    
    log "Running CMake with configuration..."
    cmake "${cmake_args[@]}" "$llvm_src_dir/llvm" || error "CMake configuration failed"
}

# Build LLVM
build_llvm() {
    log "Building LLVM toolchain..."
    
    local llvm_build_dir="$BUILD_DIR/llvm-build"
    cd "$llvm_build_dir"
    
    # Build with ninja
    log "Starting ninja build (this will take a while)..."
    ninja -j "$NPROC" || error "Build failed"
    
    log "Installing LLVM toolchain..."
    ninja install || error "Installation failed"
    
    log "Build completed successfully!"
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
    configure_llvm
    build_llvm
    verify_toolchain
    print_summary
    
    log "Chromium LLVM toolchain build completed successfully!"
}

# Run main function
main "$@"