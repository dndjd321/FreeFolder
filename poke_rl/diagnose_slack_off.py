#!/usr/bin/env python3
"""
diagnose_slack_off.py
---------------------
pokemon.json 전체에서 slack-off 현황을 자동 탐색·진단합니다.
--fix 옵션으로 지정한 포켓몬의 기술 목록을 API에서 재수집합니다.

사용법:
    python data/diagnose_slack_off.py                   # 진단만
    python data/diagnose_slack_off.py --fix growlithe arcanine exploud
    python data/diagnose_slack_off.py --fix-all         # 전체 재수집
    python data/diagnose_slack_off.py --fix-all --rebuild
"""

import json, os, sys, time, argparse, subprocess, urllib.request
from typing import Optional

BASE = "https://pokeapi.co/api/v2"
ALL_LEARN_METHODS = {"level-up", "machine", "egg", "tutor", "reminder"}
METHOD_ORDER = {"level-up": 0, "machine": 1, "egg": 2, "tutor": 3, "reminder": 4}


def fetch_json(url):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PokeDiagnose/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except Exception as e:
            if "404" in str(e): return None
            time.sleep(2 ** attempt)
    return None


def get_moves_from_api(pid):
    data = fetch_json(f"{BASE}/pokemon/{pid}")
    if not data: return None, None
    move_info = {}
    for m in data["moves"]:
        mname = m["move"]["name"]
        for vg in m["version_group_details"]:
            method = vg["move_learn_method"]["name"]
            if method not in ALL_LEARN_METHODS: continue
            if mname not in move_info:
                move_info[mname] = {"methods": set(), "min_level": 999}
            move_info[mname]["methods"].add(method)
            if method == "level-up":
                lv = vg["level_learned_at"]
                if lv < move_info[mname]["min_level"]:
                    move_info[mname]["min_level"] = lv

    def sort_key(item):
        mn, info = item
        best = min(METHOD_ORDER.get(mm, 99) for mm in info["methods"])
        level = info["min_level"] if "level-up" in info["methods"] else 0
        return (best, level, mn)

    sorted_moves = sorted(move_info.items(), key=sort_key)
    moves = [mn for mn, _ in sorted_moves]
    methods = {mn: sorted(info["methods"]) for mn, info in move_info.items()}
    return moves, methods


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="data")
    parser.add_argument("--fix", nargs="*", metavar="EN_NAME",
                        help="특정 포켓몬 영문명 지정해서 수정 (예: --fix arcanine exploud)")
    parser.add_argument("--fix-all", action="store_true",
                        help="pokemon.json 전체 재수집")
    parser.add_argument("--rebuild", action="store_true",
                        help="수정 후 build_html_db.py 실행")
    args = parser.parse_args()

    poke_path = os.path.join(args.dir, "pokemon.json")
    if not os.path.exists(poke_path):
        print(f"[오류] {poke_path} 없음")
        return

    with open(poke_path, encoding="utf-8") as f:
        poke_db = json.load(f)

    print("=" * 60)
    print("  slack-off 기술 보유 현황 진단")
    print("=" * 60)

    # ── 전체 자동 탐색 ────────────────────────────────────
    has_slack = []
    no_move_methods = []

    for en, p in sorted(poke_db.items(), key=lambda x: x[1].get("id", 9999)):
        moves = p.get("moves", [])
        methods = p.get("move_methods", {})
        if "slack-off" in moves:
            method_info = methods.get("slack-off", [])
            has_slack.append((en, p.get("name_ko", en), method_info))
        if not methods:
            no_move_methods.append((en, p.get("name_ko", en)))

    print(f"\n[pokemon.json 전체 {len(poke_db)}마리 탐색]")
    print(f"  slack-off 보유: {len(has_slack)}마리")
    if has_slack:
        for en, ko, method in has_slack:
            print(f"  ✅ {ko}({en}): 학습방법={method}")
    else:
        print("  → 없음. PokéAPI 기준 slack-off 레벨업 습득: 게을마·게을킹·히포포타스·하마돈")

    if no_move_methods:
        print(f"\n  move_methods 없는 구버전 포켓몬: {len(no_move_methods)}마리")
        for en, ko in no_move_methods[:5]:
            print(f"  ⚠️  {ko}({en})")
        if len(no_move_methods) > 5:
            print(f"  ... 외 {len(no_move_methods)-5}마리")

    # 주요 포켓몬 직접 확인
    print("\n[주요 포켓몬 slack-off 여부]")
    check_list = [
        "growlithe", "arcanine", "whismur", "loudred", "exploud",
        "slakoth", "slaking", "hippopotas", "hippowdon",
    ]
    for en in check_list:
        if en not in poke_db: continue
        p = poke_db[en]
        has = "slack-off" in p.get("moves", [])
        method_info = p.get("move_methods", {}).get("slack-off", [])
        print(f"  {'✅' if has else '❌'} {p.get('name_ko', en)}({en})"
              f"{': ' + str(method_info) if has else ''}")

    print("\n[원인 분석]")
    has_methods_field = any("move_methods" in p for p in poke_db.values())
    print(f"  move_methods 필드: {'신버전 fetch ✅' if has_methods_field else '구버전 fetch ❌'}")
    print("  ★ PokéAPI 기준: growlithe·arcanine·whismur·loudred·exploud 는")
    print("     어떤 게임에서도 slack-off를 배울 수 없습니다 (실제 게임 데이터)")
    print("     → '게으름피우기'가 보였다면 게을마·게을킹·히포포타스·하마돈 계열일 수 있음")

    # ── 수정 모드 ────────────────────────────────────────
    if args.fix is not None or args.fix_all:
        if args.fix_all:
            fix_targets = list(poke_db.keys())
        else:
            fix_targets = args.fix if args.fix else []
            # 지정 안 했으면 move_methods 없는 구버전 포켓몬만
            if not fix_targets:
                fix_targets = [en for en, _ in no_move_methods]

        if not fix_targets:
            print("\n수정할 포켓몬 없음")
            return

        print(f"\n{'=' * 60}")
        print(f"  수정 모드: {len(fix_targets)}마리 API 재수집")
        print(f"{'=' * 60}")

        updated = 0
        total = len(fix_targets)
        sorted_targets = sorted(
            [(en, poke_db[en]) for en in fix_targets if en in poke_db],
            key=lambda x: x[1].get("id", 9999)
        )

        for i, (en, p) in enumerate(sorted_targets):
            pid = p.get("id")
            ko = p.get("name_ko", en)
            new_moves, new_methods = get_moves_from_api(pid)
            if new_moves is None:
                print(f"  [----] #{pid} {ko} — API 실패")
                time.sleep(0.4)
                continue

            old_has = "slack-off" in p.get("moves", [])
            new_has = "slack-off" in new_moves

            p["moves"] = new_moves
            p["move_methods"] = new_methods
            poke_db[en] = p
            updated += 1

            change = ""
            if not old_has and new_has:
                change = f"  ★ slack-off 추가! ({new_methods.get('slack-off',[])})"
            elif old_has and not new_has:
                change = "  ※ slack-off 제거됨 (실제로 못 배우는 기술)"

            print(f"  [{i+1:4d}/{total}] #{pid:04d} {ko:10s} {len(new_moves)}개{change}")

            if (i + 1) % 100 == 0:
                with open(poke_path, "w", encoding="utf-8") as f:
                    json.dump(poke_db, f, ensure_ascii=False, indent=2)
                print(f"  [중간저장] {updated}마리...")

            time.sleep(0.35)

        with open(poke_path, "w", encoding="utf-8") as f:
            json.dump(poke_db, f, ensure_ascii=False, indent=2)
        print(f"\n✅ pokemon.json 저장 완료 ({updated}마리)")

        if args.rebuild:
            build_script = os.path.join(args.dir, "build_html_db.py")
            # pokemon_rl 루트 기준 HTML 경로
            root = os.path.dirname(os.path.abspath(args.dir))
            html_path = os.path.join(root, "pokemon_battle_v3.html")
            print(f"\n[재빌드] {build_script} --html {html_path}")
            subprocess.run([sys.executable, build_script, "--html", html_path], check=False)
        else:
            print("\n→ run.bat 재실행 또는: python data/build_html_db.py")


if __name__ == "__main__":
    main()
