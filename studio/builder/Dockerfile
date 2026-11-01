# gstudio-builder: heavy build image (ARCHITECTURE §10). Mirrors the dev VM (builder/vm-setup-reference.sh):
#   Ubuntu 24.04, KiCad 9 (PPA) with symbols/footprints/3D models, Python 3.12 venv with system site packages
#   (for pcbnew) holding the VM's exact pip set, Temurin 25 JRE + Freerouting 2.5.0, PlatformIO with espressif32 +
#   Arduino warmed, PrusaSlicer 2.7, xvfb, fonts, Playwright headless Chromium (PCB SVG -> PNG).
# Build context: a staged folder with engine/, library/ and builder/ (deploy/builder.sh makes it).
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PLATFORMIO_CORE_DIR=/opt/gs/pio GS_FW_SHARED_DIR=/opt/gs/pio-shared GS_JAVA=/opt/gs/jre25/bin/java GS_FREEROUTING_JAR=/opt/gs/freerouting.jar \
    PLAYWRIGHT_BROWSERS_PATH=/opt/gs/pw/browsers STUDIO_ROOT=/app PATH=/opt/gs/venv/bin:$PATH \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1

# ---- system packages: KiCad 9 from the official PPA, PrusaSlicer, offscreen GL, fonts --------------------------------
RUN apt-get update \
 && apt-get install -y --no-install-recommends software-properties-common ca-certificates curl gnupg git rsync zip unzip jq \
      build-essential \
 && add-apt-repository -y ppa:kicad/kicad-9.0-releases \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get update \
 && apt-get install -y --no-install-recommends kicad kicad-symbols kicad-footprints kicad-packages3d \
 && apt-get install -y --no-install-recommends python3-venv python3-pip python3-dev xvfb xauth \
      libgl1 libglu1-mesa libgl1-mesa-dri libegl1 libegl-mesa0 libosmesa6 libxrender1 libxext6 \
      prusa-slicer nodejs fonts-dejavu fonts-inter fonts-liberation fonts-noto-core \
 && rm -rf /var/lib/apt/lists/* \
 && kicad-cli version && prusa-slicer --help | head -1

# ---- Java 25 (Temurin JRE) + Freerouting 2.5.0 (sha256 of the release jar, identical to the VM's) -----------------
ARG FREEROUTING_SHA256=f6f51bb02245e8e717f9359bd260cc9c5c0b1bc0acc8b7cb2cd5b8ffeb5de3c7
RUN mkdir -p /opt/gs/jre25 \
 && curl -fsSL -o /tmp/jre.tar.gz "https://api.adoptium.net/v3/binary/latest/25/ga/linux/x64/jre/hotspot/normal/eclipse" \
 && tar -xzf /tmp/jre.tar.gz -C /opt/gs/jre25 --strip-components=1 && rm /tmp/jre.tar.gz \
 && curl -fsSL -o /opt/gs/freerouting.jar https://github.com/freerouting/freerouting/releases/download/v2.5.0/freerouting-2.5.0.jar \
 && echo "${FREEROUTING_SHA256}  /opt/gs/freerouting.jar" | sha256sum -c - \
 && /opt/gs/jre25/bin/java -version

# ---- Python venv with system site packages (pcbnew) and the VM's exact pip set -------------------------------------
COPY builder/requirements.txt /tmp/requirements.txt
RUN python3 -m venv --system-site-packages /opt/gs/venv \
 && /opt/gs/venv/bin/pip install --no-cache-dir -U pip wheel \
 && /opt/gs/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
 && /opt/gs/venv/bin/python -c "import pcbnew, cadquery, OCP, vtk, trimesh, shapely, manifold3d, fastapi, google.cloud.storage; print('python ok', pcbnew.Version())"

# ---- Playwright headless Chromium (same version as the VM) ----------------------------------------------------------
RUN mkdir -p /opt/gs/pw && cd /opt/gs/pw && npm init -y >/dev/null && npm install --no-fund --no-audit playwright@1.56.1 \
 && npx playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/* /root/.npm

# ---- runtime user. PlatformIO's core dir belongs to it from the start, so no recursive chown/chmod ever copies the
#      multi-GB toolchain into a second layer.
RUN useradd -m -u 10001 gstudio && mkdir -p /opt/gs/pio /tmp/gstudio-builds && chown gstudio:gstudio /opt/gs/pio /tmp/gstudio-builds

# ---- PlatformIO: espressif32 + Arduino for esp32-s3-devkitc-1, and the native test platform, warmed by compiling ----
USER gstudio
RUN mkdir -p /tmp/pio-warm/src /tmp/pio-warm/test/test_warm && cd /tmp/pio-warm \
 && printf '[env:s3]\nplatform = espressif32@8.0.0\nboard = esp32-s3-devkitc-1\nframework = arduino\n\n[env:native]\nplatform = native\ntest_framework = unity\nbuild_src_filter = -<*>\n' > platformio.ini \
 && printf '#include <Arduino.h>\nvoid setup(){Serial.begin(115200);}\nvoid loop(){delay(1000);}\n' > src/main.cpp \
 && printf '#include <unity.h>\nvoid setUp(){}\nvoid tearDown(){}\nvoid t(){TEST_ASSERT_EQUAL(2, 1 + 1);}\nint main(){UNITY_BEGIN();RUN_TEST(t);return UNITY_END();}\n' > test/test_warm/test_main.cpp \
 && pio run -e s3 && pio test -e native \
 && cd / && rm -rf /tmp/pio-warm

# ---- the engine, the module library, the example specs and the service (read-only for the runtime user) -----------
USER root
COPY engine /app/engine
COPY library /app/library
COPY examples /app/examples
COPY builder /app/builder
RUN mkdir -p /opt/gs/pio-shared && chown gstudio:gstudio /opt/gs/pio-shared
USER gstudio
# Firmware warm-up (firmware owner's request): compiles every example and fixture once, which installs every pinned
# library and the native platform into GS_FW_SHARED_DIR, so builds need no registry access. Required: fails the image.
RUN cd /app/engine && python -c "import gstudio, gstudio.pipeline, gstudio.order, gstudio.checks, gstudio.package; print('engine', gstudio.__version__)" \
 && (python -m gstudio.firmware.tests.build_examples /tmp/fw-warm > /tmp/fw-warm.log 2>&1 || (tail -n 60 /tmp/fw-warm.log; exit 1)) \
 && tail -n 20 /tmp/fw-warm.log \
 && test -n "$(ls -A /opt/gs/pio-shared)" && rm -rf /tmp/fw-warm /tmp/fw-warm.log

WORKDIR /app/builder
ENV PYTHONPATH=/app/engine HOME=/home/gstudio PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT} --timeout-keep-alive 75 --no-access-log"]
