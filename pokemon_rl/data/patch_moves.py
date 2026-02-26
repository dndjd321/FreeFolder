"""
data/patch_moves.py
====================
pokemon.json에 교배기(egg), 가르치기(tutor), TM(machine) 기술이
누락된 경우 PokéAPI에서 해당 기술만 추가합니다.

기존 pokemon.json의 스탯/이름/타입/특성 등은 그대로 유지하고
moves 배열만 업데이트합니다.

전체 재수집(30~90분) 없이 빠르게 교배기 추가 가능.

사용법:
    # 라우드본 한 마리만 테스트
    python data/patch_moves.py --test exploud

    # 전체 포켓몬 기술 목록 업데이트
    python data/patch_moves.py

    # 업데이트 후 HTML 재빌드
    python data/build_html_db.py

소요 시간: 전 세대 약 10~30분 (기술 목록만 수집, species 생략)
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional

BASE = "https://pokeapi.co/api/v2"

ALL_LEARN_METHODS = {"level-up", "machine", "egg", "tutor", "reminder"}
METHOD_ORDER      = {"level-up": 0, "machine": 1, "egg": 2, "tutor": 3, "reminder": 4}


def fetch_json(url: str, retries: int = 4) -> Optional[dict]:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PokemonPatch/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(2 ** attempt)
    return None


def fetch_moves_for_pokemon(pid: int) -> Optional[dict]:
    """포켓몬 ID → 기술 목록 딕셔너리 반환 (스탯 등 불필요한 정보 없이 빠르게)"""
    data = fetch_json(f"{BASE}/pokemon/{pid}")
    if not data:
        return None

    move_info: dict = {}
    for m in data["moves"]:
        mname = m["move"]["name"]
        for vg in m["version_group_details"]:
            method = vg["move_learn_method"]["name"]
            if method not in ALL_LEARN_METHODS:
                continue
            if mname not in move_info:
                move_info[mname] = {"methods": set(), "min_level": 999}
            move_info[mname]["methods"].add(method)
            if method == "level-up":
                lv = vg["level_learned_at"]
                if lv < move_info[mname]["min_level"]:
                    move_info[mname]["min_level"] = lv

    def sort_key(item):
        mname, info = item
        best = min(METHOD_ORDER.get(m, 99) for m in info["methods"])
        level = info["min_level"] if "level-up" in info["methods"] else 0
        return (best, level, mname)

    sorted_moves = sorted(move_info.items(), key=sort_key)
    return {
        "moves":        [mn for mn, _ in sorted_moves],
        "move_methods": {mn: sorted(info["methods"]) for mn, info in move_info.items()},
    }


def main():
    parser = argparse.ArgumentParser(description="pokemon.json 기술 목록 패치")
    parser.add_argument("--dir",   type=str, default="data",
                        help="data 폴더 경로 (기본: data)")
    parser.add_argument("--test",  type=str, default="",
                        help="특정 포켓몬만 테스트 (영문 이름, 예: exploud)")
    parser.add_argument("--delay", type=float, default=0.35,
                        help="API 요청 간격 초 (기본: 0.35)")
    args = parser.parse_args()

    poke_path  = os.path.join(args.dir, "pokemon.json")
    moves_path = os.path.join(args.dir, "moves.json")

    if not os.path.exists(poke_path):
        print(f"[오류] {poke_path} 없음. 먼저 fetch_pokeapi.py를 실행하세요.")
        return

    print(f"[로드] {poke_path} ...")
    with open(poke_path, encoding="utf-8") as f:
        pokemon_db: dict = json.load(f)
    print(f"  → {len(pokemon_db)}마리 로드")

    moves_db = {}
    if os.path.exists(moves_path):
        with open(moves_path, encoding="utf-8") as f:
            moves_db = json.load(f)
        print(f"  → moves.json {len(moves_db)}개 로드")

    # ── 테스트 모드: 단일 포켓몬만 ────────────────────────
    if args.test:
        name = args.test.lower()
        if name not in pokemon_db:
            print(f"[오류] '{name}' 을(를) pokemon.json에서 찾을 수 없습니다.")
            print(f"  사용 가능한 이름 예시: {list(pokemon_db.keys())[:5]}")
            return

        p = pokemon_db[name]
        pid = p["id"]
        print(f"\n[테스트] {p.get('name_ko', name)} (#{pid}) 기술 목록 조회 중...")

        result = fetch_moves_for_pokemon(pid)
        if not result:
            print("[실패] API 오류")
            return

        old_moves = set(p.get("moves", []))
        new_moves = set(result["moves"])

        added = new_moves - old_moves
        removed = old_moves - new_moves

        print(f"\n  기존 기술 수: {len(old_moves)}개")
        print(f"  신규 기술 수: {len(new_moves)}개")
        print(f"  추가될 기술:  {len(added)}개")
        print(f"  제거될 기술:  {len(removed)}개")

        # 학습 방법별 분류
        methods_new = result["move_methods"]
        egg_moves   = [m for m, ms in methods_new.items() if "egg" in ms and m in added]
        tm_moves    = [m for m, ms in methods_new.items() if "machine" in ms and m in added]
        tutor_moves = [m for m, ms in methods_new.items() if "tutor" in ms and m in added]

        def ko(mn):
            return moves_db.get(mn, {}).get("name_ko", mn)

        if egg_moves:
            print(f"\n  🥚 추가될 교배기 ({len(egg_moves)}개):")
            for m in sorted(egg_moves):
                in_moves_db = "✅" if m in moves_db else "⚠️ moves.json 없음"
                print(f"    {in_moves_db} {ko(m)} ({m})")
        if tm_moves:
            print(f"\n  📀 추가될 TM기 ({len(tm_moves)}개):")
            for m in sorted(tm_moves)[:10]:
                print(f"    {ko(m)} ({m})")
            if len(tm_moves) > 10:
                print(f"    ... 외 {len(tm_moves)-10}개")
        if tutor_moves:
            print(f"\n  📖 추가될 가르치기기 ({len(tutor_moves)}개):")
            for m in sorted(tutor_moves):
                print(f"    {ko(m)} ({m})")

        # moves.json에 없는 새 기술 목록
        missing_from_moves_db = [m for m in result["moves"] if m not in moves_db]
        if missing_from_moves_db:
            print(f"\n  ⚠️  moves.json에 없어서 HTML에 추가 안 될 기술 ({len(missing_from_moves_db)}개):")
            for m in missing_from_moves_db[:15]:
                print(f"    {m}")

        print(f"\n→ 실제 패치하려면: python data/patch_moves.py  (--test 없이 실행)")
        return

    # ── 전체 패치 모드 ─────────────────────────────────────
    sorted_pokes = sorted(pokemon_db.values(), key=lambda p: p.get("id", 9999))
    total = len(sorted_pokes)

    print(f"\n[패치] 전체 {total}마리 기술 목록 업데이트 시작...")
    print(f"  (레벨업 + TM + 교배기 + 가르치기 + 떠올리기)\n")

    all_new_move_names: set = set()
    updated = 0
    skipped = 0

    for i, p_entry in enumerate(sorted_pokes):
        pid      = p_entry.get("id")
        name_en  = p_entry.get("name_en", "")
        name_ko  = p_entry.get("name_ko", name_en)

        if not pid or not name_en:
            skipped += 1
            continue

        result = fetch_moves_for_pokemon(pid)
        if not result:
            print(f"  [----] #{pid:04d} {name_ko} — API 실패")
            skipped += 1
            time.sleep(args.delay)
            continue

        # 교배기 추가 수 계산
        old_moves = set(p_entry.get("moves", []))
        new_moves = set(result["moves"])
        egg_added = [m for m, ms in result["move_methods"].items()
                     if "egg" in ms and m not in old_moves]

        # 업데이트
        p_entry["moves"]        = result["moves"]
        p_entry["move_methods"] = result["move_methods"]
        pokemon_db[name_en]     = p_entry
        all_new_move_names.update(result["moves"])
        updated += 1

        egg_str = f"  🥚+{len(egg_added)}" if egg_added else ""
        print(f"  [{i+1:4d}/{total}] #{pid:04d} {name_ko:10s} "
              f"기술:{len(new_moves):3d}개{egg_str}")

        # 100마리마다 중간 저장
        if (i + 1) % 100 == 0:
            with open(poke_path, "w", encoding="utf-8") as f:
                json.dump(pokemon_db, f, ensure_ascii=False, indent=2)
            print(f"  [중간저장] {updated}마리 완료...")

        time.sleep(args.delay)

    # 최종 저장
    with open(poke_path, "w", encoding="utf-8") as f:
        json.dump(pokemon_db, f, ensure_ascii=False, indent=2)
    print(f"\n✅ pokemon.json 업데이트 완료: {updated}마리 / 스킵: {skipped}마리")

    # moves.json에서 누락된 기술 수집
    missing_moves = sorted(all_new_move_names - set(moves_db.keys()))
    if missing_moves:
        print(f"\n[기술] 새로 추가된 기술 {len(missing_moves)}개 수집 중...")

        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            from fetch_pokeapi import fetch_move
            for j, mname in enumerate(missing_moves):
                mv = fetch_move(mname)
                if mv:
                    moves_db[mname] = mv
                    print(f"  [{j+1:3d}/{len(missing_moves)}] {mname:30s} → {mv['name_ko']}")
                else:
                    print(f"  [{j+1:3d}/{len(missing_moves)}] {mname} — 없음")
                time.sleep(args.delay)

            with open(moves_path, "w", encoding="utf-8") as f:
                json.dump(moves_db, f, ensure_ascii=False, indent=2)
            print(f"\n✅ moves.json 업데이트: {len(moves_db)}개")
        except ImportError:
            print("  [스킵] fetch_pokeapi.py 임포트 실패 - moves.json 수동 업데이트 필요")
    else:
        print("  moves.json: 모든 기술 이미 포함됨 ✅")

    print(f"\n{'='*50}")
    print(f"  패치 완료!")
    print(f"  다음 단계: python data/build_html_db.py")
    print(f"  또는:      run.bat 재실행")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
