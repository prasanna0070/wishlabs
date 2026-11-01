#!/bin/bash
# Smoke-test the backend in scripted+fake mode on the VM: login, create project, run the chat flow.
set -u
B=${B:-http://127.0.0.1:8090}
J=/tmp/gs-cookies.txt; rm -f $J
c() { curl -s -b $J -c $J "$@"; }
c -X POST $B/api/login -H 'content-type: application/json' -d '{"key":"dev"}'; echo
P=$(c -X POST $B/api/projects -H 'content-type: application/json' -d '{"name":"Bedtime Story Robot"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "pid=$P"
c -N -X POST $B/api/projects/$P/chat -H 'content-type: application/json' -d '{"text":"I want a cute robot that tells my daughter bedtime stories and remembers her favorite characters"}' | grep '^event' | sort | uniq -c
c $B/api/projects/$P | python3 -c 'import sys,json;p=json.load(sys.stdin);print(p["stage"],[ (m["kind"],m["role"]) for m in p["chat"]]); print(json.dumps(p["chat"][-1]["payload"])[:600])'
echo $P > /tmp/gs-pid
