"""
data/fetch_pokeapi.py — PokéAPI로 전 세대 포켓몬 데이터 자동 수집

사용법:
  python data/fetch_pokeapi.py --gen 1        # 1세대만
  python data/fetch_pokeapi.py --gen all      # 전 세대 (시간 오래 걸림)
  python data/fetch_pokeapi.py --gen 1-3      # 1~3세대

출력:
  data/pokemon.json   — 포켓몬 스탯/타입/기술 목록
  data/moves.json     — 기술 상세 데이터
"""
import json
import os
import time
import argparse
import urllib.request
import urllib.error
from typing import Optional

BASE_URL = "https://pokeapi.co/api/v2"

# 9세대까지의 공식 번호 범위
GEN_RANGES = {
    1: (1, 151), 2: (152, 251), 3: (252, 386), 4: (387, 493),
    5: (494, 649), 6: (650, 721), 7: (722, 809), 8: (810, 905),
    9: (906, 1025),
}

def fetch_json(url: str, retries: int = 3) -> Optional[dict]:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            time.sleep(1 * (attempt + 1))
    return None

def get_ko_name(resource_list: list) -> str:
    """API 응답 리스트에서 한국어 이름(ko)을 추출"""
    for entry in resource_list:
        if entry.get("language", {}).get("name") == "ko":
            return entry.get("name", "")
    return ""

def parse_stats(stat_list: list) -> dict:
    stat_map = {
        "hp": "hp", "attack": "attack", "defense": "defense",
        "special-attack": "sp_attack", "special-defense": "sp_defense", "speed": "speed",
    }
    return {stat_map[s["stat"]["name"]]: s["base_stat"] for s in stat_list if s["stat"]["name"] in stat_map}

def fetch_pokemon(pokemon_id: int) -> Optional[dict]:
    data = fetch_json(f"{BASE_URL}/pokemon/{pokemon_id}")
    species = fetch_json(f"{BASE_URL}/pokemon-species/{pokemon_id}")
    if not data or not species: return None

    name_en = data["name"]
    name_ko = get_ko_name(species["names"]) or name_en

    # 기술 수집 (레벨업으로 배우는 기술만 필터링하여 부하 감소)
    move_names = list(set([m["move"]["name"] for m in data["moves"] 
                          if any(v["move_learn_method"]["name"] == "level-up" for v in m["version_group_details"])]))

    return {
        "id": pokemon_id,
        "name_en": name_en,
        "name_ko": name_ko,
        "types": [t["type"]["name"] for t in data["types"]],
        "stats": parse_stats(data["stats"]),
        "moves": move_names[:25], # 학습 효율을 위해 주요 기술 25개로 제한
        "sprite": data["sprites"]["front_default"] or "",
    }

def fetch_move(move_name: str) -> Optional[dict]:
    data = fetch_json(f"{BASE_URL}/move/{move_name}")
    if not data: return None

    name_ko = get_ko_name(data["names"]) or move_name
    
    # 부가효과 로직 파싱
    effect_tag = ""
    meta = data.get("meta")
    if meta:
        ailment = meta.get("ailment", {}).get("name", "none")
        if ailment not in ["none", "unknown"]:
            effect_tag = ailment
    
    # 능력치 변화 파싱 (예: boost_attack_1)
    if data.get("stat_changes"):
        sc = data["stat_changes"][0]
        s_name = sc["stat"]["name"].replace("special-", "sp_").replace("-", "_")
        change = sc["change"]
        effect_tag = f"{'boost' if change > 0 else 'drop'}_{s_name}_{abs(change)}"

    return {
        "name_en": move_name,
        "name_ko": name_ko,
        "type": data["type"]["name"],
        "category": data["damage_class"]["name"],
        "power": data["power"] or 0,
        "accuracy": data["accuracy"] or 100,
        "pp": data["pp"] or 35,
        "priority": data["priority"] or 0,
        "effect": effect_tag,
        "effect_chance": data.get("effect_chance") or 0
    }

def get_ids(gen_str: str) -> list[int]:
    if gen_str == "all":
        return [i for start, end in GEN_RANGES.values() for i in range(start, end + 1)]
    if "-" in gen_str:
        s, e = map(int, gen_str.split("-"))
        return [i for g in range(s, e + 1) for i in range(GEN_RANGES[g][0], GEN_RANGES[g][1] + 1)]
    g = int(gen_str)
    return list(range(GEN_RANGES[g][0], GEN_RANGES[g][1] + 1))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gen", type=str, default="1")
    parser.add_argument("--output-dir", type=str, default="data")
    args = parser.parse_args()
    
    os.makedirs(args.output_dir, exist_ok=True)
    target_ids = get_ids(args.gen)
    
    pokemon_db, moves_db, all_moves = {}, {}, set()

    print(f"🚀 {args.gen}세대 데이터 수집 시작...")
    for i, pid in enumerate(target_ids):
        p = fetch_pokemon(pid)
        if p:
            pokemon_db[p["name_en"]] = p
            all_moves.update(p["moves"])
            print(f"[{i+1}/{len(target_ids)}] #{pid:04d} {p['name_ko']} 수집 완료")
        time.sleep(0.1)

    print(f"🔍 기술 데이터 {len(all_moves)}개 수집 중...")
    for i, m_name in enumerate(sorted(all_moves)):
        m = fetch_move(m_name)
        if m:
            moves_db[m_name] = m
            if i % 10 == 0: print(f"기술 수집 진행률: {i}/{len(all_moves)}")
        time.sleep(0.05)

    with open(os.path.join(args.output_dir, "pokemon.json"), "w", encoding="utf-8") as f:
        json.dump(pokemon_db, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.output_dir, "moves.json"), "w", encoding="utf-8") as f:
        json.dump(moves_db, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 완료! data/ 폴더를 확인하세요.")

if __name__ == "__main__":
    main()