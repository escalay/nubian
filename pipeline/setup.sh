#!/bin/bash
# Setup script for the Dongolese Nubian Lexicon Digitization Pipeline
# Run this on your M4 Max Mac
#
# Prerequisites: Python 3.10+ and pip

set -e

echo "========================================"
echo "Nubian Lexicon Digitization - Setup"
echo "========================================"

# Create virtual environment
echo ""
echo "1. Creating Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

# Install Chandra OCR with HuggingFace backend (includes PyTorch)
echo ""
echo "2. Installing Chandra OCR (this will download PyTorch for Apple Silicon)..."
pip install 'chandra-ocr[hf]'

# Verify MPS support
echo ""
echo "3. Verifying Apple Silicon GPU support..."
python3 -c "
import torch
print(f'PyTorch version: {torch.__version__}')
print(f'MPS available: {torch.backends.mps.is_available()}')
if torch.backends.mps.is_available():
    print('✓ Your M4 Max GPU will be used for inference')
else:
    print('⚠ MPS not available, will fall back to CPU')
"

echo ""
echo "========================================"
echo "Setup complete!"
echo ""
echo "To activate the environment in future sessions:"
echo "  source .venv/bin/activate"
echo ""
echo "Quick test (process 3 pages):"
echo "  python stage1_ocr.py -i 'books/Dongolese Nubian_ A Lexicon - Charles Hubert Armbruster (1).pdf' -o ./ocr_output --page-range 18-20"
echo ""
echo "Then parse the results:"
echo "  python stage2_parse.py -i ./ocr_output -o ./lexicon_entries.json --validate --pretty"
echo ""
echo "Full run - Nubian-English section (pages 18-222):"
echo "  python stage1_ocr.py -i '../books/Dongolese Nubian_ A Lexicon - Charles Hubert Armbruster (1).pdf' -o ./ocr_output --page-range 18-222 --resume"
echo ""
echo "Full run - English-Nubian section (pages 223-286):"
echo "  python stage1_ocr.py -i '../books/Dongolese Nubian_ A Lexicon - Charles Hubert Armbruster (1).pdf' -o ./ocr_output --page-range 223-286 --resume"
echo "========================================"
