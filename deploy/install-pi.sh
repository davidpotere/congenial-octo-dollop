#!/usr/bin/env bash
# One-time setup on a Raspberry Pi 5 (Raspberry Pi OS Bookworm) or any Debian/Ubuntu box.
set -euo pipefail
cd "$(dirname "$0")/.."
sudo apt-get update
sudo apt-get install -y python3-venv ffmpeg rsync
python3 -m venv .venv
.venv/bin/pip install -U pip -r requirements.txt
# Narration voice (Piper, local and fast on a Pi 5)
.venv/bin/pip install piper-tts
mkdir -p voices
[ -f voices/en_US-amy-medium.onnx ] || {
  curl -L -o voices/en_US-amy-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
  curl -L -o voices/en_US-amy-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json
}
[ -f .env ] || cp .env.example .env
[ -f config.yaml ] || cp config.example.yaml config.yaml
sed "s#__DIR__#$(pwd)#g; s#__USER__#$(whoami)#g" deploy/naturalist.service | sudo tee /etc/systemd/system/naturalist.service >/dev/null
sudo systemctl daemon-reload
echo
echo "Next: edit .env and config.yaml (set narration.engine: piper and narration.voice: $(pwd)/voices/en_US-amy-medium.onnx),"
echo "then:  .venv/bin/python -m naturalist check   and   sudo systemctl enable --now naturalist"
