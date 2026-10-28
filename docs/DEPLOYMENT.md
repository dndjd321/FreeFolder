# 포켓몬 배틀 게임 실행과 배포

브라우저 게임은 FastAPI 서버와 로컬 Node 전투 worker를 함께 사용한다. 기본 서버에는 PyTorch가 필요하지 않으며, 학습과 신경망 추론은 별도 의존성을 설치한다. 로컬·CI·컨테이너의 전투 엔진은 `pokemon_rl/battle_service/package-lock.json`에 고정된 Pokémon Showdown 0.11.11을 사용한다.

## 실행 경로

| 목적 | 실행 파일 또는 명령 | 화면과 서버 위치 |
| --- | --- | --- |
| 로컬 브라우저 플레이 | `pokemon_rl/run_server.bat` | 로컬 FastAPI와 Node, 기본 `http://127.0.0.1:8765/` |
| PyQt 데스크톱 플레이 | `python app.py` | 앱이 로컬 서버와 내장 브라우저를 실행 |
| 배포 서버에 접속하는 경량 앱 | `python PokemonBattle.py` | pywebview가 코드의 `SERVER_URL`에 접속 |
| Linux 서버 또는 Render | `uvicorn server_deploy:app --host 0.0.0.0 --port "$PORT" --workers 1` | 배포 호스트의 FastAPI와 Node |
| 컨테이너 | 저장소 루트의 `Dockerfile` | Python 3.11과 Node 24가 포함된 서버 |

방·대전 연결은 프로세스 메모리를 사용하므로 서버는 **단일 worker**로 실행한다. 로그라이크 저장은 SQLite에 보관한다. 멀티플레이의 재접속은 같은 서버 프로세스 안에서 지원하며 서버 재배포·재시작을 넘어서 방과 대전 연결을 보존하지는 않는다.

## Windows 로컬 서버 설치

Python 3.11과 Node.js 24를 준비하고, `python --version`, `node --version`, `npm --version`이 같은 터미널에서 동작하는지 확인한다. Python 코드의 최소 버전은 3.10이며 CI와 컨테이너 기준은 3.11이다.

`pokemon_rl` 폴더에서 다음 명령을 실행한다. 가상환경은 이 폴더의 `.venv`에 만든다.

```bat
python -m venv .venv
.venv\Scripts\activate
setup_engine.bat
run_server.bat
```

`setup_engine.bat`는 서버 Python 패키지와 잠긴 Node 의존성을 설치하고 실제 worker 응답을 검사한다. `npm ci --ignore-scripts`를 사용하며 기존 Python 학습 체크포인트를 변환하지 않는다. `run_server.bat`는 설치된 의존성으로 서버를 실행한다. 종료는 해당 터미널에서 Ctrl+C를 누른다.

포트와 바인딩 주소를 바꾸려면 실행 전에 설정한다.

```bat
set PORT=8766
set FREEFOLDER_HOST=127.0.0.1
run_server.bat
```

PyQt 앱을 사용할 때는 같은 가상환경에서 `python -m pip install PyQt6 PyQt6-WebEngine` 후 `python app.py`를 실행한다. pywebview 경량 앱은 `python -m pip install pywebview` 후 `python PokemonBattle.py`를 실행하며, 로컬 서버 대신 코드에 지정된 배포 서버를 사용한다.

## Linux 서버 설치

저장소 루트에서 실행한다. Node.js 24와 Python 3.11은 미리 설치되어 있어야 한다.

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r pokemon_rl/requirements.txt
npm --prefix pokemon_rl/battle_service ci --ignore-scripts --no-audit --no-fund
cd pokemon_rl
PORT=8765 python -m uvicorn server_deploy:app --host 127.0.0.1 --port 8765 --workers 1
```

외부 서비스에서는 플랫폼이 지정한 `PORT`와 `0.0.0.0` 바인딩을 사용한다. WebSocket을 포함한 HTTPS 프록시는 플랫폼 또는 운영 서버에서 제공한다. `/api/engine/status`가 `available: true`와 엔진 버전을 반환하면 Node worker까지 준비된 것이다. `/api/status`는 HTTP 서버 상태이며 엔진 설치 성공을 대신하지 않는다.

## Docker 실행

저장소 루트에서 빌드한다. 다단계 빌드는 고정된 Node 24.22.1 이미지에서 Node 의존성을 설치하고 Python 3.11.17 실행 이미지로 필요한 파일을 복사한다. [Docker 다단계 빌드](https://docs.docker.com/build/building/multi-stage/)

```sh
docker build -t freefolder-game:local .
docker volume create freefolder-saves
docker run --name freefolder-game --publish 127.0.0.1:8765:8765 --mount type=volume,source=freefolder-saves,target=/data freefolder-game:local
```

브라우저에서 `http://127.0.0.1:8765/`를 연다. 실행 이미지에는 브라우저 프로필, 빌드 산출물, 과거 체크포인트, 학습 가중치와 로컬 SQLite 파일이 포함되지 않는다. 기본 컨테이너는 규칙 AI와 로그라이크·멀티플레이를 실행하며 신경망 모드는 별도 PyTorch 패키지와 모델이 필요하다.

이미지의 실행 사용자는 UID/GID 10001이며 `/data`에 쓰기 권한이 있어야 한다. 새 Docker named volume은 이미지의 `/data` 권한을 물려받는다. 기존 bind mount를 사용하는 경우 호스트 디렉터리 권한을 실행 사용자에 맞춘다. 저장을 유지하려면 다음 실행에도 같은 `freefolder-saves` 볼륨을 연결한다. [Docker 볼륨 수명](https://docs.docker.com/engine/storage/volumes/)

`PORT`를 바꾸면 포트 매핑의 컨테이너 포트도 맞춘다.

```sh
docker run --name freefolder-game-alt --env PORT=8080 --publish 127.0.0.1:8766:8080 --mount type=volume,source=freefolder-saves,target=/data freefolder-game:local
```

같은 SQLite 볼륨에 연결한 두 게임 서버를 동시에 실행하지 않는다. 서버를 정상 종료한 뒤 볼륨을 다음 컨테이너에 연결한다.

읽기 전용 root filesystem으로 실행할 때도 `/data`는 쓰기 가능한 볼륨으로 연결한다. 임시 파일은 별도 `/tmp`에 허용한다.

```sh
docker run --name freefolder-game-readonly --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m --publish 127.0.0.1:8765:8765 --mount type=volume,source=freefolder-saves,target=/data freefolder-game:local
```

## Render 설정

기존 Python 서비스는 다음 설정을 사용한다. 저장소 루트 설정이 `pokemon_rl`인 경우의 명령이다.

| 설정 | 값 |
| --- | --- |
| Runtime | Python, Node.js와 npm도 사용 가능해야 함 |
| Root Directory | `pokemon_rl` |
| Build Command | `pip install -r requirements.txt && npm --prefix battle_service ci --ignore-scripts --no-audit --no-fund` |
| Start Command | `uvicorn server_deploy:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health Check Path | `/api/engine/status` |
| 저장 경로 | 영속 디스크의 mount path를 `FREEFOLDER_SAVE_DIR`로 지정 |

`services/bootstrap.py`는 `RENDER=true` 또는 `FREEFOLDER_AUTO_INSTALL_ENGINE=1`일 때 엔진 패키지가 없으면 최초 시작에 `npm ci --ignore-scripts`를 실행한다. 빌드 단계에서 설치해 두면 시작 중 설치를 생략한다. 로컬에서는 이 자동 설치를 기본으로 사용하지 않는다.

Docker 서비스로 새로 설정할 때는 저장소 루트의 `Dockerfile`을 선택하고 health check를 `/api/engine/status`로 설정한다. 저장 경로는 기본 `/data`다. 기존 서비스의 실행 환경 변경과 영속 디스크 추가는 Render 대시보드에서 명시적으로 적용한다.

Render의 기본 파일시스템은 임시 파일시스템이다. 영속 디스크가 없으면 재배포나 재시작 때 서버의 `runs.sqlite3`가 사라질 수 있으며, 브라우저에 런 ID와 토큰이 남아 있어도 해당 런을 복원할 수 없다. 영속 디스크는 유료 서비스에서 제공되며, 지정한 mount path 아래의 파일만 유지된다. 무료 서비스는 이 저장 지속성을 제공하지 않는다. [Render 영속 디스크](https://render.com/docs/disks)

## 저장 데이터와 환경 변수

| 변수 | 기본값과 용도 |
| --- | --- |
| `FREEFOLDER_SAVE_DIR` | 로컬은 `pokemon_rl/runtime`, Docker는 `/data`. `runs.sqlite3` 저장 위치 |
| `PORT` | `8765`. 배포 플랫폼이 제공하면 해당 값 사용 |
| `FREEFOLDER_HOST` | Windows 실행 스크립트의 바인딩 주소, 기본 `127.0.0.1` |
| `FREEFOLDER_AUTO_INSTALL_ENGINE` | Node 의존성이 없을 때 자동 설치 여부. Docker는 `0` |
| `FREEFOLDER_POLICY_MODEL` | 호환 신경망 체크포인트 경로. 로컬 기본은 `training/models/expedition-v2.pt`, 이전 `bootstrap.pt`는 비교용으로 보관 |

로그라이크는 서버의 SQLite 저장과 브라우저에 보관한 런 ID·토큰이 함께 있어야 이어 할 수 있다. 같은 주소와 브라우저 프로필에서 재개한다. 브라우저 데이터 삭제, 다른 도메인 접속 또는 서버 저장 손실은 서로 다른 복구 문제다. 런 토큰은 다른 사람에게 전달하지 않는다.

탐험 변경 API는 최근 조회 응답의 최상위 `run_revision`을 함께 받는다. `battle.revision`은 해당 전투 입력 버전으로 별도 유지된다. 다른 탭이 먼저 진행했거나 변경 요청에 런 버전이 없으면 HTTP 409 `RUN_STALE`을 반환한다. 브라우저는 인증된 `/resume`으로 현재 상태만 다시 읽고 선택을 다시 받는다. 저장은 런 버전과 상태를 같은 SQLite commit으로 기록하며 과거 저장의 누락된 런 버전은 0으로 읽는다. 직접 호출하는 운영·검사 코드는 서버 소유 서비스 메서드를 사용할 수 있다.

최근 탐험의 접근정보는 런별 브라우저 저장 키에 보관하고 구형 목록은 자동 이전한다. 진행 중 10개는 새 생성 제한이며 여러 탭이 동시에 만들어 이를 넘긴 경우에도 이미 발급된 접근정보를 삭제하지 않는다. 목록에서 명시적으로 제거한 기록은 제거 표식으로 보존한다. 브라우저 저장 권한·용량이 부족한 경우 현재 탭의 경고를 확인해야 하며, 이 기능은 서버 DB 보존이나 생성 응답이 소실된 요청의 복구를 대신하지 않는다.

전투 준비의 `party/order`는 현재 슬롯의 순열과 `run_revision`을 받아 팀·자원을 함께 재배열한다. 전투·정예·보스의 준비 단계에서만 허용하며 기절한 선두는 거부한다. 회복 선택의 `preview`는 실제 쉼터·상점 효과를 자원 복사본에 적용한 결과로 HP·PP·상태·부활의 전후값을 제공한다. `preview.available`은 실제 자원 변화 여부이고, 선택 자체의 허용 여부인 `rest_options[].available`과 다르다. 회복량이 0인 쉼터 선택도 다음 구간으로 진행할 수 있다.

탐험 전투의 메모리 캐시는 기본 16개·유휴 300초이며 인증된 활동 때 TTL/LRU로 정리한다. 생성·복원이 같은 한도를 사용하며, 퇴출한 전투는 마지막 SQLite commit의 checkpoint로 복원한다. 별도 정리 타이머는 없고 probe 임시 전투와 다른 게임 모드는 이 런 캐시 한도에 포함하지 않는다. `/api/runs/status`의 `storage.battle_cache`는 비공개 ID 없이 활성 수·한도·유휴 기준을 제공하며 상태 조회만으로 정리나 엔진 기동을 하지 않는다. 메모리 전투를 닫아도 Node의 RSS가 즉시 줄어든다는 보장은 없다.

브라우저는 최근 탐험의 접근정보를 최대 10개 보관한다. 새 탐험을 시작해도 이전 탐험은 목록에 남으며, 진행 중인 탐험이 10개면 목록을 정리하기 전까지 새 탐험을 만들지 않는다. 완료한 기록은 새 자리가 필요할 때 먼저 정리한다. `이 기기 목록에서 제거`는 이 브라우저의 접근정보만 지우며 서버의 런을 삭제하지 않는다.

## 온라인 백업과 저장 복구

`pokemon_rl` 폴더에서 운영 CLI를 실행한다. `inspect`는 원본을 읽기 전용으로 열며, `backup`은 SQLite 온라인 백업 API로 서버가 마지막으로 commit한 일관된 시점을 새 파일에 보관한다. 전투 중에도 백업할 수 있다. 진행 중 전투는 마지막 저장 checkpoint에서 복구한다.

```sh
python -X utf8 -B -m services.save_tools inspect runtime/runs.sqlite3
python -X utf8 -B -m services.save_tools backup runtime/runs.sqlite3 backups/pre-release-20261027.sqlite3
python -X utf8 -B -m services.save_tools inspect backups/pre-release-20261027.sqlite3
```

`FREEFOLDER_SAVE_DIR`로 경로를 바꿨다면 명령의 원본도 해당 디렉터리의 `runs.sqlite3`로 바꾼다. 인자를 생략한 `inspect`는 실제 기본 저장 위치를 읽는다. 보고서에는 무결성·호환성·런 개수·버전만 포함하며 토큰 해시나 private checkpoint는 출력하지 않는다. `compatible: true`는 구조와 버전 메타데이터 검사 결과다. 모든 전투를 엔진에서 재생했다는 뜻은 아니며 `engine_restore_verified`는 `false`로 표시한다. 구버전은 보관용 백업이 가능하지만 현재 버전으로의 복원은 거부한다.

복원은 **새 파일 경로로만** 수행한다. 복원 대상 서버를 정지한 뒤 백업을 검사하고 다음을 실행한다. 기존 파일·같은 파일·심볼릭 링크를 출력으로 지정하면 거부하며 원본을 덮어쓰지 않는다.

```sh
python -X utf8 -B -m services.save_tools restore backups/pre-release-20261027.sqlite3 restored-20261027/runs.sqlite3
```

Windows에서는 새 저장 디렉터리를 지정하고 서버를 다시 실행한다.

```bat
set FREEFOLDER_SAVE_DIR=D:\game-saves\restored-20261027
run_server.bat
```

위 경로는 실제로 복원한 `runs.sqlite3`의 상위 디렉터리로 맞춘다. 같은 브라우저·도메인의 최근 탐험 목록에서 이어하기를 확인한 뒤 이전 원본은 별도 보관한다. 백업 이후의 진행은 백업에 포함되지 않는다. 엔진·콘텐츠 버전이 달라졌다면 해당 버전과 백업을 함께 보관하고 호환 버전으로 복원한다.

게임 서버는 DB 옆의 `.lock` 파일에 OS 임대를 유지한다. 같은 DB를 사용하는 두 서버나 사용 중인 복원 대상은 동시에 열 수 없다. `.lock` 파일이 남아 있는 것만으로 서버가 실행 중이라는 뜻은 아니며, 잠금은 프로세스 종료 시 해제된다. 사용 중인 `.lock` 파일을 삭제하지 않는다. 백업은 원본의 임대와 별개인 읽기 전용 연결을 사용한다.

CLI는 게임 서버와 같은 OS 사용자로 실행한다. 새 파일은 비공개 권한으로 생성되므로 Docker에서는 기본 UID 10001로 실행해야 서버가 읽을 수 있다. 도구는 하드링크를 지원하는 파일시스템에서 덮어쓰기 없는 원자적 게시를 사용하며, 지원하지 않는 출력 위치에서는 명시적으로 실패한다. 백업 파일에는 서버의 전체 런 기록이 들어 있으므로 공개 저장소에 올리지 않는다. 동일한 임시 디스크의 백업도 재배포 시 함께 사라질 수 있으므로 보존용 사본은 별도 저장소에 보관한다.

저장 쓰기가 실패하면 API는 HTTP 503을 반환하고 마지막 commit에 포함되지 않은 전투 인스턴스를 버린다. 다음 이어하기에서 저장 checkpoint를 복원하므로 성공하지 않은 입력을 새 상태에 중복 적용하지 않는다. `/api/runs/status`의 `storage`는 접근 가능 여부와 마지막 쓰기 결과만 제공하며 영속 디스크 여부를 보증하지 않는다.

## 학습과 신경망 추론

학습 의존성은 기본 서버와 분리한 `requirements-training.txt`로 설치한다. Windows와 Linux의 CPU 환경은 `pokemon_rl` 폴더에서 다음 명령을 사용한다. PyTorch 공식 CPU 인덱스에는 2.13.0의 Python 3.11용 Linux x86-64 및 Windows x86-64 wheel이 제공된다. [공식 CPU wheel 목록](https://download.pytorch.org/whl/cpu/torch/), [공식 설치 명령](https://pytorch.org/get-started/previous-versions/)

```sh
python -m pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-training.txt
python -X utf8 -B -m training.cli train --steps 2048 --self-play
python -X utf8 -B -m training.cli evaluate --games 500 --model training/runtime/latest.pt
```

서버 신경망 추론에는 NumPy와 PyTorch가 필요하고 Gymnasium은 학습에 사용한다. 기본 Docker 이미지에는 PyTorch와 모델이 없으므로 신경망을 사용하려면 이를 추가한 별도 이미지와 호환 모델 경로를 준비한다. 로컬 기본 `expedition-v2.pt`는 30종 분리 평가에서 이전 bootstrap보다 나은 결과를 낸 실험 모델이다. 규칙 AI 상대 승률은 19.8%이므로 규칙 AI를 계속 권장한다. 이전 bootstrap과 평가 보고서는 보존하며 학습 결과·한계·호환 스키마는 [학습 안내](../pokemon_rl/training/README.md)에 기록한다.

## 자동 검증

`.github/workflows/tests.yml`은 push와 PR에서 Node 24와 Python 3.11을 준비하고 다음을 수행한다. [GitHub의 Python 테스트 안내](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)

1. 잠긴 Node 의존성과 CPU 학습 패키지를 설치한다.
2. Node 전투·프런트엔드 검사와 Python 게임·저장·모델·학습 검사를 실행한다.
3. 기본 서버 이미지를 빌드하고 실제 Node worker로 런 전투를 시작한다.
4. 같은 named volume을 연결한 다음 컨테이너에서 SQLite 저장 유지 여부를 확인한다.
5. 읽기 전용 컨테이너의 HTTP 서버, 엔진 readiness, 로그라이크 상태와 게임 스크립트 제공을 확인한다. 실행 중 DB를 백업하고 새 디렉터리에 복원한 뒤 동일한 런의 전투를 재개한다.
6. 정상 종료 로그, 메모리 강제 종료 여부, 종료 코드 0 또는 SIGTERM에 해당하는 143을 확인한다.

이 workflow는 이미지를 레지스트리에 게시하거나 배포 서비스를 변경하지 않는다. Docker가 설치되지 않은 개발 장치에서는 로컬 이미지 빌드를 확인할 수 없으므로, 컨테이너의 실제 검증 결과는 GitHub Actions의 `container` 작업에서 확인한다. 빌드 설정 파일이 있다는 사실만으로 컨테이너 실행 성공을 판단하지 않는다.
