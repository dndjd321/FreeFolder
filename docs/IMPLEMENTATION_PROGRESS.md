# 포켓몬 게임 구현 진행

2026년 10월 26일부터 개발 계획에 따라 작업한다. 완료된 작업 묶음은 검증 후 기존 `main` 브랜치에 커밋하고 푸시한다.

| 단계 | 상태 | 산출물과 검증 |
| --- | --- | --- |
| 1 기존 버전 안정화 | 완료 | 공통 멀티플레이 서비스, 외부 입력 검증, 방 이탈 정리, 화면 XSS와 KO·교체 처리 수정. 서버 ASGI 24개와 실제 JS 함수 회귀 검사 13개 통과 |
| 2 엔진 연결 검증 | 로컬 검증 완료 | Showdown 0.11.11 고정, 실제 엔진 9개와 subprocess 3개 검사 통과. 자원 이월·결정적 리플레이·worker 재시작 복원. 실제 호스팅은 6단계에서 확인 |
| 3 판정 통합 | 완료 | 서버 싱글 API와 권위 멀티플레이, 기존 한국어 화면 연결. API·실제 엔진 WebSocket 5개 및 UI 계약 19개 검사와 브라우저 싱글 전투 확인 |
| 4 로그라이크 완주 | 완료 | 20노드·30종·6테마·3보스·특전12종, 보상·분기·상점·휴식·부활·영입·SQLite 저장. 서버 18개(UI 14개) 검사와 실제 엔진 20노드 완주, 브라우저 시작·저장·재개 확인 |
| 5 AI 학습과 평가 | 구현 중 | 공통 엔진 환경, 마스크와 자기 대전, 실제 학습·평가 |
| 6 배포와 밸런스 | 대기 | 실제 화면 검증, 설치·실행, 재접속과 설정 |

서버 회귀 검사는 저장소 루트에서 `python -X utf8 -B -m unittest discover -s pokemon_rl/tests -p test_multiplayer.py -v`로 실행한다. 실제 ASGI WebSocket 경로에서 잘못된 메시지 이후 연결 유지, 방 교체·이탈·재입장, 엔트리 선택과 턴 메시지를 검증한다.

프론트 회귀 검사는 `node --test pokemon_rl/tests/frontend_stability.test.cjs`로 실행한다. 원본 게임 함수를 실행해 외부 입력의 텍스트 처리, 날씨·독·트랩 KO와 연속 교체, 교체 시 임시 상태 초기화와 HP·PP·주요 상태 보존을 확인한다.

엔진 설치는 `npm --prefix pokemon_rl/battle_service ci`로 진행한다. 검사는 `node --test pokemon_rl/tests/battle_service.test.cjs`와 `python -X utf8 -B -m unittest discover -s pokemon_rl/tests -p test_battle_service.py -v`로 실행한다. 초기 자원 복원은 고정 버전의 내부 API를 사용하는 단일 어댑터에 격리했다.

로그라이크 검사는 `python -X utf8 -B -m unittest discover -s pokemon_rl/tests -p test_run_service.py -v`와 `node --test pokemon_rl/tests/rogue_frontend.test.cjs`로 실행한다. 실제 엔진 완주 검사는 합법 파티·입력·보상·영입·회복을 사용한다. 승패나 HP를 클라이언트에서 조작하지 않는다. 재배포 뒤에도 런을 보존하려면 `FREEFOLDER_SAVE_DIR`를 영속 디스크에 연결해야 한다.
