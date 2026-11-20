"""Pick corner-fill plates that square off the big page-block plate (centroids outside it, max coverage)."""
from shapely.geometry import Point, box
from shapely.ops import unary_union
def rrect(cx,cz,w,h):
    r=min(w,h)*0.28
    return box(cx-w/2+r,cz-h/2+r,cx+w/2-r,cz+h/2-r).buffer(r,quad_segs=16)
a=39.5; z0,z1=2.0,201.0; zc=(z0+z1)/2; W=2*a; H=z1-z0; r=0.28*W
big=rrect(0,zc,W,H)
ins=0.2
best=None
for xh0 in [x/2 for x in range(30,52)]:
  for zh0 in [z/2 for z in range(376,400)]:
    for xv0 in [x/2 for x in range(58,78)]:
      for zv0 in [z/2 for z in range(350,374)]:
        Hp=((xh0+a-ins)/2,(zh0+z1-ins)/2,(a-ins)-xh0,(z1-ins)-zh0)
        Vp=((xv0+a-ins)/2,(zv0+z1-ins)/2,(a-ins)-xv0,(z1-ins)-zv0)
        if any(big.buffer(0.15).contains(Point(p[0],p[1])) for p in (Hp,Vp)): continue
        if Hp[3]<3 or Vp[2]<3: continue
        u=unary_union([big,rrect(*Hp),rrect(*Vp)])
        sq=box(a-r,z1-r,a-0.6,z1-0.6)
        miss=sq.difference(u).area
        if best is None or miss<best[0]: best=(miss,Hp,Vp)
print('r',round(r,3),'arc centre',round(a-r,3),round(z1-r,3),'miss',round(best[0],3))
print('H',[round(v,3) for v in best[1]]); print('V',[round(v,3) for v in best[2]])
