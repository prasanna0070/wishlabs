#!/bin/bash
# Run heavy work on the temporary dev VM, never on the laptop.
# The studio/ tree is mirrored to the SAME absolute path on the VM.
#   tools/vm.sh sync                 push local studio/ sources to the VM (no --delete; out dirs excluded)
#   tools/vm.sh run '<command>'      run in studio/ on the VM with the toolchain env (venv, PlatformIO, KiCad)
#   tools/vm.sh pull <rel-path>      copy a file/dir from the VM's studio/<rel-path> back to the laptop
#   tools/vm.sh ssh                  interactive shell
# Toolchain on the VM: /opt/gs/venv (python3.12 + cadquery + pcbnew via system site), kicad-cli 9,
# Java 25 at $GS_JAVA (/opt/gs/jre25/bin/java) + Freerouting 2.5.0 at $GS_FREEROUTING_JAR, PlatformIO core dir /opt/gs/pio, prusa-slicer, node 22, xvfb-run.
set -euo pipefail
HOST=gstudio-dev-temp.asia-south1-a.your-gcp-project
STUDIO="$(cd "$(dirname "$0")/.." && pwd)"
SSH="ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -i $HOME/.ssh/google_compute_engine -o UserKnownHostsFile=$HOME/.ssh/google_compute_known_hosts"
cmd="${1:-}"; shift || true
case "$cmd" in
  sync)
    $SSH $HOST "sudo mkdir -p '$STUDIO' && sudo chown -R \$(id -u):\$(id -g) /Users 2>/dev/null; true"
    rsync -az -e "$SSH" --exclude node_modules --exclude .venv --exclude '__pycache__' --exclude '/out' --exclude '_out' \
      --exclude '.pio' --exclude 'dist' "$STUDIO/" "$HOST:$STUDIO/" ;;
  run)
    q=$(printf %q "$*")
    $SSH $HOST "cd '$STUDIO' && export PATH=/opt/gs/venv/bin:\$PATH PLATFORMIO_CORE_DIR=/opt/gs/pio PYTHONUNBUFFERED=1 GS_FREEROUTING_JAR=/opt/gs/freerouting.jar GS_JAVA=/opt/gs/jre25/bin/java && bash -lc $q" </dev/null ;;
  pull)
    rel="$1"; mkdir -p "$(dirname "$STUDIO/$rel")"
    rsync -az -e "$SSH" "$HOST:$STUDIO/$rel" "$(dirname "$STUDIO/$rel")/" ;;
  ssh) $SSH $HOST ;;
  *) echo "usage: vm.sh sync|run '<cmd>'|pull <rel>|ssh"; exit 2 ;;
esac
