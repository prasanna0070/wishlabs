from gstudio.cad import decor as D
import os
print("fonts", [(p, os.path.exists(p)) for p in D.FONT_PATHS])
for t in ["KIRAN STORES", "WELCOME", "NIMBUS"]:
    s = D._text_solid({"text": t, "height": 11}, 0, 120, -10, 20)
    print(t, None if s is None else (round(s.Volume(),1), s.BoundingBox().xlen, s.BoundingBox().zlen))
