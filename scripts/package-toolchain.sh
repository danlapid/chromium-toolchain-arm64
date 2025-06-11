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

# Use Chromium's package.py script for packaging
package_with_chromium_script() {
    log "Using Chromium's package.py script for packaging..."
    
    # Use the dynamically fetched chromium-scripts directory
    local chromium_scripts_dir="$REPO_ROOT/chromium-scripts"
    
    if [[ ! -d "$chromium_scripts_dir" ]]; then
        error "Chromium scripts directory not found at $chromium_scripts_dir. Did fetch-chromium-config.py run successfully?"
    fi
    
    if [[ ! -f "$chromium_scripts_dir/package.py" ]]; then
        error "Chromium package.py script not found at $chromium_scripts_dir/package.py"
    fi
    
    cd "$chromium_scripts_dir"
    
    log "Running Chromium's package.py script..."
    
    # Use Chromium's package.py with appropriate arguments
    # Note: We may need to adapt this based on what arguments package.py actually accepts
    python3 package.py \
        --build-dir "$BUILD_DIR" \
        --install-dir "$INSTALL_DIR" \
        --output-dir "$REPO_ROOT" \
        || error "Chromium package.py script failed"
    
    log "Packaging with Chromium's script completed successfully!"
}

# Main execution
main() {
    log "Starting toolchain packaging..."
    
    # Use Chromium's packaging - fail if it doesn't work
    package_with_chromium_script
    
    log "Toolchain packaging completed!"
}

main "$@"