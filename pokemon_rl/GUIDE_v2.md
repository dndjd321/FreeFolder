# 🎮 포켓몬 싱글 배틀 강화학습 AI — 완전 가이드 v2

## 📁 전체 프로젝트 구조

```
pokemon_rl/
├── data/
│   └── fetch_pokeapi.py      ← 🆕 PokéAPI 전 세대 데이터 자동 수집
├── env/
│   ├── battle_env.py         ← Gymnasium 배틀 환경 (특성/날씨/지형 통합)
│   ├── pokemon.py            ← 포켓몬/기술 클래스
│   ├── damage_calc.py        ← 데미지 공식 + 타입 상성표
│   ├── ability.py            ← 🆕 특성 시스템 (23종)
│   └── weather.py            ← 🆕 날씨 + 지형 시스템
├── agents/
│   └── ppo_agent.py          ← PPO 에이전트 + Actor-Critic 신경망
├── train.py                  ← 기본 학습 (규칙 기반 상대)
├── train_selfplay.py         ← 🆕 Self-Play + 리그 시스템 학습
├── play.py                   ← 사람 vs AI 대전 (터미널)
└── requirements.txt
```

---

## 🚀 빠른 시작

```bash
pip install -r requirements.txt

# ① 포켓몬 데이터 수집 (선택 — 1세대 약 2분)
python data/fetch_pokeapi.py --gen 1

# ② 기본 학습 (규칙 기반 상대, GPU 30~60분)
python train.py --timesteps 2000000

# ③ Self-Play 고급 학습 (리그 시스템, GPU 2~4시간)
python train_selfplay.py --timesteps 5000000

# ④ AI와 대전
python play.py --model checkpoints_selfplay/best_model.pt
```

---

## 🆕 추가된 기능 상세

### 1. PokéAPI 전 세대 데이터 수집

```bash
# 1세대만 (151마리, ~2분)
python data/fetch_pokeapi.py --gen 1

# 1~3세대 (386마리, ~8분)
python data/fetch_pokeapi.py --gen 1-3

# 전 세대 (1025마리, ~30분)
python data/fetch_pokeapi.py --gen all
```

수집되는 데이터:
- 기저 스탯 6종 (HP/공격/방어/특공/특방/스피드)
- 타입 (1~2개)
- 레벨업 기술 목록 (최대 20개)
- 스프라이트 URL

---

### 2. 특성(Ability) 시스템 — 23종 구현

| 특성 | 효과 |
|---|---|
| 맹화/격류/과부하 | HP 1/3 이하에서 해당 타입 1.5배 |
| 위협 | 등장 시 상대 공격 -1 |
| 두꺼운지방 | 불꽃/얼음 기술 데미지 절반 |
| 저수/축전 | 물/전기 기술 무효 + HP 회복 |
| 부유 | 땅 기술 완전 무효 |
| 매직가드 | 날씨/독/화상 간접 피해 없음 |
| 근성 | 상태이상 시 물리공격 1.5배 |
| 가속 | 턴 종료마다 스피드 +1 |
| 의기양양 | 상대 기절 시 공격 +1 |
| 적응력 | STAB 1.5배 → 2배 |
| 재생력 | 교체 시 HP 1/3 회복 |
| 껍질갑옷 | 접촉 공격한 상대에게 반동 |
| 불꽃몸 | 불꽃 기술 무효 + 이후 불꽃 1.5배 |
| 자연회복 | 교체 시 상태이상 해제 |
| 쓱쓱/엽록소 | 비/쾌청에서 스피드 2배 |
| 모래날리기/가뭄/우천 | 등장 시 날씨 발동 |

---

### 3. 날씨(Weather) 시스템

| 날씨 | 효과 |
|---|---|
| ☀️ 쾌청 | 불꽃 1.5배, 물 0.5배, 합성/아침해 회복량 2/3 |
| 🌧️ 비 | 물 1.5배, 불꽃 0.5배, 썬더/폭풍 명중 100% |
| 🌪️ 모래바람 | 바위/강철/땅 이외 매 턴 1/16 데미지, 바위 특방 1.5배 |
| 🌨️ 싸라기눈 | 얼음 이외 매 턴 1/16 데미지, 블리자드 명중 100% |

날씨 발동 특성 포켓몬:
- **한카리아스(모래날리기)** → 등장 즉시 모래바람
- 날씨 5턴 지속 후 자동 소멸

---

### 4. 지형(Terrain) 시스템

| 지형 | 효과 |
|---|---|
| 전기장 | 전기 기술 1.3배, 수면 방지 |
| 풀밭 | 풀 기술 1.3배, 매 턴 HP 1/16 회복, 지진 0.5배 |
| 안개 | 모든 상태이상 방지, 드래곤 기술 0.5배 |
| 사이코 | 사이코 기술 1.3배, 우선도 기술 무효 |

---

### 5. Self-Play + 리그 시스템

```
학습 흐름:

[메인 에이전트] ←--배틀--→ [상대 에이전트]
     │ 학습                      │
     │                    ┌──────┴──────┐
     └──50,000스텝마다──→  리그 풀 갱신  │
                          │(최대 5버전)  │
                          └─────────────┘
                                │
                    에피소드마다 10% 확률로
                    리그 풀 → 랜덤 선택
```

**왜 Self-Play가 중요한가?**
- 규칙 기반 상대는 항상 같은 패턴 → AI가 그 패턴만 익힘
- Self-Play: 상대도 같이 강해지므로 AI가 계속 도전받음
- 리그 풀: 과거 버전과도 싸워 "퇴화" 방지 (망각 방지)

---

## 📈 학습 단계별 기대 승률

| 단계 | 스텝 | 승률 | 특징 |
|---|---|---|---|
| 초기 | 0 ~ 500K | 40~55% | 타입 상성 인식 시작 |
| 전술 | 500K ~ 2M | 55~70% | 날씨/특성 활용, HP 관리 |
| 전략 | 2M ~ 5M | 70~80% | 스탯 랭크 전략, 교체 타이밍 |
| 고급 | 5M+ (Self-Play) | 80%+ | 리그 학습, 복잡한 시너지 |

---

## 🔧 하이퍼파라미터 튜닝 가이드

```bash
# 탐험 강화 (다양한 전략 시도)
python train_selfplay.py --timesteps 5000000 # entropy_coef는 코드에서 0.01→0.05

# 빠른 수렴 (강한 상대 필요 시)
python train_selfplay.py --opponent-update-freq 20000

# 큰 네트워크 (복잡한 전략)
python train_selfplay.py --hidden-dim 512

# 리그 확대 (다양성 강조)
python train_selfplay.py --league-size 10
```

---

## 🐞 트러블슈팅

| 문제 | 원인 | 해결 |
|---|---|---|
| PokéAPI 수집 느림 | rate limit | `--delay 0.5` 로 늘리기 |
| Self-Play 승률 50% 고착 | 상대와 동시 강해짐 | `--league-size` 늘리기 |
| 날씨/특성 미반영 느낌 | 보상 함수 반영 필요 | 날씨 활성화 보상 추가 가능 |
| 메모리 부족 | buffer_size 너무 큼 | `--buffer-size 2048` |

---

## 📚 참고 자료

- [PokéAPI](https://pokeapi.co/) — 포켓몬 데이터 REST API
- [Pokémon Showdown 소스](https://github.com/smogon/pokemon-showdown) — 공식 배틀 시뮬레이터 로직
- [Smogon 데미지 공식](https://www.smogon.com/dp/articles/damage_formula) — 정확한 공식
- [PPO 논문](https://arxiv.org/abs/1707.06347) — Proximal Policy Optimization
- [AlphaGo Zero](https://www.nature.com/articles/nature24270) — Self-Play의 원조
