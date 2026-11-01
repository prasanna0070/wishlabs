#!/bin/bash
# Integration: full builds of the three examples into _out/r1-int/<slug> (run on the VM from studio/).
cd "$(dirname "$0")/../.."
for s in bedtime-robot cloud-lamp desk-buddy; do
  rm -rf "_out/r1-int/$s"
  (PYTHONPATH=engine python -m gstudio build "examples/$s/spec.json" "_out/r1-int/$s" --json > "_out/r1-int/$s.json" 2> "_out/r1-int/$s.log"; echo "EXIT=$?" >> "_out/r1-int/$s.log") > /dev/null 2>&1 &
done
