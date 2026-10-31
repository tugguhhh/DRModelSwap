"""Test models for the vehicle rigging: a car (body, four wheels of tyre + rim + brake calliper, a steering wheel, a spare
tyre on the back) and a bike. python tests/make_test_car.py out_folder"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from drmodelswap import glbwrite
def cyl(c, r, w, axis=0, n=32, inner=None):
    # closed cylinder along axis; inner radius -> tube (tyre)
    a=np.linspace(0,2*np.pi,n,endpoint=False); P=[];T=[]
    def ring(rad, x):
        pts=np.zeros((n,3)); pts[:,axis]=x; o=[k for k in range(3) if k!=axis]; pts[:,o[0]]=rad*np.cos(a); pts[:,o[1]]=rad*np.sin(a); return pts+c
    rings=[ring(r,-w/2),ring(r,w/2)]
    P=np.concatenate(rings)
    for i in range(n):
        j=(i+1)%n; T+= [[i,j,n+j],[i,n+j,n+i]]
    # caps
    c0=len(P); P=np.vstack([P,[c+np.eye(3)[axis]*-w/2, c+np.eye(3)[axis]*w/2]])
    for i in range(n):
        j=(i+1)%n; T+= [[c0,j,i],[c0+1,n+i,n+j]]
    return P,np.array(T)
def box(c,s):
    c=np.array(c); s=np.array(s)/2
    P=np.array([[x,y,z] for x in (-1,1) for y in (-1,1) for z in (-1,1)])*s+c
    T=np.array([[0,1,3],[0,3,2],[4,6,7],[4,7,5],[0,4,5],[0,5,1],[2,3,7],[2,7,6],[0,2,6],[0,6,4],[1,5,7],[1,7,3]])
    return P,T
def prim(PT, mi=0):
    P,T=PT; return (P, None, np.zeros((len(P),2)), T, mi)
def car(path, named=True, merged=False, scale=1.0, wr=0.33, wb=2.7, track=1.55, zup=False, extras=True):
    nodes=[]
    body=[prim(box([0,0.75,0],[1.8,0.7,4.4])), prim(box([0,1.35,-0.3],[1.6,0.5,2.2]))]
    wheels=[]
    for zf in (wb/2,-wb/2):
        for xs in (track/2,-track/2):
            c=np.array([xs,wr,zf])
            tire=prim(cyl(c,wr,0.22),1); rim=prim(cyl(c+[np.sign(xs)*0.02,0,0],wr*0.62,0.2),2)
            cal=prim(box(c+[-np.sign(xs)*0.05,wr*0.45,0.12],[0.06,0.12,0.14]),3)
            wheels.append(('Wheel_%s%s'%('F' if zf>0 else 'R','L' if xs>0 else 'R'),[tire,rim]+([cal] if extras else [])))
    if extras:
        body.append(prim(cyl(np.array([0.4,1.15,0.6]),0.18,0.04,axis=2),2))  # steering wheel (round, faces back)
        body.append(prim(cyl(np.array([0,0.9,-2.25]),0.3,0.2,axis=2),1))     # spare tyre on the back door
    if merged:
        allp=body+[p for _,ps in wheels for p in ps]
        nodes=[dict(name='Object_1',prims=allp)]
    else:
        nodes=[dict(name='Body' if named else 'Object_0',prims=body)]+[dict(name=n if named else 'Object_%d'%(i+1),prims=ps) for i,(n,ps) in enumerate(wheels)]
    for nd in nodes:
        nd['prims']=[(P*scale if not zup else (P*scale)[:,[0,2,1]]*[1,-1,1], N, UV, (T if not zup else T), mi) for P,N,UV,T,mi in nd['prims']]
    mats=[dict(name='paint',colour=[0.8,0.1,0.1]),dict(name='rubber',colour=[0.1,0.1,0.1]),dict(name='chrome',colour=[0.8,0.8,0.8]),dict(name='brake',colour=[0.9,0.6,0.1])]
    return glbwrite.write(path,nodes,mats)
def trike(path, front_single=True, named=True):
    """Three wheels: one in the middle at the front (or at the back), two at the other end; axle rods between pairs."""
    body=[prim(box([0,0.75,0],[1.6,0.7,3.6]))]
    nodes=[dict(name='Body',prims=body)]
    wr=0.33
    single_z, pair_z = (1.2, -1.2) if front_single else (-1.2, 1.2)
    nodes.append(dict(name='Wheel_single' if named else 'Object_1', prims=[prim(cyl(np.array([0,wr,single_z]),wr,0.22),1)]))
    for xs in (0.75,-0.75):
        nodes.append(dict(name=('Wheel_%s' % ('L' if xs>0 else 'R')) if named else 'Object_%d'%(2 if xs>0 else 3),
                          prims=[prim(cyl(np.array([xs,wr,pair_z]),wr,0.22),1), prim(cyl(np.array([xs+np.sign(xs)*0.02,wr,pair_z]),wr*0.6,0.2),2)]))
    nodes.append(dict(name='Axle_rod' if named else 'Object_9', prims=[prim(cyl(np.array([0,wr,pair_z]),0.04,1.5),3)]))
    mats=[dict(name='paint',colour=[0.2,0.6,0.9]),dict(name='rubber',colour=[0.1,0.1,0.1]),dict(name='chrome',colour=[0.8,0.8,0.8]),dict(name='steel',colour=[0.4,0.3,0.2])]
    return glbwrite.write(path,nodes,mats)


def round_car(path):
    """A tall, round car with its wheels close together (like a burger car) and rods for axles."""
    nodes=[dict(name='Bun',prims=[prim(box([0,1.5,0],[3.0,2.4,2.6]))])]
    wr=0.35
    for zf in (0.9,-0.9):
        for xs in (1.6,-1.6):
            nodes.append(dict(name='Pickle_wheel_%s%s'%('F' if zf>0 else 'R','L' if xs>0 else 'R'),prims=[prim(cyl(np.array([xs,wr,zf]),wr,0.15),1)]))
        nodes.append(dict(name='Stick_%s'%('front' if zf>0 else 'back'),prims=[prim(cyl(np.array([0,wr,zf]),0.05,3.5),2)]))
    mats=[dict(name='bun',colour=[0.8,0.5,0.2]),dict(name='pickle',colour=[0.4,0.7,0.2]),dict(name='stick',colour=[0.5,0.3,0.1])]
    return glbwrite.write(path,nodes,mats)


def bike(path):
    nodes=[dict(name='Frame',prims=[prim(box([0,0.6,0],[0.25,0.4,1.0]))]),
           dict(name='Front wheel',prims=[prim(cyl(np.array([0,0.33,0.7]),0.33,0.1),1)]),
           dict(name='Rear wheel',prims=[prim(cyl(np.array([0,0.33,-0.7]),0.33,0.12),1)]),
           dict(name='Handlebar',prims=[prim(box([0,1.0,0.55],[0.7,0.05,0.05]))])]
    return glbwrite.write(path,nodes,[dict(name='a',colour=[.2,.2,.8]),dict(name='b',colour=[.1,.1,.1])])


if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    car(os.path.join(out, 'car_named.glb'))
    car(os.path.join(out, 'car_unnamed.glb'), named=False)
    car(os.path.join(out, 'car_one_mesh.glb'), merged=True)
    bike(os.path.join(out, 'bike.glb'))
    trike(os.path.join(out, 'trike.glb'))
    trike(os.path.join(out, 'trike_back.glb'), front_single=False)
    print('written to', out)
