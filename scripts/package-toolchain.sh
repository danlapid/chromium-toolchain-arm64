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
RED='\033[0;31m'
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

# Try to use Chromium's package.py script for packaging
package_with_chromium_script() {
    log "Attempting to use Chromium's package.py script..."
    
    local chromium_scripts_dir="$REPO_ROOT/chromium-scripts"
    
    if [[ ! -f "$chromium_scripts_dir/package.py" ]]; then
        warn "Chromium package.py not found, using simple tar packaging..."
        return 1
    fi
    
    cd "$chromium_scripts_dir"
    
    # Note: Chromium's package.py may have different arguments
    # We'll try common patterns and fall back if they fail
    log "Running Chromium's package.py script..."
    
    # Try different argument patterns that Chromium might use
    if python3 package.py --help >/dev/null 2>&1; then
        log "Chromium package.py available, but needs custom integration"
        warn "Chromium's package.py is designed for their GCS upload workflow"
        warn "Falling back to simple packaging for now"
        return 1
    else
        warn "Chromium package.py not compatible, using simple packaging"
        return 1
    fi
}

# Simple tar-based packaging (fallback)
package_simple() {
    log "Creating simple tar package..."
    
    if [[ ! -d "$INSTALL_DIR" ]]; then
        error "Install directory not found: $INSTALL_DIR"
    fi

    cd "$REPO_ROOT"
    
    # Get build information
    local llvm_revision_file="$BUILD_DIR/actual_llvm_revision.txt"
    local llvm_revision="unknown"
    
    if [[ -f "$llvm_revision_file" ]]; then
        llvm_revision=$(head -1 "$llvm_revision_file" | cut -c1-8)
    fi
    
    local timestamp=$(date +%Y%m%d-%H%M%S)
    local package_name="chromium-llvm-toolchain-linux-arm64-${timestamp}-${llvm_revision}"
    
    log "Creating package: $package_name.tar.xz"
    
    # Create the package
    tar -cJf "${package_name}.tar.xz" \
        --transform "s|^install|$package_name|" \
        install/
    
    # Create checksum
    sha256sum "${package_name}.tar.xz" > "${package_name}.tar.xz.sha256"
    
    # Create usage script
    cat > "${package_name}-usage.sh" << 'EOF'
#!/bin/bash
# Usage script for Chromium LLVM Toolchain

TOOLCHAIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Chromium LLVM Toolchain Usage:"
echo "  Export PATH: export PATH=\"$TOOLCHAIN_DIR/bin:\$PATH\""
echo "  Clang version: $TOOLCHAIN_DIR/bin/clang --version"
echo "  Available tools:"
find "$TOOLCHAIN_DIR/bin" -type f -executable | sort | sed 's|.*/|  - |'
EOF
    
    chmod +x "${package_name}-usage.sh"
    
    log "Package created successfully:"
    log "  Package: ${package_name}.tar.xz ($(du -h "${package_name}.tar.xz" | cut -f1))"
    log "  Checksum: ${package_name}.tar.xz.sha256"
    log "  Usage script: ${package_name}-usage.sh"
    
    # Summary
    log "Package contents:"
    echo "  Total size: $(du -sh "$INSTALL_DIR" | cut -f1)"
    echo "  Executables: $(find "$INSTALL_DIR/bin" -type f -executable | wc -l)"
    echo "  Libraries: $(find "$INSTALL_DIR/lib" -name "*.so*" 2>/dev/null | wc -l)"
}

# Main execution
main() {
    log "Starting toolchain packaging..."
    
    # Try Chromium's packaging first, fall back to simple if needed
    if ! package_with_chromium_script; then
        package_simple
    fi
    
    log "Toolchain packaging completed!"
}

main "$@"