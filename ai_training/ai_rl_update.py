#!/usr/bin/env python3
# 안전한 REINFORCE: 낮은 학습률 + 그래디언트 클리핑 + 어드밴티지 정규화
import json, numpy as np, sys
data = json.load(open('/home/claude/rl_traj.json'))
if len(data) < 50: print("too few"); sys.exit(0)
X = np.array([d['o'] for d in data], dtype=np.float32)
A = np.array([d['a'] for d in data], dtype=np.int64)
R = np.array([d['r'] for d in data], dtype=np.float32)
R = (R - R.mean()) / (R.std() + 1e-6)   # 어드밴티지 정규화
R = np.clip(R, -3, 3)

W = json.load(open('/home/claude/ppo_weights.json'))
D,H1,H2,OUT = W['arch']
def arr(k,r,c): return np.array(W[k],dtype=np.float32).reshape(r,c)
def vec(k): return np.array(W[k],dtype=np.float32)
W1=arr('W1',D,H1);b1=vec('b1');W2=arr('W2',H1,H2);b2=vec('b2');W3=arr('W3',H2,OUT);b3=vec('b3')
def relu(x): return np.maximum(0,x)
def softmax(x): x=x-x.max(1,keepdims=True);e=np.exp(x);return e/e.sum(1,keepdims=True)
def forward(Xb):
    z1=Xb@W1+b1;a1=relu(z1);z2=a1@W2+b2;a2=relu(z2);z3=a2@W3+b3;return z1,a1,z2,a2,softmax(z3)
params=[W1,b1,W2,b2,W3,b3]
mom=[np.zeros_like(p) for p in params];vel=[np.zeros_like(p) for p in params]
lr=float(sys.argv[1]) if len(sys.argv)>1 else 1.5e-4
b1_,b2_,eps=0.9,0.999,1e-8;t=0
def clipg(g,c=1.0):
    n=np.sqrt((g*g).sum());return g*(c/n) if n>c else g
def adam(grads):
    global t;t+=1
    for i,(p,g) in enumerate(zip(params,grads)):
        g=clipg(g)
        mom[i]=b1_*mom[i]+(1-b1_)*g;vel[i]=b2_*vel[i]+(1-b2_)*(g*g)
        mh=mom[i]/(1-b1_**t);vh=vel[i]/(1-b2_**t)
        p-=lr*mh/(np.sqrt(vh)+eps)
N=len(X);BS=512;EPOCHS=2
for ep in range(EPOCHS):
    perm=np.random.permutation(N)
    for s in range(0,N,BS):
        bi=perm[s:s+BS];Xb,Ab,Rb=X[bi],A[bi],R[bi];m=len(bi)
        z1,a1,z2,a2,p=forward(Xb)
        dz3=p.copy();dz3[np.arange(m),Ab]-=1;dz3*=Rb[:,None];dz3/=m   # REINFORCE
        gW3=a2.T@dz3;gb3=dz3.sum(0)
        da2=dz3@W3.T;dz2=da2*(z2>0);gW2=a1.T@dz2;gb2=dz2.sum(0)
        da1=dz2@W2.T;dz1=da1*(z1>0);gW1=Xb.T@dz1;gb1=dz1.sum(0)
        adam([gW1,gb1,gW2,gb2,gW3,gb3])
def tl(a): return [round(float(x),5) for x in a.flatten()]
json.dump({"arch":[D,H1,H2,OUT],"W1":tl(W1),"b1":tl(b1),"W2":tl(W2),"b2":tl(b2),"W3":tl(W3),"b3":tl(b3)}, open('/home/claude/ppo_weights.json','w'))
print(f"updated N={N} lr={lr}")
