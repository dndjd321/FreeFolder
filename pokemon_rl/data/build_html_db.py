"""
data/build_html_db.py
======================
fetch_pokeapi.py로 수집한 JSON 파일들을
pokemon_battle_v3.html 안의 POKE_DB에 자동으로 통합합니다.

사용법:
    python data/build_html_db.py
    python data/build_html_db.py --html pokemon_battle_v3.html
    python data/build_html_db.py --min-moves 4   # 기술 4개 이상인 포켓몬만

출력:
    pokemon_battle_v3.html  (원본 덮어쓰기)
    pokemon_battle_v3.html.bak  (백업)
"""

import argparse
import json
import os
import re
import shutil
from pathlib import Path

ROOT      = Path(__file__).parent.parent   # pokemon_rl/
DATA_DIR  = ROOT / "data"


# ══════════════════════════════════════════
# 타입 → CSS 클래스 매핑 (HTML에서 쓰는 것과 일치)
# ══════════════════════════════════════════
VALID_TYPES = {
    "normal","fire","water","electric","grass","ice","fighting","poison",
    "ground","flying","psychic","bug","rock","ghost","dragon","dark","steel","fairy",
}


# ══════════════════════════════════════════
# 기술 효과 태그 → HTML 특수 플래그 변환
# ══════════════════════════════════════════
EFFECT_TO_JS = {
    "burn":       "burn",
    "paralysis":  "paralyze",
    "sleep":      "sleep",
    "freeze":     "freeze",
    "poison":     "poison",
    "bad-poison": "toxic",
    "confusion":  "confuse",
    "flinch":     "flinch",
}


def effect_fields(mv: dict) -> dict:
    """기술 딕셔너리 → JS move 객체에 들어갈 extra 필드"""
    extra = {}
    eff = mv.get("effect", "")
    chance = mv.get("effect_chance", 0)
    special = mv.get("special", "")

    if special:
        extra["special"] = special
    if eff in EFFECT_TO_JS:
        extra["effect"] = EFFECT_TO_JS[eff]
        if chance:
            extra["effectChance"] = chance
    elif eff.startswith("boost_") or eff.startswith("drop_"):
        extra["effect"] = eff
    return extra


def build_move_obj(mv: dict) -> dict:
    """moves.json 항목 → HTML JS 객체"""
    obj = {
        "name":  mv["name_ko"],
        "type":  mv["type"],
        "cat":   mv["category"][:1].lower() if mv["category"] in ("physical","special","status") else "s",
        "power": mv["power"],
        "acc":   mv["accuracy"],
        "pp":    mv["pp"],
        "maxPp": mv["pp"],
    }
    # cat 전체 이름으로 통일
    cat_map = {"p": "physical", "s": "special", "s": "status"}
    obj["cat"] = mv["category"]  # keep full

    obj.update(effect_fields(mv))
    return obj


def build_poke_js(p: dict, moves_db: dict, min_moves: int = 2) -> str | None:
    """pokemon.json 항목 → JS POKE_DB 항목 문자열"""

    # 스탯 필드 확인
    stats = p.get("stats", {})
    required = ["hp","attack","defense","sp_attack","sp_defense","speed"]
    if not all(k in stats for k in required):
        return None

    # 타입 필터
    types = [t for t in p.get("types", []) if t in VALID_TYPES]
    if not types:
        return None

    # 기술 목록 구성
    move_objs = []
    for mname in p.get("moves", []):
        if mname not in moves_db:
            continue
        mv = moves_db[mname]
        # 배틀에서 쓸모 있는 기술만 (위력 있거나 상태이상)
        if mv["power"] == 0 and not mv.get("effect") and not mv.get("special"):
            continue
        move_objs.append(build_move_obj(mv))

    if len(move_objs) < min_moves:
        return None

    # 특성 이름 (첫 번째 비-히든)
    ability_name = ""
    for ab in p.get("abilities", []):
        if not ab.get("hidden"):
            ability_name = ab["name"]
            break
    if not ability_name and p.get("abilities"):
        ability_name = p["abilities"][0]["name"]

    # 이름 (한국어 우선)
    name = p.get("name_ko") or p.get("name_en", "???")

    # JS 객체 문자열 생성
    lines = []
    lines.append(f"  {{id:{p['id']},name:{json.dumps(name, ensure_ascii=False)},"
                 f"types:{json.dumps(types)},"
                 f"hp:{stats['hp']},atk:{stats['attack']},def:{stats['defense']},"
                 f"spa:{stats['sp_attack']},spd:{stats['sp_defense']},spe:{stats['speed']},"
                 f"ability:{json.dumps(ability_name, ensure_ascii=False)},")

    move_strs = []
    for m in move_objs:
        parts = [
            f"name:{json.dumps(m['name'], ensure_ascii=False)}",
            f"type:{json.dumps(m['type'])}",
            f"cat:{json.dumps(m['cat'])}",
            f"power:{m['power']}",
            f"acc:{m['acc']}",
            f"pp:{m['pp']}",
            f"maxPp:{m['pp']}",
        ]
        for k in ("effect","effectChance","special"):
            if k in m:
                val = m[k]
                if isinstance(val, str):
                    parts.append(f"{k}:{json.dumps(val)}")
                else:
                    parts.append(f"{k}:{val}")
        move_strs.append("{" + ",".join(parts) + "}")

    lines.append(f"   allMoves:[{','.join(move_strs)}]}}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--html",       type=str, default="pokemon_battle_v3.html",
                        help="수정할 HTML 파일 경로 (기본: pokemon_battle_v3.html)")
    parser.add_argument("--min-moves",  type=int, default=3,
                        help="포함할 최소 기술 수 (기본: 3)")
    parser.add_argument("--gen",        type=str, default="all",
                        help="포함할 세대: 1 / 1-3 / all (기본: all)")
    args = parser.parse_args()

    # ── 파일 로드 ────────────────────────────────────────
    poke_path  = DATA_DIR / "pokemon.json"
    moves_path = DATA_DIR / "moves.json"
    items_path = DATA_DIR / "items.json"
    _p = Path(args.html)
    html_path = _p if _p.is_absolute() else ROOT / args.html

    if not poke_path.exists():
        print(f"[오류] {poke_path} 없음. 먼저 fetch_pokeapi.py 실행하세요.")
        return
    if not moves_path.exists():
        print(f"[오류] {moves_path} 없음. 먼저 fetch_pokeapi.py 실행하세요.")
        return
    if not html_path.exists():
        print(f"[오류] {html_path} 없음.")
        return

    print(f"[로드] pokemon.json ...")
    with open(poke_path, encoding="utf-8") as f:
        pokemon_raw: dict = json.load(f)

    print(f"[로드] moves.json ...")
    with open(moves_path, encoding="utf-8") as f:
        moves_db: dict = json.load(f)

    items_db = {}
    if items_path.exists():
        print(f"[로드] items.json ...")
        with open(items_path, encoding="utf-8") as f:
            items_db = json.load(f)

    # ── 세대 필터 ────────────────────────────────────────
    GEN_RANGES = {
        1:(1,151), 2:(152,251), 3:(252,386), 4:(387,493),
        5:(494,649), 6:(650,721), 7:(722,809), 8:(810,905), 9:(906,1025),
    }
    def in_gen(pid):
        if args.gen == "all":
            return True
        if "-" in args.gen:
            g1, g2 = args.gen.split("-")
            for g in range(int(g1), int(g2)+1):
                s, e = GEN_RANGES.get(g, (0,0))
                if s <= pid <= e:
                    return True
            return False
        g = int(args.gen)
        s, e = GEN_RANGES.get(g, (0,0))
        return s <= pid <= e

    # ── POKE_DB 빌드 ─────────────────────────────────────
    print(f"\n[빌드] POKE_DB 구성 중...")
    poke_entries = []
    skipped = 0

    # ID순 정렬
    sorted_pokes = sorted(pokemon_raw.values(), key=lambda p: p.get("id", 9999))

    for p in sorted_pokes:
        pid = p.get("id", 0)
        if not in_gen(pid):
            continue
        js = build_poke_js(p, moves_db, args.min_moves)
        if js:
            poke_entries.append(js)
        else:
            skipped += 1

    print(f"  포함: {len(poke_entries)}마리  |  스킵 (기술 부족 등): {skipped}마리")

    new_db_str = "const POKE_DB=[\n" + ",\n".join(poke_entries) + "\n];"

    # ── 도구 목록 빌드 ───────────────────────────────────
    item_lines = []
    for k, item in items_db.items():
        item_lines.append(
            f"  {json.dumps(k)}: {{"
            f"name:{json.dumps(item['name_ko'], ensure_ascii=False)},"
            f"effect:{json.dumps(item.get('effect','')[:60])}"
            f"}}"
        )
    new_items_str = "const ITEMS_DB={\n" + ",\n".join(item_lines) + "\n};"

    # ── HTML 백업 ────────────────────────────────────────
    bak_path = str(html_path) + ".bak"
    shutil.copy(str(html_path), bak_path)
    print(f"\n[백업] {bak_path}")

    # ── HTML 수정 ────────────────────────────────────────
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    # POKE_DB 교체
    poke_pattern = re.compile(
        r'const POKE_DB\s*=\s*\[.*?\];',
        re.DOTALL
    )
    if poke_pattern.search(html):
        html = poke_pattern.sub(new_db_str, html)
        print(f"[수정] POKE_DB 교체 완료 ({len(poke_entries)}마리)")
    else:
        # 없으면 </script> 바로 앞에 삽입
        html = html.replace("</script>", new_db_str + "\n</script>", 1)
        print(f"[삽입] POKE_DB 추가 완료 ({len(poke_entries)}마리)")

    # ITEMS_DB 교체 (있으면 교체, 없으면 POKE_DB 뒤에 추가)
    items_pattern = re.compile(r'const ITEMS_DB\s*=\s*\{.*?\};', re.DOTALL)
    if items_pattern.search(html):
        html = items_pattern.sub(new_items_str, html)
        print(f"[수정] ITEMS_DB 교체 완료 ({len(items_db)}개)")
    elif items_db:
        html = html.replace(new_db_str, new_db_str + "\n" + new_items_str)
        print(f"[삽입] ITEMS_DB 추가 완료 ({len(items_db)}개)")

    # GEN_RANGES 업데이트
    gen_js = (
        "const GEN_RANGES=[[1,151],[152,251],[252,386],[387,493],"
        "[494,649],[650,721],[722,809],[810,905],[906,1025]];"
    )
    gen_pattern = re.compile(r'const GEN_RANGES\s*=\s*\[.*?\];', re.DOTALL)
    if gen_pattern.search(html):
        html = gen_pattern.sub(gen_js, html)
        print(f"[수정] GEN_RANGES 9세대로 업데이트")

    # 세대 필터 버튼 추가 (5~9세대)
    old_gen_btns = '<button class="gen-btn" onclick="setGen(4,this)">4세대</button>'
    new_gen_btns = (
        '<button class="gen-btn" onclick="setGen(4,this)">4세대</button>\n'
        '      <button class="gen-btn" onclick="setGen(5,this)">5세대</button>\n'
        '      <button class="gen-btn" onclick="setGen(6,this)">6세대</button>\n'
        '      <button class="gen-btn" onclick="setGen(7,this)">7세대</button>\n'
        '      <button class="gen-btn" onclick="setGen(8,this)">8세대</button>\n'
        '      <button class="gen-btn" onclick="setGen(9,this)">9세대</button>'
    )
    if old_gen_btns in html and "5세대" not in html:
        html = html.replace(old_gen_btns, new_gen_btns)
        print(f"[수정] 세대 필터 버튼 5~9세대 추가")

    # 도구 선택 옵션 업데이트 (ITEMS_DB에서 동적으로 로드하는 코드 삽입)
    if items_db:
        item_options = ""
        for k, item in items_db.items():
            item_options += f'<option value="{k}">{item["name_ko"]}</option>\n'

        old_item_none = '<option value="">없음</option>'
        if old_item_none in html:
            # 기존 도구 옵션 전체를 새 목록으로 교체
            item_sel_pattern = re.compile(
                r'<select class="build-sel" id="itemSelect">.*?</select>',
                re.DOTALL
            )
            new_item_sel = (
                '<select class="build-sel" id="itemSelect">\n'
                '<option value="">없음</option>\n' +
                item_options +
                '</select>'
            )
            if item_sel_pattern.search(html):
                html = item_sel_pattern.sub(new_item_sel, html)
                print(f"[수정] 도구 선택 옵션 {len(items_db)}개로 업데이트")

    # ── 저장 ─────────────────────────────────────────────
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n{'='*50}")
    print(f" ✅ HTML 업데이트 완료!")
    print(f"   파일: {html_path}")
    print(f"   포켓몬: {len(poke_entries)}마리")
    print(f"   도구:   {len(items_db)}개")
    print(f"   백업:   {bak_path}")
    print(f"{'='*50}\n")
    print(f" 이제 run.bat 실행하면 전 세대 포켓몬으로 플레이 가능합니다!")


if __name__ == "__main__":
    main()
