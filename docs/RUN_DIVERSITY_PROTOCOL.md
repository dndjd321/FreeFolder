# 다양한 파티·공개 정책 탐험 평가 규약

2026년 10월 29일, 11번 측정 전 고정. 평가 이름은 `run-diversity-v1`이다. QA1–QA6 이후 코드에서 특정 자동 정책과 시작 파티의 탐험 성과를 비교한다. 사람의 완주율이나 기존 3대3 AI 평가 승률을 추정하지 않는다. 기존 [120런 비교](RUN_BALANCE_REPORT.md)·모델·최종 AI 평가 자료를 유지한다.

## 실행 전 고정 조건

- 새 시드 `run-diversity-v1-000`부터 `run-diversity-v1-009`까지 10개.
- 아래 순서의 시작 파티 3개 × 전투 정책 2개 × `normal`, `hard` 순서의 두 모드: 총 **120런**.
- 케이스 순서는 시드 → 파티 → 정책 → 모드. 케이스마다 새 메모리 DB와 새 정책 인스턴스를 쓰고 실제 Showdown worker 하나를 공유한다. 사용한 런 전투와 worker를 종료한다. 운영 저장은 사용하지 않는다.
- 모든 런에 `normal-rescue-v2`를 명시한다. 이 프로필의 효과는 기본 모드 첫 쉼터 부활뿐이며 전략 모드는 과거 규칙과 같다. 콘텐츠는 `roguelike-v1`, 저장 스키마는 1이다.
- 최대 1,000회 공개 서비스 입력. 최초 create는 제외하고 `perform`·명시적 `resume` 시도를 포함하는 기존 `run_case` 경계를 따른다. 전투 관측 `turn > 200`이면 다음 입력 전에 중단한다. 정상 패배·완료·제한 중단·오류·미실행을 구분한다. 오류는 배치를 멈추고 나머지를 미실행으로 집계한다. 제한 중단은 이후 케이스를 계속한다.
- 경제·분기·영입·선두 순서는 기존 `balance.cli.choose_operation`의 `public-economy-v1`을 그대로 사용한다. 필요 HP 65% 미만·PP 35% 미만, 기절 시 제시된 영입/부활 우선, 첫 분기·첫 후보·낮은 현재 절대 HP 슬롯 교체, 재추첨 없음. 상세 설정과 기존 코드 hash를 원시 보고서에 기록한다.

## 시작 파티

파티의 기술·특성·도구·성격·EV·순서를 바꾸지 않는다. 생략된 IV는 실제 엔진 기본 31이다. 전체 입력 JSON과 hash는 보고서 manifest에 보관한다. 이후 영입은 두 정책 모두 같은 경제 규약을 따른다.

| ID | 시작 순서 | 빌드 출처 |
| --- | --- | --- |
| `recommended-v1` | Charizard · Garchomp · Clefable | 현 콘텐츠의 `balance.cli.recommended_team()` 그대로 |
| `fixture-sustain-v1` | Garchomp · Vaporeon · Scizor | 기존 실제 20구간 회귀의 아래 리터럴, 테스트 함수는 실행 중 import/추출하지 않음 |
| `curated-coverage-v1` | Rotom-Wash · Scizor · Togekiss | 현 `content/roguelike.json` recruit_pool의 빌드 그대로 |

유지력 파티는 모두 레벨 50·Leftovers이며 다음 입력을 고정한다.

| 종 | 특성 · 성격 | EV | 기술 순서 |
| --- | --- | --- | --- |
| Garchomp | Rough Skin · Jolly | Atk252 / Spe252 / SpD4 | Earthquake / Dragon Claw / Poison Jab / Swords Dance |
| Vaporeon | Water Absorb · Modest | HP252 / SpA252 / Def4 | Surf / Ice Beam / Protect / Wish |
| Scizor | Technician · Adamant | HP252 / Atk252 / Def4 | Bullet Punch / Swords Dance / Knock Off / Roost |

## 전투 정책

첫 정책 `public-heuristic-v2`는 기존 `BaselinePolicy('heuristic', seed=0)` 그대로다. 기존 파일과 경제 정책을 수정하지 않는다.

둘째 정책 `public-sustain-v1`은 평가 전용 `balance/policies.py`다. 기존 20구간 회귀의 **전투 판단**을 출발점으로 삼되 합법 마스크와 아래 고정 규약으로 정식 모듈화한다. 회귀의 별도 경제 루프나 진단 코드를 옮기지 않는다.

1. 종료·대기·합법 입력 없음은 행동 없음. 강제 교체에서는 합법 벤치의 matchup 점수가 가장 큰 슬롯.
2. 자발 교체가 합법이고 최고 벤치 점수가 현재의 **2배 초과**일 때 교체.
3. 현재 활성 동료의 마지막 자기 기술이 Wish이고 HP **80% 미만**이면 합법 Protect.
4. HP **55% 미만**이면 합법 Roost/Moonlight 중 기술 인덱스가 빠른 기술, 이어 같은 조건의 Wish.
5. HP **70% 초과**·공격 랭크 **+2 미만**이면 합법 Swords Dance.
6. 나머지는 공격 점수가 가장 큰 합법 기술. 동점은 원래 슬롯/기술 인덱스가 작은 쪽. 0 PP·disabled·기절·현재 활성 슬롯·trapped·강제 교체 제약과 Struggle 예외는 기존 `legal_mask`와 `action_to_command`를 따른다.

공격 점수는 literal basePower × 명중률 × 자속 × 자기 공격/특수공격 × 자기 랭크 배율 × 자기 Technician 보정 × 공개 상대 타입 상성이다. matchup은 최고 공격 점수 × max(0.3, HP비율) ÷ max(0.15, 공개 상대 타입별 위험의 평균)이다. 자기 Water Absorb는 물 위험을 0으로 본다. 공개 상대 종이 Rotom-Wash이면 종의 단일 특성 Levitate라는 사전 지식으로 땅 공격 점수를 0으로 본다. 상대의 실제 비공개 특성을 읽지 않는다.

타입표는 `training.policies.effectiveness`를 사용한다. 예전 fixture의 `env.damage_calc` 표와 일부 다르며, 이 정책의 성과를 fixture의 기존 완주 성과와 동일시하지 않는다. 화상 반감·상대 방어·날씨·우선도·가변 위력 prior는 이 점수식에 추가하지 않는다. 점수는 기술 선택용 근사치이며 실제 피해량이 아니다. 본 평가 결과를 본 뒤 정책·팀·시드·제한을 조정하지 않는다.

## 관측과 기록 경계

공개 서비스의 제시된 선택과 p1 관측만 사용한다. 상대의 미공개 기술·특성·도구·능력치·벤치·checkpoint는 판단/보고서에 사용하지 않는다. HP·PP·재화·승패·DB 단계를 수정하지 않는다.

기존 `run_case`의 journal 수집과 경제 선택을 재사용하고, 공개 서비스 wrapper에서 케이스당 최대 1,000개의 행동 기록을 허용 목록으로 만든다. 기록은 구간·단계·턴·연산·제시된 선택 ID/합법 행동·선택한 행동·자기 HP/PP·공개 활성 상대 종/타입·고정 이유 코드로 한정한다. 전투 중 자원은 그 시점의 공개 own view를 사용한다. 원본 run/view/request/events 전체나 연산 인자 전체를 덤프하지 않는다.

토큰·생성 secret·run/battle ID·checkpoint·상대 비공개 빌드·예외 전문은 보고서에 넣지 않는다. 오류는 통제된 type/code로 기록한다. 공개 p1 `-heal` 이벤트 개수와 회복기 선택 횟수를 별도로 기록한다. 회복기 선택은 현재 콘텐츠/fixture의 `roost`, `moonlight`, `morningsun`, `synthesis`, `rest`, `wish`를 선택한 횟수다. Protect·흡수 공격(Giga Drain)·Leech Seed·도구의 간접 회복은 이 선택 지표에서 제외하고, 실제 공개 `-heal` 이벤트에는 포함될 수 있다. 이 지표는 기술의 성공이나 회복량을 뜻하지 않는다. journal의 HP/PP 경계 순변화는 **전투 중 누적 피해·총 회복량과 다르다**. 전투별 누적 이벤트 cursor로 재생을 중복 계산하지 않되 같은 내용으로 실제 반복된 회복은 각각 센다.

현재 공개 서비스는 전투 종료 입력에서 바로 보상/패배/완주 단계로 전환해 마지막 battle view를 반환하지 않는다. 따라서 `-heal` 집계는 **관측 가능한 공개 응답의 하한**이며 종료 입력의 회복 이벤트가 누락될 수 있다. 전투→다른 단계 응답에 coverage 표식과 횟수를 남기고 이 제한을 함께 보고한다. 정책 간 총 회복량·총 회복 이벤트 수 비교로 해석하지 않으며, 이를 보충하려고 비공개 worker/checkpoint를 읽거나 게임 API를 변경하지 않는다.

manifest에는 실제 팀 입력/순서/기본 IV, 팀·정책 설정·경제 규약·콘텐츠·관련 코드 hash, 엔진/Node/Python/NumPy 버전과 Git 커밋/작업 트리 상태를 기록한다. 관련 코드는 실행기·두 정책·관측·런 서비스/준비·공개 엔진 API·worker/엔진·도감/기술 데이터·잠금 파일과 이 규약을 포함한다.

## 검증과 본 실행

정책 합법성·임계값·동점·입력 불변·비공개 필드 미사용, 세 파티의 실제 엔진 합법성, 120개 순서/중복, 오류/미실행·제한·정리·출력 덮어쓰기 거부·정보 누출 경계를 검사한다. 별도 `run-diversity-smoke-v1-*` 시드로 입력/실행과 반복 재현만 확인하며 본 10개 시드를 개발 중 성능 선택에 쓰지 않는다.

소스·테스트·규약을 먼저 검사하고 커밋·푸시해 작업 트리를 깨끗하게 만든 뒤 본 120런을 한 번 실행한다. 원시 보고서는 새 경로에 배타적으로 생성한다. 기존 자료를 덮어쓰지 않는다.

```powershell
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
Push-Location pokemon_rl
python -X utf8 -B -m balance.diversity --report ../docs/research/run-diversity-v1-20261029.json
Pop-Location
```

결과는 파티/정책/모드별 도달·실패 구간·회복·PP·상점 접근과 오류/중단을 분리한다. 첫 정예·비 테마 보스의 공개 행동/자원 근거를 확인하되 작은 후반 도달 표본으로 전체 난이도나 인과를 단정하지 않는다. 자동 판단의 한계와 자원/상성 병목을 구분하고 후속 조정은 별도 묶음으로 계획한다.
