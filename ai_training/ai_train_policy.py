#!/usr/bin/env python3
# 정책 신경망 학습 (numpy MLP) — 행동 모방학습 + 승패 가중
import json, numpy as np

np.random.seed(42)

# ===== 데이터 로드 =====
data = json.load(open('/home/claude/selfplay_data.json'))
X = np.array([d['o'] for d in data], dtype=np.float32)          # (N, 84)
y = np.array([d['a'] for d in data], dtype=np.int64)            # (N,) 행동 0-3
w = np.array([1.5 if d['w'] == 1 else 0.7 for d in data], dtype=np.float32)  # 승리 행동 가중
N, D = X.shape
print(f"데이터: {N} samples, {D} dims, 행동분포: {np.bincount(y, minlength=4)}")

# 학습/검증 분리
idx = np.random.permutation(N)
split = int(N * 0.9)
tr, va = idx[:split], idx[split:]
Xtr, ytr, wtr = X[tr], y[tr], w[tr]
Xva, yva = X[va], y[va]

# ===== MLP: 84 -> 64 -> 32 -> 4 =====
H1, H2, OUT = 64, 32, 4
def init(a, b):
    return (np.random.randn(a, b) * np.sqrt(2.0/a)).astype(np.float32)
W1, b1 = init(D, H1), np.zeros(H1, np.float32)
W2, b2 = init(H1, H2), np.zeros(H2, np.float32)
W3, b3 = init(H2, OUT), np.zeros(OUT, np.float32)

def relu(x): return np.maximum(0, x)
def softmax(x):
    x = x - x.max(axis=1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=1, keepdims=True)

def forward(Xb):
    z1 = Xb @ W1 + b1; a1 = relu(z1)
    z2 = a1 @ W2 + b2; a2 = relu(z2)
    z3 = a2 @ W3 + b3; p = softmax(z3)
    return z1, a1, z2, a2, p

# ===== Adam =====
params = [W1, b1, W2, b2, W3, b3]
mom = [np.zeros_like(p) for p in params]
vel = [np.zeros_like(p) for p in params]
lr, beta1, beta2, eps = 2e-3, 0.9, 0.999, 1e-8
t = 0

def adam_step(grads):
    global t
    t += 1
    for i, (p, g) in enumerate(zip(params, grads)):
        mom[i] = beta1*mom[i] + (1-beta1)*g
        vel[i] = beta2*vel[i] + (1-beta2)*(g*g)
        mh = mom[i]/(1-beta1**t)
        vh = vel[i]/(1-beta2**t)
        p -= lr * mh/(np.sqrt(vh)+eps)

# ===== 학습 루프 =====
EPOCHS, BS = 40, 256
ntr = len(Xtr)
for ep in range(EPOCHS):
    perm = np.random.permutation(ntr)
    tot_loss = 0; nb = 0
    for s in range(0, ntr, BS):
        bi = perm[s:s+BS]
        Xb, yb, wb = Xtr[bi], ytr[bi], wtr[bi]
        z1, a1, z2, a2, p = forward(Xb)
        m = len(bi)
        # weighted cross-entropy
        logp = np.log(p[np.arange(m), yb] + 1e-9)
        loss = -(wb * logp).mean()
        tot_loss += loss; nb += 1
        # backward
        dz3 = p.copy()
        dz3[np.arange(m), yb] -= 1
        dz3 *= wb[:, None]      # 가중치
        dz3 /= m
        gW3 = a2.T @ dz3; gb3 = dz3.sum(0)
        da2 = dz3 @ W3.T; dz2 = da2 * (z2 > 0)
        gW2 = a1.T @ dz2; gb2 = dz2.sum(0)
        da1 = dz2 @ W2.T; dz1 = da1 * (z1 > 0)
        gW1 = Xb.T @ dz1; gb1 = dz1.sum(0)
        adam_step([gW1, gb1, gW2, gb2, gW3, gb3])
    # 검증 정확도
    _,_,_,_, pv = forward(Xva)
    acc = (pv.argmax(1) == yva).mean()
    if (ep+1) % 5 == 0 or ep == 0:
        print(f"  epoch {ep+1:2d}/{EPOCHS} | loss {tot_loss/nb:.4f} | val_acc(휴리스틱 일치율) {acc*100:.1f}%")

# ===== 가중치 내보내기 (브라우저용 JSON) =====
def to_list(a): return [round(float(x), 5) for x in a.flatten()]
weights = {
    "arch": [D, H1, H2, OUT],
    "W1": to_list(W1), "b1": to_list(b1),
    "W2": to_list(W2), "b2": to_list(b2),
    "W3": to_list(W3), "b3": to_list(b3),
}
json.dump(weights, open('/home/claude/ppo_weights.json', 'w'))
sz = len(json.dumps(weights))/1024
print(f"\n저장: ppo_weights.json ({sz:.0f} KB) | 구조 {D}->{H1}->{H2}->{OUT}")
