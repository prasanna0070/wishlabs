#!/bin/bash
# Toolchain bring-up for the Gadget Studio dev VM (mirrors the future builder Dockerfile).
set -x
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y software-properties-common ca-certificates curl gnupg git rsync zip unzip jq build-essential
add-apt-repository -y ppa:kicad/kicad-9.0-releases
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get update
apt-get install -y --no-install-recommends kicad kicad-symbols kicad-footprints kicad-packages3d
apt-get install -y python3-venv python3-pip python3-dev xvfb xauth libgl1 libglu1-mesa libxrender1 libxext6 libegl1 libosmesa6 \
  openjdk-21-jre-headless prusa-slicer nodejs docker.io fonts-dejavu fonts-inter || true
python3 -m venv --system-site-packages /opt/gs/venv
/opt/gs/venv/bin/pip install -U pip wheel
/opt/gs/venv/bin/pip install cadquery trimesh shapely vtk pyvista matplotlib pillow numpy scipy networkx rtree manifold3d lxml \
  fastapi uvicorn[standard] httpx pydantic google-genai google-cloud-storage platformio pygltflib
mkdir -p /opt/gs && cd /opt/gs
FR=$(curl -fsSL https://api.github.com/repos/freerouting/freerouting/releases/latest | jq -r '.assets[] | select(.name|test("freerouting-.*\\.jar$")) | .browser_download_url' | head -1)
echo "freerouting url: $FR"; curl -fsSL -o /opt/gs/freerouting.jar "$FR"
# Warm PlatformIO with an ESP32-S3 Arduino build.
mkdir -p /opt/gs/pio-warm/src && cd /opt/gs/pio-warm
cat > platformio.ini <<'INI'
[env:s3]
platform = espressif32
board = esp32-s3-devkitc-1
framework = arduino
INI
printf '#include <Arduino.h>\nvoid setup(){Serial.begin(115200);}\nvoid loop(){delay(1000);}\n' > src/main.cpp
PLATFORMIO_CORE_DIR=/opt/gs/pio /opt/gs/venv/bin/pio run || true
chmod -R a+rwX /opt/gs
echo SETUP_DONE
