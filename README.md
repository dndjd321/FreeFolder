# FreeFolder

개인 프로젝트 저장소 - 게임, 도구, 실험적 프로그램 모음

---

## 📂 Projects

### ⚡ Pokemon Battle AI
**PPO 강화학습 AI와 대전하는 포켓몬 배틀 게임**

- **언어**: Python, JavaScript, HTML/CSS
- **프레임워크**: PyQt6, FastAPI, PyTorch
- **AI**: PPO (Proximal Policy Optimization) 강화학습
- **특징**: 
  - 싱글플레이 (AI 대전)
  - 멀티플레이 (WebSocket 실시간 대전)
  - 1000+ 포켓몬, 완전한 배틀 시스템
  - 데스크톱 앱 + 클라우드 배포

현재 개발판은 서버 전투 엔진, 20구간 로그라이크, 보스 예고·분기 안내와 모바일 이어하기를 제공한다. 전투 전 선두를 정하고 기술별 PP와 쉼터·상점의 실제 회복량을 확인할 수 있다. 여러 탭의 입력 충돌을 막고 탐험 접근정보를 개별 보존한다. 기본/전략 탐험을 선택할 수 있고, 30종 재학습 신경망은 실험 옵션으로 제공한다. 규칙 AI를 계속 권장한다. 완료 범위와 검증 결과는 [구현 진행](docs/IMPLEMENTATION_PROGRESS.md), 실행 방법은 [배포 안내](docs/DEPLOYMENT.md)에 있다.

📁 **위치**: [`pokemon_rl/`](./pokemon_rl)

**다음 작업 재개:** [인계 문서](docs/DEVELOPMENT_HANDOFF.md) · [남은 개발 계획](docs/NEXT_DEVELOPMENT_PLAN.md). “이어서 진행해줘” 또는 “이어서 작업해줘”라고 요청하면 첫 미완료 항목부터 진행한다. QA1–QA4를 완료했으며 다음은 QA5 여러 탭의 생성 제한 갱신이다. QA1–QA6 뒤 기존 11번 탐험 평가로 이어간다.

---

## 🛠️ Tech Stack

| Category | Technologies |
|----------|-------------|
| **언어** | Python 3.10+, JavaScript (ES6+), HTML5/CSS3 |
| **프론트엔드** | Vanilla JS, PyQt6 WebEngine |
| **백엔드** | FastAPI, Uvicorn, WebSocket |
| **AI/ML** | PyTorch, Gymnasium (OpenAI Gym) |
| **배포** | Render.com, PyInstaller |

---

## 🤖 Built with Claude AI

이 저장소의 프로젝트들은 **Anthropic Claude AI**와의 협업으로 개발되었습니다.

전체 아키텍처 설계, 코드 구현, 디버깅, 최적화 과정에서 Claude가 개발을 지원했습니다.

---

## 📝 License

MIT License - 자유롭게 사용, 수정, 배포 가능

---

## 👤 Author

**ByeongJun Gu** (dndjd321)

<p align="center">
  <i>Powered by Claude AI</i>
</p>
