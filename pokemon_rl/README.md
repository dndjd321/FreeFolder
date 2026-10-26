# ⚡ Pokemon Battle AI

현재 개발 버전은 **Showdown 서버 판정 + 20노드 로그라이크 + 같은 엔진을 사용하는 AI 학습 환경**을 제공한다. 기존 한국어 팀 빌더와 자유 대전 화면을 유지한다. 진행 상황은 [구현 기록](../docs/IMPLEMENTATION_PROGRESS.md), 배포·저장 설정은 [실행 및 배포 안내](../docs/DEPLOYMENT.md)를 참고한다.

## 현재 버전 실행

Node.js 24와 Python 3.11을 권장한다. 저장소를 받은 뒤 Windows에서는 `pokemon_rl/setup_engine.bat`를 한 번 실행하고 `pokemon_rl/run_server.bat`로 서버와 브라우저를 연다.

직접 실행하려면 `pokemon_rl` 폴더에서 다음 명령을 사용한다.

```powershell
python -m pip install -r requirements.txt
npm --prefix battle_service ci --ignore-scripts --no-audit --no-fund
python -m uvicorn server_deploy:app --host 127.0.0.1 --port 8765
```

브라우저에서 `http://127.0.0.1:8765`를 연다. 싱글 플레이의 **로그라이크 탐험**에서 추천 파티 또는 검증된 30종 중 3마리를 골라 시작할 수 있다. 런은 서버 SQLite에 자동 저장되며, 같은 브라우저에서 이어할 수 있다. 다른 기기로 옮기는 계정 기능은 아직 없다. 기본 저장 경로는 `runtime/runs.sqlite3`, 별도 저장 위치는 `FREEFOLDER_SAVE_DIR`로 지정한다.

멀티플레이는 서버가 승패를 판정하며 연결이 끊기면 60초간 재접속을 기다린다. 방의 제한시간은 서버가 적용하고 시간 초과 시 합법 행동을 자동 선택한다. 더블 배틀과 고정 날씨 선택은 준비 중이며 싱글만 제공한다. 기존 타워·체육관 등 캠페인은 이전 엔진 경로를 유지한다.

신경망은 실험용 초기 모델이다. 일반 서버는 규칙 AI로 동작하며 신경망을 사용하려면 별도 PyTorch 의존성이 필요하다. 새 학습·평가 방법과 실제 승률은 [학습 안내](training/README.md)를 참고한다. 아래의 `train.py`와 `export_ppo_to_js.py` 설명은 이전 학습 경로에 해당한다.

## 자동 검사

```powershell
python -X utf8 -B -m unittest discover -s tests -p "test_*.py" -v
node --test tests/battle_service.test.cjs tests/frontend_stability.test.cjs tests/engine_frontend.test.cjs tests/rogue_frontend.test.cjs
```

학습 검사까지 실행할 때는 `requirements-training.txt`의 CPU PyTorch 설치 안내를 먼저 따른다. GitHub Actions는 검사와 Docker 기동 검사를 수행하며 이미지를 외부에 게시하지 않는다.

## 이전 버전 기록

아래는 기존 데스크톱 버전의 기능과 실행 기록이다. 현재 서버 엔진·로그라이크·AI 개발에는 위 실행법과 연결된 문서를 사용한다.

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

## 🤝 Built with Claude

이 프로젝트는 **Anthropic Claude AI**와의 협업으로 개발되었습니다.

게임 설계, 배틀 엔진, 강화학습 환경, 멀티플레이 서버, UI/UX, 배포 파이프라인까지 전 과정에서 Claude가 코드 작성 및 아키텍처 설계를 지원했습니다.

---

<p align="center">
  Made with ⚡ by <b>ByeongJun Gu</b> & <b>Claude AI</b>
</p>
