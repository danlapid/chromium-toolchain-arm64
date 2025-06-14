## Chromium LLVM Toolchain Builder

This repository builds a Linux ARM64 native LLVM toolchain using Chromium's exact configuration via CI/CD.

### Key Features

- **Native ARM64 build** on GitHub's ARM64 runners
- **Uses Chromium's exact LLVM configuration** - patches, build flags, and scripts
- **Automated CI/CD pipeline** with artifact uploads
- **Weekly builds** to stay current with Chromium updates
- **Single Python script** for simplified build process

### Architecture

The build process is streamlined and uses Chromium's tools directly:

1. **`scripts/build_toolchain.py`** - Single Python script that:
   - Fetches Chromium source code
   - Extracts LLVM revision from Chromium's DEPS file
   - Runs Chromium's own `build.py` script for building LLVM
   - Verifies the built toolchain

2. **GitHub Actions workflow** - Automated CI/CD that:
   - Runs on `ubuntu-24.04-arm` for native ARM64 builds
   - Uses ccache for build acceleration
   - Packages using Chromium's `package.py` script
   - Uploads build artifacts

### Usage

#### Local Build
```bash
# Build the toolchain
python3 scripts/build_toolchain.py

# Just get the LLVM revision
python3 scripts/build_toolchain.py --get-llvm-revision
```

#### Key Commands
- **Build**: `python3 scripts/build_toolchain.py --version main`
- **Package**: Use Chromium's packaging script in `chromium/tools/clang/scripts/package.py`

### Directory Structure

```
chromium-toolchain-arm64/
├── scripts/
│   └── build_toolchain.py          # Single build script
├── .github/workflows/
│   └── build-toolchain.yml         # CI/CD pipeline
├── chromium/                       # Chromium source (git ignored)
└── ccache/                         # Build cache (git ignored)
```

### Implementation Notes

- **Works directly in chromium directory** - no complex copying or directory structures
- **Uses Chromium's own tools** - `build.py` for building, `package.py` for packaging
- **Python logging** - clean, professional output
- **Minimal dependencies** - just Chromium source and standard Python

### Customization

- **Build flags**: Modify the build command in `build_toolchain.py`
- **Workflow triggers**: Adjust in `.github/workflows/build-toolchain.yml` 
- **Chromium version**: Use `--version` flag to specify branch/tag

This provides a production-ready toolchain builder that matches Chromium's exact configuration while building natively on ARM64.