#!/bin/bash
# web-frontend REVIEW-1 dev server on VM port 9107 (scripted agent, fake builder copying _out/r1-web-frontend/bedtime-robot)
set -u
S=/Users/prasanna/os/brain/projects/consumer-hardware-platform/studio
R=$S/_out/r1-web-frontend
D=$R/devroot
mkdir -p $D/examples/bedtime-robot
ln -sfn $S/engine $D/engine; ln -sfn $S/library $D/library; ln -sfn $S/docs $D/docs
ln -sfn $S/examples/bedtime-robot/spec.json $D/examples/bedtime-robot/spec.json
ln -sfn $R/bedtime-robot $D/examples/bedtime-robot/_out
STORE=$R/store-9107
if [ ! -d $STORE/projects/p_d565a53hmmrp ]; then mkdir -p $STORE/projects; cp -a $S/web/server/_out/e2e-store/projects/p_d565a53hmmrp $STORE/projects/; fi
PIDF=$R/server-9107.pid
if [ -f $PIDF ]; then kill $(cat $PIDF) 2>/dev/null; sleep 1; fi
cd $S/web/server
env STORE_DIR=$STORE AGENT=${AGENT:-scripted} BUILDER=${BUILDER:-fake} ACCESS_KEY=gstudio-dev PHOTOS=0 FAKE_BUILD_SECONDS=${FAKE_BUILD_SECONDS:-8} \
  STATIC_DIR=$S/web/app/dist STUDIO_ROOT=$D \
  nohup /opt/gs/venv/bin/uvicorn app.asgi:app --host 127.0.0.1 --port 9107 > $R/server-9107.log 2>&1 &
echo $! > $PIDF
for i in $(seq 1 40); do curl -sf http://127.0.0.1:9107/api/health >/dev/null && break; sleep 0.25; done
curl -s http://127.0.0.1:9107/api/health; echo
