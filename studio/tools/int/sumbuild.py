import json, sys, os
for out in sys.argv[1:]:
    print("==", out)
    try:
        st = json.load(open(os.path.join(out, "status.json")))
    except Exception as e:
        print("  no status", e); continue
    print("  stages", {x['id']: x['state'] for x in st.get('stages', [])}, "verdict", st.get('verdict'))
    try:
        d = json.load(open(os.path.join(out, "checks/digital-checks.json")))
        print("  digital", d.get('summary'))
        for r in d['checks']:
            if r['status'] in ('failed', 'warning'):
                print("   ", r['status'].upper(), r['id'], '|', str(r.get('detail'))[:300])
    except Exception as e:
        print("  no checks", e)
    try:
        pk = json.load(open(os.path.join(out, ".gstudio/stages/package.json")))
        for c in (pk.get('summary') or {}).get('checks') or []:
            print("   PKG", c['status'], c['id'], '|', str(c.get('detail'))[:200])
    except Exception as e:
        print("  no package rec", e)
