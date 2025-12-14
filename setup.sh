#!/bin/bash
# Setup script for Zotero Manager
# Run this script to create the virtual environment and install dependencies
#
# Usage:
#   ./setup.sh          - Setup and activate environment
#   source setup.sh     - Setup and activate in current shell

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "Zotero Manager - Environment Setup"
echo "=========================================="

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is required but not installed."
    exit 1
fi

PYTHON_VERSION=$(python3 --version)
echo "Found: $PYTHON_VERSION"

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo ""
    echo "Creating virtual environment..."
    python3 -m venv .venv
    echo "✓ Virtual environment created at .venv/"
else
    echo "✓ Virtual environment already exists at .venv/"
fi

# Check if already activated (VIRTUAL_ENV points to current venv)
if [ "$VIRTUAL_ENV" = "$SCRIPT_DIR/.venv" ]; then
    echo "✓ Virtual environment already activated"
else
    echo ""
    echo "Activating virtual environment..."
    source .venv/bin/activate
    echo "✓ Virtual environment activated"
fi

# Check if dependencies are already installed (use venv python explicitly)
if .venv/bin/python -c "import pyzotero, httpx" 2>/dev/null; then
    echo "✓ Dependencies already installed"
else
    # Upgrade pip
    echo ""
    echo "Upgrading pip..."
    pip install --upgrade pip --quiet

    # Install dependencies
    echo ""
    echo "Installing dependencies..."
    pip install pyzotero httpx --quiet
    echo "✓ Installed: pyzotero, httpx"
fi

# Check for .keys file
echo ""
if [ -f ".keys" ]; then
    echo "✓ Found .keys file"
    if grep -q "zotero=" .keys; then
        echo "✓ Zotero API key configured"
    else
        echo "⚠ Warning: No 'zotero=' entry found in .keys file"
        echo "  Add your API key: zotero=YOUR_API_KEY"
    fi
else
    echo "⚠ No .keys file found"
    echo "  Create .keys file with your Zotero API key:"
    echo "  echo 'zotero=YOUR_API_KEY' > .keys"
    echo ""
    echo "  Get your API key from: https://www.zotero.org/settings/keys"
fi

echo ""
echo "=========================================="
echo "Setup complete!"
echo "=========================================="
echo ""
echo "To activate the environment:"
echo "  source .venv/bin/activate"
echo ""
echo "To run the Zotero API client:"
echo "  python zotapi.py"
echo ""

set +e  # Disable exit on error
