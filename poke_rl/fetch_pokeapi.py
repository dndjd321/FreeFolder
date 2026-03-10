"""
data/fetch_pokeapi.py
=====================
PokéAPI에서 1~9세대 포켓몬 / 기술 / 도구 전체 데이터를 수집합니다.

사용법:
    python data/fetch_pokeapi.py             # 전 세대 (기본값)
    python data/fetch_pokeapi.py --gen 1     # 1세대만
    python data/fetch_pokeapi.py --gen 1-3   # 1~3세대
    python data/fetch_pokeapi.py --gen all   # 전 세대

출력 파일:
    data/pokemon.json   — 포켓몬 전체 (스탯/타입/한국어이름/기술목록)
    data/moves.json     — 기술 전체 (위력/타입/분류/부가효과)
    data/items.json     — 도구 전체 (배틀용 도구만 필터링)

소요 시간: 전 세대 약 30~90분 (API 요청 제한 준수)

[변경사항 v2]
  - 레벨업 외에 TM/교배기/떠올리기/가르치기 기술 전체 수집
  - 기술 수 상한 제거 (상위 60개 → 전체)
  - 학습 방법별 정렬: 레벨업(레벨순) → TM → 교배기 → 가르치기 → 떠올리기
  - fetch_move: 회복/방어/스크린/필드/유틸리티 기술 special 태그 추가
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional

BASE = "https://pokeapi.co/api/v2"

GEN_RANGES = {
    1: (1,   151),
    2: (152, 251),
    3: (252, 386),
    4: (387, 493),
    5: (494, 649),
    6: (650, 721),
    7: (722, 809),
    8: (810, 905),
    9: (906, 1025),
}

# 수집할 기술 학습 방법 전체
ALL_LEARN_METHODS = {
    "level-up",   # 레벨업
    "machine",    # TM / HM
    "egg",        # 교배기
    "tutor",      # 가르치기 (떠올리기 포함)
    "reminder",   # 떠올리기 (일부 게임)
}

# 배틀에서 유효한 도구 카테고리
BATTLE_ITEM_CATEGORIES = {
    "held-items",
    "choice",
    "type-enhancement",
    "species-specific",
    "mega-stones",
    "z-crystals",
    "plates",
    "memories",
    "incense",
    "berries-to-be-eaten",
    "medicine",
    "other",
}

# 학습 방법 정렬 우선순위 (낮을수록 앞)
METHOD_ORDER = {
    "level-up": 0,
    "machine":  1,
    "egg":      2,
    "tutor":    3,
    "reminder": 4,
}

# 회복기 (special: "heal")
HEAL_MOVES = {
    "recover", "roost", "rest", "moonlight", "morning-sun", "synthesis",
    "slack-off", "soft-boiled", "milk-drink", "wish", "heal-order",
    "floral-healing", "jungle-healing", "healing-wish", "lunar-dance",
    "life-dew", "shore-up", "aqua-ring",
}

# 방어기 (special: "protect")
PROTECT_MOVES = {
    "protect", "detect", "endure", "baneful-bunker", "kings-shield",
    "spiky-shield", "obstruct", "burning-bulwark", "silk-trap",
    "max-guard", "wide-guard", "quick-guard", "mat-block",
}

# 강제 교체기 (special: "roar")
ROAR_MOVES = {
    "roar", "whirlwind", "dragon-tail", "circle-throw",
}

# 교체 후 퇴장기 (special: "uturn")
UTURN_MOVES = {
    "u-turn", "volt-switch", "flip-turn", "parting-shot", "baton-pass",
}

# 트릭룸 (special: "trick_room")
TRICKROOM_MOVES = {"trick-room"}

# 스크린 (special: "screen")
SCREEN_MOVES = {
    "reflect", "light-screen", "aurora-veil",
}

# 독뿌리기/스텔스록 등 진입해저드 (special: "hazard")
HAZARD_MOVES = {
    "stealth-rock", "spikes", "toxic-spikes", "sticky-web",
}

# 전천후 (special: "weather")
WEATHER_MOVES = {
    "rain-dance", "sunny-day", "sandstorm", "hail", "snowscape",
}

# 필드 (special: "field")
FIELD_MOVES = {
    "electric-terrain", "grassy-terrain", "misty-terrain", "psychic-terrain",
}

# 기타 배틀 유틸 (special: 이름 그대로)
UTIL_SPECIAL = {
    "substitute": "substitute",
    "encore":     "encore",
    "taunt":      "taunt",
    "belly-drum": "belly_drum",
    "tailwind":   "tailwind",
    "trick":      "trick",
    "switcheroo": "trick",
    "haze":       "haze",
    "defog":      "defog",
}


# ══════════════════════════════════════════
# 네트워크 유틸
# ══════════════════════════════════════════
def fetch_json(url: str, retries: int = 4) -> Optional[dict]:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "PokemonBattleAI/1.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            wait = 2 ** attempt
            print(f"    HTTP {e.code} — {wait}s 후 재시도...")
            time.sleep(wait)
        except Exception as e:
            wait = 2 ** attempt
            print(f"    오류: {e} — {wait}s 후 재시도...")
            time.sleep(wait)
    return None


def get_ko_name(names_list: list, fallback: str) -> str:
    for n in names_list:
        if n.get("language", {}).get("name") == "ko":
            return n["name"]
    return fallback


# ══════════════════════════════════════════
# 포켓몬 수집
# ══════════════════════════════════════════
def fetch_pokemon(pid: int) -> Optional[dict]:
    data = fetch_json(f"{BASE}/pokemon/{pid}")
    if not data:
        return None

    name_en = data["name"]

    # 한국어 이름은 species에서
    species = fetch_json(f"{BASE}/pokemon-species/{pid}")
    name_ko = name_en
    if species:
        name_ko = get_ko_name(species.get("names", []), name_en)

    # 타입
    types = [t["type"]["name"] for t in data["types"]]

    # 스탯
    stat_map = {
        "hp": "hp", "attack": "attack", "defense": "defense",
        "special-attack": "sp_attack", "special-defense": "sp_defense", "speed": "speed",
    }
    stats = {}
    for entry in data["stats"]:
        k = entry["stat"]["name"]
        if k in stat_map:
            stats[stat_map[k]] = entry["base_stat"]

    # 특성 (비히든 먼저, 히든 뒤에)
    abilities = []
    non_hidden = []
    hidden_abs = []
    for a in data["abilities"]:
        entry = {
            "name":   a["ability"]["name"],
            "hidden": a["is_hidden"],
        }
        if a["is_hidden"]:
            hidden_abs.append(entry)
        else:
            non_hidden.append(entry)
    abilities = non_hidden + hidden_abs

    # ─────────────────────────────────────────────────────
    # 기술 목록 수집: 레벨업 + TM + 교배기 + 가르치기 + 떠올리기
    # ─────────────────────────────────────────────────────
    # move_info: { mname: {"methods": set(), "level": int} }
    move_info: dict[str, dict] = {}

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

    # 정렬 기준:
    #   1차) 학습 방법 우선순위 (level-up 먼저)
    #   2차) 레벨업인 경우 레벨 오름차순
    #   3차) 이름 알파벳순
    def sort_key(item):
        mname, info = item
        methods = info["methods"]
        # 가장 높은 우선순위(낮은 값) 방법 선택
        best_method_order = min(METHOD_ORDER.get(m, 99) for m in methods)
        level = info["min_level"] if "level-up" in methods else 0
        return (best_method_order, level, mname)

    sorted_moves = sorted(move_info.items(), key=sort_key)
    move_names = [mname for mname, _ in sorted_moves]

    # 학습 방법 정보도 저장 (build_html_db.py에서 활용 가능)
    move_methods = {
        mname: sorted(info["methods"])
        for mname, info in move_info.items()
    }

    sprite_id = data.get("id", pid)

    return {
        "id":           sprite_id,
        "name_en":      name_en,
        "name_ko":      name_ko,
        "types":        types,
        "stats":        stats,
        "abilities":    abilities,
        "moves":        move_names,
        "move_methods": move_methods,   # 학습 방법 정보 포함
    }


# ══════════════════════════════════════════
# 기술 수집
# ══════════════════════════════════════════
def fetch_move(move_name: str) -> Optional[dict]:
    data = fetch_json(f"{BASE}/move/{move_name}")
    if not data:
        return None

    # 한국어 이름
    name_ko = get_ko_name(data.get("names", []), move_name)

    type_  = data.get("type", {}).get("name", "normal")
    cat    = data.get("damage_class", {}).get("name", "status")
    power  = data.get("power") or 0
    acc    = data.get("accuracy") or 0
    pp     = data.get("pp") or 5
    prio   = data.get("priority", 0)
    effect_chance = data.get("effect_chance") or 0

    # ── 부가효과 분류 ──────────────────────────────────────
    effect_tag = ""
    meta = data.get("meta") or {}
    if meta:
        ailment = (meta.get("ailment") or {}).get("name", "none")
        if ailment not in ("none", "unknown", ""):
            effect_tag = ailment   # burn, paralysis, sleep, freeze, poison 등

    stat_changes = data.get("stat_changes") or []
    if stat_changes and not effect_tag:
        sc = stat_changes[0]
        sname = sc["stat"]["name"].replace("-", "_")
        sname = (sname
                 .replace("special_attack", "sp_attack")
                 .replace("special_defense", "sp_defense"))
        change = sc["change"]
        direction = "boost" if change > 0 else "drop"
        effect_tag = f"{direction}_{sname}_{abs(change)}"

    # ── special 플래그 ─────────────────────────────────────
    # 배틀 엔진에서 특수 처리가 필요한 기술들
    special = ""

    if move_name in ("outrage", "thrash", "petal-dance"):
        special = "outrage"
    elif move_name == "yawn":
        special = "yawn"
    elif move_name in HEAL_MOVES:
        special = "heal"
    elif move_name in PROTECT_MOVES:
        special = "protect"
    elif move_name in ROAR_MOVES:
        special = "roar"
    elif move_name in UTURN_MOVES:
        special = "uturn"
    elif move_name in TRICKROOM_MOVES:
        special = "trick_room"
    elif move_name in SCREEN_MOVES:
        special = "screen"
    elif move_name in HAZARD_MOVES:
        special = "hazard"
    elif move_name in WEATHER_MOVES:
        special = "weather"
    elif move_name in FIELD_MOVES:
        special = "field"
    elif move_name in UTIL_SPECIAL:
        special = UTIL_SPECIAL[move_name]

    # 회복 관련 메타 정보로도 판별 (API 데이터 기반)
    if not special and meta:
        drain = meta.get("drain", 0)
        healing = meta.get("healing", 0)
        if healing > 0 and power == 0:
            special = "heal"

    return {
        "name_en":       move_name,
        "name_ko":       name_ko,
        "type":          type_,
        "category":      cat,
        "power":         power,
        "accuracy":      acc,
        "pp":            pp,
        "priority":      prio,
        "effect":        effect_tag,
        "effect_chance": effect_chance,
        "special":       special,
    }


# ══════════════════════════════════════════
# 도구 수집
# ══════════════════════════════════════════
def fetch_all_items(delay: float) -> dict:
    """배틀에서 사용할 수 있는 도구만 수집"""

    BATTLE_ITEMS = [
        # Choice
        "choice-band", "choice-specs", "choice-scarf",
        # Power boost
        "life-orb", "muscle-band", "wise-glasses",
        "black-belt", "black-glasses", "charcoal", "dragon-fang",
        "hard-stone", "magnet", "metal-coat", "miracle-seed",
        "mystic-water", "never-melt-ice", "poison-barb", "sharp-beak",
        "silk-scarf", "silver-powder", "soft-sand", "spell-tag",
        "twisted-spoon", "wave-incense", "rock-incense",
        # Defense / Survival
        "leftovers", "black-sludge", "rocky-helmet",
        "assault-vest", "eviolite", "focus-sash",
        "air-balloon", "shed-shell", "safety-goggles",
        # Status
        "flame-orb", "toxic-orb",
        # Speed
        "iron-ball", "lagging-tail",
        # Berries
        "sitrus-berry", "lum-berry", "chesto-berry",
        "leppa-berry", "oran-berry", "pecha-berry",
        "rawst-berry", "aspear-berry", "cheri-berry",
        "persim-berry", "wiki-berry", "mago-berry",
        "aguav-berry", "iapapa-berry", "figy-berry",
        "salac-berry", "petaya-berry", "apicot-berry",
        "lansat-berry", "starf-berry", "enigma-berry",
        "micle-berry", "custap-berry", "jaboca-berry",
        "rowap-berry", "kee-berry", "maranga-berry",
        "liechi-berry", "ganlon-berry", "shuca-berry",
        "occa-berry", "passho-berry", "wacan-berry",
        "rindo-berry", "yache-berry", "chople-berry",
        "kebia-berry", "payapa-berry", "tanga-berry",
        "charti-berry", "kasib-berry", "haban-berry",
        "colbur-berry", "babiri-berry", "chilan-berry",
        "roseli-berry",
        # Z-crystals
        "normalium-z", "fightinium-z", "flyinium-z",
        "poisonium-z", "groundium-z", "rockium-z",
        "buginium-z", "ghostium-z", "steelium-z",
        "firium-z", "waterium-z", "grassium-z",
        "electrium-z", "psychium-z", "icium-z",
        "dragonium-z", "darkinium-z", "fairium-z",
    ]

    items_db = {}
    print(f"\n[도구] {len(BATTLE_ITEMS)}개 수집 시작...")

    for i, item_name in enumerate(BATTLE_ITEMS):
        data = fetch_json(f"{BASE}/item/{item_name}")
        if not data:
            print(f"  [{i+1:3d}/{len(BATTLE_ITEMS)}] {item_name} — 없음 (스킵)")
            time.sleep(delay)
            continue

        name_ko = get_ko_name(data.get("names", []), item_name)

        effect_text = ""
        for e in data.get("effect_entries", []):
            if e.get("language", {}).get("name") == "en":
                effect_text = e.get("short_effect", "")[:120]
                break

        category = (data.get("category") or {}).get("name", "")

        items_db[item_name] = {
            "name_en":  item_name,
            "name_ko":  name_ko,
            "category": category,
            "effect":   effect_text,
        }
        print(f"  [{i+1:3d}/{len(BATTLE_ITEMS)}] {item_name:30s} → {name_ko}")
        time.sleep(delay)

    return items_db


# ══════════════════════════════════════════
# 세대 범위 파싱
# ══════════════════════════════════════════
def parse_gen(gen_str: str) -> list:
    if gen_str == "all":
        ids = []
        for s, e in GEN_RANGES.values():
            ids.extend(range(s, e + 1))
        return ids
    if "-" in gen_str:
        g1, g2 = gen_str.split("-")
        ids = []
        for g in range(int(g1), int(g2) + 1):
            if g in GEN_RANGES:
                s, e = GEN_RANGES[g]
                ids.extend(range(s, e + 1))
        return ids
    g = int(gen_str)
    if g in GEN_RANGES:
        s, e = GEN_RANGES[g]
        return list(range(s, e + 1))
    return list(range(1, 152))


# ══════════════════════════════════════════
# 진행상황 저장/복원
# ══════════════════════════════════════════
def load_progress(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(path: str, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


# ══════════════════════════════════════════
# 메인
# ══════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="PokéAPI 데이터 수집기 v2")
    parser.add_argument("--gen",        type=str,   default="all",
                        help="세대 선택: 1 / 1-3 / all  (기본: all)")
    parser.add_argument("--output-dir", type=str,   default="data",
                        help="출력 폴더  (기본: data)")
    parser.add_argument("--delay",      type=float, default=0.4,
                        help="API 요청 간격(초)  (기본: 0.4)")
    parser.add_argument("--skip-items", action="store_true",
                        help="도구 수집 건너뛰기")
    parser.add_argument("--skip-moves", action="store_true",
                        help="기술 수집 건너뛰기")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    poke_progress_path  = os.path.join(args.output_dir, "_progress_pokemon.json")
    moves_progress_path = os.path.join(args.output_dir, "_progress_moves.json")
    poke_out  = os.path.join(args.output_dir, "pokemon.json")
    moves_out = os.path.join(args.output_dir, "moves.json")
    items_out = os.path.join(args.output_dir, "items.json")

    pokemon_ids = parse_gen(args.gen)
    total = len(pokemon_ids)

    print(f"\n{'='*60}")
    print(f" PokéAPI 데이터 수집기 v2  (세대: {args.gen})")
    print(f" 포켓몬 {total}마리 | 딜레이 {args.delay}s | 출력: {args.output_dir}/")
    print(f" 수집 기술 방법: 레벨업 + TM + 교배기 + 가르치기 + 떠올리기")
    print(f"{'='*60}\n")

    # ── 포켓몬 수집 ──────────────────────────────────────
    pokemon_db = load_progress(poke_progress_path)
    all_move_names: set = set()
    for p in pokemon_db.values():
        all_move_names.update(p.get("moves", []))

    already_done = set(str(p["id"]) for p in pokemon_db.values())
    remaining = [pid for pid in pokemon_ids if str(pid) not in already_done]

    if remaining:
        print(f"[포켓몬] 수집 시작: {len(remaining)}마리 남음 (이미 {len(pokemon_db)}마리 완료)\n")
        for i, pid in enumerate(remaining):
            poke = fetch_pokemon(pid)
            if poke:
                pokemon_db[poke["name_en"]] = poke
                all_move_names.update(poke["moves"])
                done = len(pokemon_db)
                move_cnt = len(poke["moves"])
                print(f"  [{done:4d}/{total}] #{pid:04d} {poke['name_ko']:12s} "
                      f"({poke['name_en']:20s}) {'/'.join(poke['types'])} | 기술:{move_cnt}개")
            else:
                print(f"  [----/{total}] #{pid:04d} — 없음")

            if (i + 1) % 50 == 0:
                save_progress(poke_progress_path, pokemon_db)
                print(f"   중간 저장 ({len(pokemon_db)}마리)...")

            time.sleep(args.delay)

        save_progress(poke_progress_path, pokemon_db)

    with open(poke_out, "w", encoding="utf-8") as f:
        json.dump(pokemon_db, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 포켓몬 저장 완료: {poke_out}  ({len(pokemon_db)}마리)")
    print(f"   총 고유 기술 수: {len(all_move_names)}개\n")

    # ── 기술 수집 ────────────────────────────────────────
    if not args.skip_moves:
        moves_db = load_progress(moves_progress_path)
        remaining_moves = sorted(all_move_names - set(moves_db.keys()))
        total_moves = len(all_move_names)

        print(f"[기술] 수집 시작: {len(remaining_moves)}개 남음 (이미 {len(moves_db)}개 완료)")
        for i, mname in enumerate(remaining_moves):
            mv = fetch_move(mname)
            if mv:
                moves_db[mname] = mv
                cat_char = {"physical": "물", "special": "특", "status": "변"}.get(mv["category"], "?")
                special_tag = f" [{mv['special']}]" if mv['special'] else ""
                print(f"  [{len(moves_db):4d}/{total_moves}] {mname:30s} "
                      f"[{cat_char}] pwr:{mv['power']:3d}{special_tag}  {mv['name_ko']}")
            else:
                print(f"  [----/{total_moves}] {mname} — 없음")

            if (i + 1) % 100 == 0:
                save_progress(moves_progress_path, moves_db)
                print(f"   중간 저장 ({len(moves_db)}개)...")

            time.sleep(args.delay)

        save_progress(moves_progress_path, moves_db)
        with open(moves_out, "w", encoding="utf-8") as f:
            json.dump(moves_db, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 기술 저장 완료: {moves_out}  ({len(moves_db)}개)")

    # ── 도구 수집 ────────────────────────────────────────
    if not args.skip_items:
        items_db = fetch_all_items(args.delay)
        with open(items_out, "w", encoding="utf-8") as f:
            json.dump(items_db, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 도구 저장 완료: {items_out}  ({len(items_db)}개)")

    # 진행상황 파일 정리
    for f in [poke_progress_path, moves_progress_path]:
        if os.path.exists(f):
            os.remove(f)

    print(f"\n{'='*60}")
    print(f"  수집 완료!")
    print(f"   포켓몬: {len(pokemon_db)}마리  →  {poke_out}")
    if not args.skip_moves:
        print(f"   기술:   {len(moves_db)}개    →  {moves_out}")
    if not args.skip_items:
        print(f"   도구:   {len(items_db)}개    →  {items_out}")
    print(f"\n다음 단계:")
    print(f"   python data/build_html_db.py   # HTML에 DB 통합")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
