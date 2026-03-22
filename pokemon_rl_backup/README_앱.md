# 포켓몬 배틀 AI — 데스크탑 앱

서버 열고 닫기 없이 더블클릭 한 번으로 실행!

## 📁 폴더 구조

```
pokemon_rl/                  ← 기존 프로젝트 폴더
├── app.py                   ← ✅ 새로 추가 (메인 앱)
├── server_core.py           ← ✅ 새로 추가 (내장 서버)
├── pokemon_battle_v3.html   ← ✅ 새로 추가 (게임 UI)
├── 포켓몬배틀AI_실행.bat    ← ✅ 새로 추가 (실행파일)
├── 최초설치.bat             ← ✅ 새로 추가 (최초 1회만)
│
├── checkpoints/
│   ├── final_model.pt       ← 학습된 모델 (자동 인식)
│   └── best_model.pt        ← 또는 이거
│
├── agents/ppo_agent.py
├── env/battle_env.py
├── train.py
└── ...
```

## 🚀 실행 방법

### 최초 1회 설치
```
최초설치.bat 더블클릭
```

### 이후 매번
```
포켓몬배틀AI_실행.bat 더블클릭
```

또는 터미널에서:
```bash
python app.py
```

## ⚙️ 동작 원리

1. `app.py` 실행
2. **백그라운드에서 FastAPI 서버 자동 시작** (포트 8765)
3. **`checkpoints/` 폴더에서 모델 자동 탐색**
   - `final_model.pt` → `best_model.pt` → `checkpoint_XXXXXXXX.pt` 순으로 탐색
4. **PyQt6 윈도우 + 내장 웹뷰**에서 게임 로드
5. 창 닫으면 서버도 자동 종료

## 🔧 트러블슈팅

### "PyQt6-WebEngine이 없다" 메시지가 뜨는 경우
내장 브라우저 대신 기본 브라우저(Chrome 등)가 자동으로 열립니다.
그래도 게임은 정상 동작합니다.
```
pip install PyQt6-WebEngine
```

### 모델이 "랜덤 AI"로 뜨는 경우
`checkpoints/` 폴더에 `final_model.pt` 또는 `best_model.pt` 가 있는지 확인하세요.

### 포트 충돌
`app.py` 상단의 `SERVER_PORT = 8765` 를 다른 번호로 바꾸세요.
