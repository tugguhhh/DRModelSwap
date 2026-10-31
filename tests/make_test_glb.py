# Makes a small test model (a boxy van with a checker texture): python tests/make_test_glb.py testvan.glb
import sys
# a boxy van: body + cabin + 4 wheels, one checker texture
import struct, json, io, numpy as np
from PIL import Image, ImageDraw
def box(cx,cy,cz,sx,sy,sz,u0,v0,u1,v1):
    P=[];N=[];UV=[];T=[]
    for ax in range(3):
        for sg in (-1,1):
            n=[0,0,0]; n[ax]=sg; a=(ax+1)%3; b=(ax+2)%3
            if sg<0: a,b=b,a
            base=len(P)
            for (i,j) in ((-1,-1),(1,-1),(1,1),(-1,1)):
                p=[0,0,0]; p[ax]=sg; p[a]=i; p[b]=j
                P.append([cx+p[0]*sx,cy+p[1]*sy,cz+p[2]*sz]); N.append(n); UV.append([u0+(i+1)/2*(u1-u0), v0+(1-(j+1)/2)*(v1-v0)])
            T+= [[base,base+1,base+2],[base,base+2,base+3]]
    return P,N,UV,T
parts=[box(0,0.9,0,0.9,0.5,2.2,0,0,1,0.5), box(0,1.65,-0.3,0.85,0.35,1.5,0,0.5,1,1)]
for x in (-0.95,0.95):
    for z in (-1.4,1.4): parts.append(box(x,0.35,z,0.12,0.35,0.35,0.45,0.45,0.55,0.55))
P=[];N=[];UV=[];T=[]
for p,n,uv,t in parts:
    o=len(P); P+=p; N+=n; UV+=uv; T+=[[a+o,b+o,c+o] for a,b,c in t]
P=np.array(P,np.float32); N=np.array(N,np.float32); UV=np.array(UV,np.float32); T=np.array(T,np.uint16)
img=Image.new('RGB',(512,512),(230,230,230)); d=ImageDraw.Draw(img)
for i in range(0,512,64):
    for j in range(0,512,64):
        if (i//64+j//64)%2: d.rectangle([i,j,i+63,j+63],fill=(60,160,60))
d.rectangle([0,0,511,40],fill=(220,30,30)); d.rectangle([0,471,511,511],fill=(30,30,220))
d.text((200,240),"DR CO-OP TEST",fill=(0,0,0))
buf=io.BytesIO(); img.save(buf,'PNG'); png=buf.getvalue()
bins=[P.tobytes(),N.tobytes(),UV.tobytes(),T.tobytes(),png]
bv=[];off=0;blob=b''
for b in bins:
    bv.append({'buffer':0,'byteOffset':off,'byteLength':len(b)}); pad=(-len(b))%4; blob+=b+b'\0'*pad; off+=len(b)+pad
js={'asset':{'version':'2.0'},'scene':0,'scenes':[{'nodes':[0]}],'nodes':[{'mesh':0}],
 'meshes':[{'primitives':[{'attributes':{'POSITION':0,'NORMAL':1,'TEXCOORD_0':2},'indices':3,'material':0}]}],
 'materials':[{'name':'van','pbrMetallicRoughness':{'baseColorTexture':{'index':0}}}],
 'textures':[{'source':0}],'images':[{'bufferView':4,'mimeType':'image/png'}],
 'accessors':[{'bufferView':0,'componentType':5126,'count':len(P),'type':'VEC3','min':P.min(0).tolist(),'max':P.max(0).tolist()},
  {'bufferView':1,'componentType':5126,'count':len(N),'type':'VEC3'},{'bufferView':2,'componentType':5126,'count':len(UV),'type':'VEC2'},
  {'bufferView':3,'componentType':5123,'count':T.size,'type':'SCALAR'}],
 'bufferViews':bv, 'buffers':[{'byteLength':len(blob)}]}
j=json.dumps(js).encode(); j+=b' '*((-len(j))%4)
out=struct.pack('<III',0x46546C67,2,12+8+len(j)+8+len(blob))+struct.pack('<II',len(j),0x4E4F534A)+j+struct.pack('<II',len(blob),0x004E4942)+blob
open(sys.argv[1] if len(sys.argv) > 1 else __import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), 'testvan.glb'),'wb').write(out); print(len(out))
