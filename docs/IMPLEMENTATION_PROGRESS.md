# 포켓몬 게임 구현 진행

2026년 10월 26일부터 개발 계획에 따라 작업한다. 완료된 작업 묶음은 검증 후 기존 `main` 브랜치에 커밋하고 푸시한다.

| 단계 | 상태 | 산출물과 검증 |
| --- | --- | --- |
| 1 기존 버전 안정화 | 완료 | 공통 멀티플레이 서비스, 외부 입력 검증, 방 이탈 정리, 화면 XSS와 KO·교체 처리 수정. 서버 ASGI 24개와 실제 JS 함수 회귀 검사 13개 통과 |
| 2 엔진 연결 검증 | 완료 | Showdown 0.11.11 고정, 실제 엔진 10개와 subprocess 3개 검사 통과. 자원 이월·결정적 리플레이·worker 재시작 복원. Render 엔진·콘텐츠 상태 HTTP 200 확인 |
| 3 판정 통합 | 완료 | 서버 싱글 API와 권위 멀티플레이, 기존 한국어 화면 연결. API·실제 엔진 WebSocket 5개 및 UI 계약 19개 검사와 브라우저 싱글 전투 확인 |
| 4 로그라이크 완주 | 완료 | 20노드·30종·6테마·3보스·특전12종, 보상·분기·상점·휴식·부활·영입·SQLite 저장. 서버 18개(UI 14개) 검사와 실제 엔진 20노드 완주, 브라우저 시작·저장·재개 확인 |
| 5 AI 학습과 평가 | 완료 | 333차원 관측·7행동, 수집/업데이트 마스크 일치, 실제 상대 모델을 사용하는 자기 대전. 2,048단계 초기 모델과 2,000경기 평가, 학습 검사 7개 통과. 서버 신경망 추론 연결 |
| 6 배포와 밸런스 | 완료 | 실제 두 브라우저 대전·새로고침 복구, 서버 턴 제한, 설정, 공유 worker와 저장 복원, Windows 실행 확인. GitHub Actions의 Linux 전체 검사와 Docker 기동·볼륨 복원 검사 통과, Render 최신 코드 반영 확인 |

2026년 10월 27일 통합 검사: Python 94개, Node.js 72개 전부 통과했다. Python 검사는 엔진·HTTP·실제 ASGI WebSocket·방 규칙·저장·학습 계약을 포함한다. 네트워크 응답이 사라져도 이미 적용한 플레이어 입력을 다시 적용하지 않으며, 로그라이크는 worker 장애 후 저장 checkpoint에서 복구한다. 싱글·런·멀티플레이가 애플리케이션 소유의 한 worker를 공유한다.

Ego 브라우저에서 추천 팀으로 싱글 전투, 로그라이크 생성·행동·새로고침 후 복원, 두 탭 방 생성·참가·전투를 확인했다. 멀티플레이는 p1/p2 시점의 비공개 상대 벤치를 숨겼으며, p2가 2턴에 새로고침한 뒤 같은 역할·턴·HP `[82, 183, 202]`로 복원되었다. 로그라이크도 2턴의 HP `[84, 184, 202]`를 그대로 복원했다. 재접속 자동화 검사는 60초 유예, 중복 연결 거부, 대기 중 입력 유지와 제한시간 재개를 포함한다.

현재 신경망은 초기 기준 모델이고 기본 컨테이너는 규칙 AI를 제공한다. 브라우저 기록·도감은 해당 브라우저에 저장되며 런 SQLite와 분리되어 있다. 영구 능력치 상승이나 계정 동기화는 구현 범위에 포함하지 않았다. Docker 실행 검사는 로컬 Docker가 없어 GitHub Actions에서 수행했다. 커밋 `d060cec`에서 이미지 빌드·실제 엔진 전투·볼륨 재연결·읽기 전용 이미지의 HTTP 기동·정상 종료가 통과했다. 같은 실행의 Python 검사는 최신 FastAPI에서 제거된 검사 코드의 startup 메서드 때문에 실패하여 lifespan 컨텍스트로 수정했다. 최종 변경을 포함한 원격 검사 결과는 아래에 기록한다.

Windows 로컬 CPU에서 정상 행동 100회의 Python↔Node 왕복 지연은 중앙값 4.500ms, 95백분위 6.634ms였다. 최초 worker 기동은 469ms, warm 경기 생성 중앙값은 120ms였으며 관측한 Node 최대 Working Set은 167.45MiB였다. HTTP·브라우저·네트워크와 동시 접속 부하는 제외한 값이다. 환경과 측정 범위는 [성능 기록](research/engine-performance-20261027.json)에 있다.

최종 원격 검사는 커밋 `1ad4414`의 [GitHub Actions 실행](https://github.com/dndjd321/FreeFolder/actions/runs/40347136292)에서 성공했다. Ubuntu 24.04·Python 3.11·Node 24.22.1 환경에서 Python 94개, Node 72개 및 의존성 호환 검사가 통과했고, Docker 이미지 빌드·전투 생성·저장 볼륨 복원·읽기 전용 HTTP 기동·정상 종료도 통과했다. Render의 `/api/status`, `/api/engine/status`, `/api/runs/status`가 HTTP 200을 반환했으며 엔진 0.11.11·20노드·30종 풀과 최신 재접속 스크립트 제공을 확인했다.

주요 구현 커밋은 안정화 `1d80bc6`, 엔진 `83ae390`, 서버 판정 UI `5be49c7`, 로그라이크 `2d47850`, AI 학습·평가 `65db4bd`, 배포 구성 `c173351`, 재접속·공유 worker `16c23a5`, 최종 호환 수정 `1ad4414`이다. 실행 안내는 [DEPLOYMENT.md](DEPLOYMENT.md)를 따른다. 운영 저장소의 영속 디스크 연결 여부는 이 검사로 보증하지 않으며, Render에서 배포 후에도 런을 보존하려면 해당 문서의 `FREEFOLDER_SAVE_DIR` 설정이 필요하다.

서버 회귀 검사는 저장소 루트에서 `python -X utf8 -B -m unittest discover -s pokemon_rl/tests -p test_multiplayer.py -v`로 실행한다. 실제 ASGI WebSocket 경로에서 잘못된 메시지 이후 연결 유지, 방 교체·이탈·재입장, 엔트리 선택과 턴 메시지를 검증한다.

프론트 회귀 검사는 `node --test pokemon_rl/tests/frontend_stability.test.cjs`로 실행한다. 원본 게임 함수를 실행해 외부 입력의 텍스트 처리, 날씨·독·트랩 KO와 연속 교체, 교체 시 임시 상태 초기화와 HP·PP·주요 상태 보존을 확인한다.

엔진 설치는 `npm --prefix pokemon_rl/battle_service ci`로 진행한다. 검사는 `node --test pokemon_rl/tests/battle_service.test.cjs`와 `python -X utf8 -B -m unittest discover -s pokemon_rl/tests -p test_battle_service.py -v`로 실행한다. 초기 자원 복원은 고정 버전의 내부 API를 사용하는 단일 어댑터에 격리했다.

로그라이크 검사는 `python -X utf8 -B -m unittest discover -s pokemon_rl/tests -p test_run_service.py -v`와 `node --test pokemon_rl/tests/rogue_frontend.test.cjs`로 실행한다. 실제 엔진 완주 검사는 합법 파티·입력·보상·영입·회복을 사용한다. 승패나 HP를 클라이언트에서 조작하지 않는다. 재배포 뒤에도 런을 보존하려면 `FREEFOLDER_SAVE_DIR`를 영속 디스크에 연결해야 한다.

AI 평가 결과는 `pokemon_rl/training/benchmarks/evaluation-20261026.json`에 있다. 개발 풀 6종과 학습에서 분리한 seed를 사용해 상대별 500경기를 진행했다. 초기 신경망의 승률은 랜덤 38.8%, 위력 우선 17.2%, 휴리스틱 5.6%, 학습 전 모델 68.2%이며 불법 행동은 0건이었다. 현재 휴리스틱을 기본 기준으로 유지하며 신경망을 강한 AI로 표시하지 않는다. 별도 학습 의존성과 명령은 `pokemon_rl/training/README.md`를 따른다.
