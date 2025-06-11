#!/bin/bash

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="$REPO_ROOT/install"
BUILD_DIR="$REPO_ROOT/build"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

warn() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: $1${NC}"
}

# Get build information
get_build_info() {
    log "Gathering build information..."
    
    # Get LLVM revision
    if [[ -f "$BUILD_DIR/llvm_revision.txt" ]]; then
        LLVM_REVISION=$(cat "$BUILD_DIR/llvm_revision.txt")
    else
        LLVM_REVISION="unknown"
    fi
    
    # Get current date
    BUILD_DATE=$(date +%Y%m%d)
    
    # Get git commit if available
    if git rev-parse --git-dir > /dev/null 2>&1; then
        GIT_COMMIT=$(git rev-parse --short HEAD)
    else
        GIT_COMMIT="unknown"
    fi
    
    # Set package name
    PACKAGE_NAME="chromium-llvm-toolchain-linux-arm64-${BUILD_DATE}-${GIT_COMMIT}"
    
    log "Build information:"
    echo "  LLVM Revision: $LLVM_REVISION"
    echo "  Build Date: $BUILD_DATE"
    echo "  Git Commit: $GIT_COMMIT"
    echo "  Package Name: $PACKAGE_NAME"
}

# Create build info file
create_build_info() {
    log "Creating build information file..."
    
    local info_file="$INSTALL_DIR/BUILD_INFO"
    
    cat > "$info_file" << EOF
Chromium LLVM Toolchain Build Information
========================================

Build Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')
LLVM Revision: $LLVM_REVISION
Git Commit: $GIT_COMMIT
Built On: $(uname -a)
Architecture: $(uname -m)

Components Included:
- LLVM/Clang compiler with Chromium patches
- LLD linker
- compiler-rt runtime libraries
- libc++/libc++abi C++ standard library
- LLVM tools and utilities

Usage:
  export PATH=\$PWD/bin:\$PATH
  clang++ --version

For more information, visit:
https://github.com/$(git config --get remote.origin.url | sed 's/.*github.com[:/]\([^.]*\).*/\1/' 2>/dev/null || echo "your-username/your-repo")
EOF

    log "Build info created: $info_file"
}

# Create usage script
create_usage_script() {
    log "Creating usage script..."
    
    local usage_script="$INSTALL_DIR/setup-env.sh"
    
    cat > "$usage_script" << 'EOF'
#!/bin/bash

# Chromium LLVM Toolchain Environment Setup
# Source this script to add the toolchain to your PATH

TOOLCHAIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Add toolchain to PATH
export PATH="$TOOLCHAIN_DIR/bin:$PATH"

# Set up C++ standard library
export CXXFLAGS="${CXXFLAGS:-} -stdlib=libc++"
export LDFLAGS="${LDFLAGS:-} -stdlib=libc++"

# Set up compiler-rt as the default runtime
export CFLAGS="${CFLAGS:-} -rtlib=compiler-rt"
export CXXFLAGS="${CXXFLAGS:-} -rtlib=compiler-rt"

echo "Chromium LLVM Toolchain environment configured!"
echo "Toolchain directory: $TOOLCHAIN_DIR"
echo "Clang version: $(clang --version | head -1)"
echo ""
echo "Usage examples:"
echo "  clang hello.c -o hello"
echo "  clang++ -std=c++17 hello.cpp -o hello"
echo "  clang++ -O2 -flto hello.cpp -o hello  # With LTO"
EOF

    chmod +x "$usage_script"
    log "Usage script created: $usage_script"
}

# Strip debug symbols to reduce size
strip_binaries() {
    log "Stripping debug symbols to reduce package size..."
    
    local stripped_count=0
    
    # Strip binaries in bin directory
    if [[ -d "$INSTALL_DIR/bin" ]]; then
        for binary in "$INSTALL_DIR/bin"/*; do
            if [[ -f "$binary" && -x "$binary" ]]; then
                if file "$binary" | grep -q "not stripped"; then
                    strip "$binary" 2>/dev/null || warn "Could not strip $binary"
                    ((stripped_count++))
                fi
            fi
        done
    fi
    
    # Strip libraries
    find "$INSTALL_DIR/lib" -name "*.so*" -type f 2>/dev/null | while read -r lib; do
        if file "$lib" | grep -q "not stripped"; then
            strip --strip-unneeded "$lib" 2>/dev/null || warn "Could not strip $lib"
            ((stripped_count++))
        fi
    done
    
    log "Stripped $stripped_count binaries/libraries"
}

# Remove unnecessary files
cleanup_install() {
    log "Cleaning up installation directory..."
    
    local removed_size=0
    
    # Remove CMake files
    find "$INSTALL_DIR" -name "*.cmake" -type f -delete 2>/dev/null || true
    find "$INSTALL_DIR" -name "CMakeFiles" -type d -exec rm -rf {} + 2>/dev/null || true
    
    # Remove pkg-config files (usually not needed for standalone toolchain)
    find "$INSTALL_DIR" -name "*.pc" -type f -delete 2>/dev/null || true
    
    # Remove empty directories
    find "$INSTALL_DIR" -type d -empty -delete 2>/dev/null || true
    
    log "Cleanup completed"
}

# Create compressed archive
create_archive() {
    log "Creating compressed archive..."
    
    cd "$REPO_ROOT"
    
    # Create the package directory
    local package_dir="chromium-llvm-toolchain"
    
    if [[ -d "$package_dir" ]]; then
        rm -rf "$package_dir"
    fi
    
    # Copy installation to package directory
    cp -r "$INSTALL_DIR" "$package_dir"
    
    # Create tar.xz archive (best compression for toolchains)
    local archive_name="${PACKAGE_NAME}.tar.xz"
    
    log "Creating archive: $archive_name"
    tar -cJf "$archive_name" "$package_dir"
    
    # Create SHA256 checksum
    sha256sum "$archive_name" > "${archive_name}.sha256"
    
    # Get archive size
    local archive_size=$(du -h "$archive_name" | cut -f1)
    local uncompressed_size=$(du -sh "$package_dir" | cut -f1)
    
    log "Archive created successfully!"
    echo "  Archive: $archive_name"
    echo "  Compressed size: $archive_size"
    echo "  Uncompressed size: $uncompressed_size"
    echo "  SHA256: $(cat "${archive_name}.sha256" | cut -d' ' -f1)"
    
    # Clean up temporary directory
    rm -rf "$package_dir"
}

# Test the packaged toolchain
test_package() {
    log "Testing packaged toolchain..."
    
    local test_dir="$BUILD_DIR/package-test"
    mkdir -p "$test_dir"
    cd "$test_dir"
    
    # Extract the archive
    tar -xf "$REPO_ROOT/${PACKAGE_NAME}.tar.xz"
    
    # Test basic compilation
    cat > test.cpp << 'EOF'
#include <iostream>
#include <vector>
#include <memory>
#include <string>

int main() {
    std::vector<std::unique_ptr<std::string>> strings;
    strings.push_back(std::make_unique<std::string>("Hello from packaged toolchain!"));
    
    for (const auto& str : strings) {
        std::cout << *str << std::endl;
    }
    
    return 0;
}
EOF
    
    # Compile with the packaged toolchain
    ./chromium-llvm-toolchain/bin/clang++ -stdlib=libc++ -std=c++17 -O2 \
        test.cpp -o test || error "Package test compilation failed"
    
    # Run the test
    ./test || error "Package test execution failed"
    
    log "Package test passed!"
    
    # Clean up test directory
    cd "$REPO_ROOT"
    rm -rf "$test_dir"
}

# Print package summary
print_summary() {
    log "Packaging Summary:"
    
    local archive_name="${PACKAGE_NAME}.tar.xz"
    
    if [[ -f "$archive_name" ]]; then
        echo "  Package: $archive_name"
        echo "  Size: $(du -h "$archive_name" | cut -f1)"
        echo "  SHA256: $(cat "${archive_name}.sha256" | cut -d' ' -f1)"
        echo ""
        echo "Installation instructions:"
        echo "  1. Download and extract: tar -xf $archive_name"
        echo "  2. Add to PATH: export PATH=\$PWD/chromium-llvm-toolchain/bin:\$PATH"
        echo "  3. Or source setup script: source chromium-llvm-toolchain/setup-env.sh"
        echo ""
        echo "Contents:"
        tar -tf "$archive_name" | head -20
        if [[ $(tar -tf "$archive_name" | wc -l) -gt 20 ]]; then
            echo "  ... and $(( $(tar -tf "$archive_name" | wc -l) - 20 )) more files"
        fi
    else
        warn "Archive file not found: $archive_name"
    fi
}

# Main execution
main() {
    log "Starting toolchain packaging..."
    
    # Check if install directory exists
    if [[ ! -d "$INSTALL_DIR" ]]; then
        echo "ERROR: Install directory not found: $INSTALL_DIR"
        echo "Please run build-toolchain.sh first"
        exit 1
    fi
    
    get_build_info
    create_build_info
    create_usage_script
    strip_binaries
    cleanup_install
    create_archive
    test_package
    print_summary
    
    log "Toolchain packaging completed successfully!"
}

# Run main function
main "$@"