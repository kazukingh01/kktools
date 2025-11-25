#! /bin/bash
set -euo

cd "$(dirname "$0")"
python -m venv venv
source ./venv/bin/activate
pip install -r requirements.txt
./venv/bin/playwright install chromium
echo "alias pyplaywright='$(pwd)/venv/bin/python $(pwd)/pyplaywright.py'" >> ~/.bashrc
source ~/.bashrc
