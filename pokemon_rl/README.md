# ⚡ Pokemon Battle AI

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/PyQt6-WebEngine-green?logo=qt" />
  <img src="https://img.shields.io/badge/AI-PPO%20Reinforcement%20Learning-orange" />
  <img src="https://img.shields.io/badge/Multiplayer-WebSocket-purple" />
  <img src="https://img.shields.io/badge/License-MIT-yellow" />
</p>

<p align="center">
  <b>PPO 강화학습 AI와 대전하는 포켓몬 배틀 게임</b><br>
  싱글플레이 · 멀티플레이 · 1000+ 포켓몬 · 한국어
</p>

---

## 🎮 Features

| Feature | Description |
|---------|-------------|
| **싱글플레이** | PPO 강화학습 AI + 규칙 기반 전략 AI 대전 |
| **멀티플레이** | WebSocket 기반 실시간 대전 (방 생성/참가) |
| **1000+ 포켓몬** | PokeAPI 기반 전 세대 포켓몬 데이터 |
| **배틀 시스템** | 타입 상성, 특성, 도구, 날씨, 필드, 트릭룸, 스텔스록 등 |
| **PPO AI** | PyTorch 기반 강화학습으로 훈련된 신경망 AI |
| **데스크톱 앱** | PyQt6 네이티브 윈도우 (브라우저 불필요) |

---

## 🚀 Quick Start

### 1. 설치
```bash
git clone https://github.com/dndjd321/FreeFolder.git
cd FreeFolder/pokemon_rl
pip install -r requirements.txt
```

### 2. 실행
```bash
# Windows
run.bat

# 또는 직접 실행
python app.py
```

### 3. 플레이
- **SINGLE PLAY** → 포켓몬 3마리 선택 → AI 대전
- **MULTIPLAYER** → 방 만들기/참가 → 친구와 대전
- **SETTINGS** → BGM, 닉네임, 배틀 속도 설정

---

## 🏗️ Project Structure

```
pokemon_rl/
├── app.py                    # PyQt6 데스크톱 앱
├── server_core.py            # FastAPI 서버 (배틀 API + WebSocket)
├── server_deploy.py          # 클라우드 배포용 경량 서버
├── pokemon_battle_v3.html    # 게임 UI + 배틀 엔진 + 내장 AI
├── run.bat                   # 실행 스크립트
│
├── env/                      # 강화학습 환경
│   ├── battle_env.py         # Gymnasium 배틀 환경
│   └── damage_calc.py        # 데미지 계산기
│
├── agents/                   # AI 에이전트
│   └── ppo_agent.py          # PPO 알고리즘
│
├── checkpoints/              # 학습된 모델
│   └── final_model.pt        # PPO 모델 가중치
│
├── data/                     # 포켓몬 데이터
│   ├── pokemon.json          # PokeAPI 원본 데이터
│   └── build_html_db.py      # HTML DB 빌더
│
└── export_ppo_to_js.py       # PPO → JavaScript 변환기
```

---

## 🤖 AI System

### 규칙 기반 AI
- KO 판단 (원킬 가능 시 최우선)
- 선제기 / 회복기 / 설정기 상황 판단
- 스텔스록, 트릭룸, 순풍 전략
- 상태이상 우선순위 (잠듦 > 마비 > 화상 > 독)

### PPO 강화학습 AI
- **관측 공간**: 84차원 (스탯, 기술 효과, 필드 상태 등)
- **행동 공간**: 6 (기술 4 + 교체 2)
- **네트워크**: 128 → 64 → 6 (2-layer MLP)

### AI 학습 & 적용
```bash
# 학습 (기존 모델 재학습)
python train.py

# 학습된 모델을 HTML에 탑재
python export_ppo_to_js.py --model checkpoints/final_model.pt --html pokemon_battle_v3.html
```

---

## 🌐 Multiplayer

### 로컬 대전
```
run.bat 실행 → MULTIPLAYER → 방 만들기 → 같은 네트워크 친구 접속
```

### 온라인 대전 (Render.com)
```
서버: server_deploy.py가 Render.com에서 상시 운영
클라이언트: PokemonBattle.exe 실행 → 자동 접속 → 방 만들기/참가
```

---

## 📦 배포 (친구에게 공유)

```bash
# 클라이언트 EXE 빌드
build_client.bat

# dist/PokemonBattle/ 폴더를 ZIP → 친구에게 전달
# 친구는 PokemonBattle.exe 더블클릭만 하면 플레이 가능
```

---

## 🛠️ Tech Stack

- **Frontend**: HTML5, CSS3, JavaScript (Vanilla)
- **Backend**: FastAPI, Uvicorn, WebSocket
- **Desktop**: PyQt6 WebEngine / pywebview
- **AI**: PyTorch, PPO (Proximal Policy Optimization)
- **Data**: PokeAPI, 1000+ Pokemon
- **Deploy**: Render.com (Free Tier)

---

<p align="center">
  Made with ⚡ by <b>ByeongJun Gu</b>
</p>
