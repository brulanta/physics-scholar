#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/apps/physics-scholar
git pull --ff-only
.venv/bin/pip install -r requirements.txt
sudo systemctl restart physics-scholar
sudo systemctl status physics-scholar --no-pager
