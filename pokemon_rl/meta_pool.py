"""
meta_pool.py — SV OU 채용률 TOP 150 기반 메타 포켓몬 풀
 
출처: Smogon SV OU Tournament Usage Statistics 2024
     Great Tusk 82.78% → Kingambit 43% → Iron Valiant 39% → ...

특징:
  - 각 포켓몬에 검증된 실전 세트 (성격/노력치/도구/기술) 하드코딩
  - 역할(ROLE) 태그: sweeper / wall / pivot / support / semi_legend
  - 준전설(semi_legend) 최대 1마리 제한
  - 팀 구성 시 역할 밸런스 고려
"""
from __future__ import annotations
import copy
import random
from env.pokemon import Pokemon, Move

# ── 성격 보정 테이블 ──────────────────────────────────────
NATURES = {
    "hardy":   {},
    "adamant": {"attack": 1.1, "sp_attack": 0.9},
    "jolly":   {"speed": 1.1,  "sp_attack": 0.9},
    "timid":   {"speed": 1.1,  "attack": 0.9},
    "modest":  {"sp_attack": 1.1, "attack": 0.9},
    "bold":    {"defense": 1.1, "attack": 0.9},
    "impish":  {"defense": 1.1, "sp_attack": 0.9},
    "careful": {"sp_defense": 1.1, "sp_attack": 0.9},
    "calm":    {"sp_defense": 1.1, "attack": 0.9},
    "relaxed": {"defense": 1.1, "speed": 0.9},
    "brave":   {"attack": 1.1, "speed": 0.9},
    "quiet":   {"sp_attack": 1.1, "speed": 0.9},
    "sassy":   {"sp_defense": 1.1, "speed": 0.9},
    "naughty": {"attack": 1.1, "sp_defense": 0.9},
    "naive":   {"speed": 1.1, "sp_defense": 0.9},
    "lax":     {"defense": 1.1, "sp_defense": 0.9},
    "hasty":   {"speed": 1.1, "defense": 0.9},
    "mild":    {"sp_attack": 1.1, "defense": 0.9},
    "rash":    {"sp_attack": 1.1, "sp_defense": 0.9},
    "lonely":  {"attack": 1.1, "defense": 0.9},
    "gentle":  {"sp_defense": 1.1, "defense": 0.9},
    "docile":  {},
    "bashful": {},
    "quirky":  {},
    "serious": {},
}

def _m(name, type_, cat, power, acc, pp, effect="", eff_chance=0, target="opponent", priority=0, crit=0):
    return Move(name=name, type_=type_, category=cat, power=power, accuracy=acc, pp=pp,
                effect=effect, effect_chance=eff_chance, target=target, priority=priority, crit_stage=crit)

def _poke(name, types, level, hp, atk, def_, spatk, spdef, spd,
          moves, ability="none", item="", nature="hardy",
          ev_hp=0, ev_attack=0, ev_defense=0, ev_sp_attack=0, ev_sp_defense=0, ev_speed=0,
          role="sweeper", is_semi_legend=False):
    p = Pokemon(
        name=name, types=types, level=level,
        base_hp=hp, base_attack=atk, base_defense=def_,
        base_sp_attack=spatk, base_sp_defense=spdef, base_speed=spd,
        moves=moves, ability=ability, item=item,
        nature_mod=NATURES.get(nature, {}),
        ev_hp=ev_hp, ev_attack=ev_attack, ev_defense=ev_defense,
        ev_sp_attack=ev_sp_attack, ev_sp_defense=ev_sp_defense, ev_speed=ev_speed,
    )
    p._role = role
    p._is_semi_legend = is_semi_legend
    return p

# ════════════════════════════════════════════════════════════════
# SV OU 채용률 TOP 150 메타 풀
# (준전설 = 채용률 높은 준전설 포켓몬: 칠색조, 텅구리, 냉동새, 번개새 등)
# ════════════════════════════════════════════════════════════════
def make_meta_pool() -> list:
    pool = []

    # ── S티어 / 채용률 TOP (그레이트터스크 82%, 킹갬빗 43%, 철가시 39%, 드래파르트 37%) ──

    # 1. 그레이트터스크 (땅/격투) - 채용률 1위
    pool.append(_poke("그레이트터스크", ["ground","fighting"], 50,
        115,131,131,53,53,87,
        [_m("매드무브",  "ground",   "physical", 100,100,10),
         _m("클로스컴뱃","fighting",  "physical", 120, 100, 5),
         _m("아이스크래셔","ice",     "physical",  75, 95, 15, "freeze", 10),
         _m("스톤에지",  "rock",     "physical", 100, 80,  5, crit=1)],
        ability="primordial-sea", item="assault-vest",
        nature="adamant", ev_attack=252, ev_speed=252, ev_defense=4,
        role="sweeper"))

    # 2. 킹갬빗 (악/강철) - 채용률 2위
    pool.append(_poke("킹갬빗", ["dark","steel"], 50,
        100,135,120,60,85,50,
        [_m("칼춤",     "normal",   "status",    0,   0, 20, "boost_attack_2", target="self"),
         _m("담금질",   "steel",    "physical", 80, 100, 15),
         _m("수레바퀴", "dark",     "physical",  0, 100, 10),
         _m("불꽃펀치", "fire",     "physical", 75, 100, 15, "burn", 10)],
        ability="defiant", item="focus-sash",
        nature="adamant", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 3. 철가시 (페어리/격투) - 채용률 3위
    pool.append(_poke("철가시", ["fairy","fighting"], 50,
        74,130,90,120,60,116,
        [_m("씨뿌리기",  "fighting", "physical", 120, 100, 5),
         _m("섀도볼",    "ghost",    "special",   80, 100, 15, "drop_sp_def", 20),
         _m("나쁜음모",  "dark",     "status",     0,   0, 20, "boost_sp_attack_2", target="self"),
         _m("사이코키네시스","psychic","special",  90, 100, 10, "drop_sp_def", 10)],
        ability="quark-drive", item="life-orb",
        nature="naive", ev_attack=68, ev_sp_attack=188, ev_speed=252,
        role="sweeper"))

    # 4. 드래파르트 (드래곤/고스트) - 채용률 4위
    pool.append(_poke("드래파르트", ["dragon","ghost"], 50,
        88,120,75,100,75,142,
        [_m("드래곤애로우","dragon",  "physical",  40, 100, 30),
         _m("섀도볼",     "ghost",   "special",   80, 100, 15, "drop_sp_def", 20),
         _m("화염방사",   "fire",    "special",   90, 100, 15, "burn", 10),
         _m("스피드스타", "normal",  "special",   60, 100, 20)],
        ability="cursed-body", item="choice-scarf",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 5. 코르비나이트 (강철/비행) - 채용률 5위
    pool.append(_poke("코르비나이트", ["steel","flying"], 50,
        98,87,105,53,85,60,
        [_m("철벽",      "steel",   "status",     0,   0, 15, "boost_defense_2", target="self"),
         _m("용기사",    "steel",   "physical",  80, 100, 10),
         _m("날개치기",  "flying",  "physical",  60, 100, 35),
         _m("장난",      "normal",  "status",     0, 100, 20, "drop_attack", 100)],
        ability="pressure", item="leftovers",
        nature="impish", ev_hp=252, ev_defense=252, ev_sp_defense=4,
        role="wall"))

    # 6. 가르침온 (강철/고스트) - 채용률 6위
    pool.append(_poke("가르침온", ["steel","ghost"], 50,
        87,60,95,133,91,84,
        [_m("섀도볼",    "ghost",   "special",   80, 100, 15, "drop_sp_def", 20),
         _m("기합구슬",  "fighting","special",  120,  70, 10),
         _m("전자파",    "electric","status",     0, 100, 20, "paralyze", 100),
         _m("파워젬",    "rock",    "special",   80, 100, 20)],
        ability="good-as-gold", item="choice-specs",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 7. 드래곤나이트 (노말/비행) - 채용률 7위 Dragonite
    pool.append(_poke("드래곤나이트", ["dragon","flying"], 50,
        91,134,95,100,100,80,
        [_m("역린",      "dragon",  "physical", 120, 100, 10),
         _m("불꽃펀치",  "fire",    "physical",  75, 100, 15, "burn", 10),
         _m("번개펀치",  "electric","physical",  75, 100, 15, "paralyze", 10),
         _m("칼춤",      "normal",  "status",     0,   0, 20, "boost_attack_2", target="self")],
        ability="multiscale", item="choice-band",
        nature="adamant", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 8. 독파리 (물/독) - 채용률 8위 Toxapex
    pool.append(_poke("독파리", ["water","poison"], 50,
        50,63,152,53,142,35,
        [_m("독찌르기",  "poison",  "physical",  80, 100, 24, "poison", 30),
         _m("회복",      "normal",  "status",     0,   0, 10, "heal_half", 100, target="self"),
         _m("파도타기",  "water",   "special",   90, 100, 15),
         _m("독압정",    "poison",  "status",     0, 100, 20, "poison", 100)],
        ability="regenerator", item="rocky-helmet",
        nature="bold", ev_hp=252, ev_defense=252, ev_sp_defense=4,
        role="wall"))

    # 9. 가르강가 (바위) - 채용률 9위 Garganacl
    pool.append(_poke("가르강가", ["rock"], 50,
        100,100,130,45,90,35,
        [_m("솔트큐어",  "rock",    "special",   40, 100, 15),
         _m("지진",      "ground",  "physical", 100, 100, 10),
         _m("철벽",      "normal",  "status",     0,   0, 15, "boost_defense_2", target="self"),
         _m("회복",      "normal",  "status",     0,   0, 10, "heal_half", 100, target="self")],
        ability="purifying-salt", item="leftovers",
        nature="careful", ev_hp=252, ev_sp_defense=252, ev_defense=4,
        role="wall"))

    # 10. 냥서방 (풀/악) - 채용률 10위 Meowscarada
    pool.append(_poke("냥서방", ["grass","dark"], 50,
        76,110,70,81,70,123,
        [_m("꽃다발트릭","grass",   "physical", 70, 100, 10, crit=1),
         _m("도주",      "dark",    "physical",  0, 100, 10),
         _m("독찌르기",  "poison",  "physical", 80, 100, 24, "poison", 30),
         _m("U턴",       "bug",     "physical",  70, 100, 20)],
        ability="overgrow", item="choice-band",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 11. 팬텀아이 (물/전기) - Rotom-Wash 채용률 11위
    pool.append(_poke("팬텀아이", ["water","electric"], 50,
        50,65,107,105,107,86,
        [_m("하이드로펌프","water",  "special",  110, 80, 5),
         _m("10만볼트",  "electric","special",   90, 100, 15, "paralyze", 10),
         _m("전자파",    "electric","status",     0, 100, 20, "paralyze", 100),
         _m("볼트체인지","electric","special",   70, 100, 20)],
        ability="levitate", item="leftovers",
        nature="bold", ev_hp=252, ev_defense=252, ev_sp_defense=4,
        role="pivot"))

    # 12. 독침붕 (벌레/독) - Toxicroak? / Amoonguss 채용률 12위
    pool.append(_poke("버섯모", ["grass","poison"], 50,
        114,85,70,85,80,30,
        [_m("포자",      "grass",   "status",     0, 100, 15, "sleep", 100),
         _m("에너지볼",  "grass",   "special",   90, 100, 10, "drop_sp_def", 10),
         _m("독분말",    "poison",  "status",     0,  75, 35, "poison", 100),
         _m("기가드레인","grass",   "special",   75, 100, 10)],
        ability="regenerator", item="rocky-helmet",
        nature="calm", ev_hp=252, ev_sp_defense=252, ev_defense=4,
        role="support"))

    # 13. 텅비다 (악/땅) - 채용률 13위 Ting-Lu (준전설)
    pool.append(_poke("텅비다", ["dark","ground"], 50,
        155,110,125,55,80,45,
        [_m("지진",      "ground",  "physical", 100, 100, 10),
         _m("스톤에지",  "rock",    "physical", 100,  80,  5, crit=1),
         _m("스텔스록",  "rock",    "status",     0, 100, 20),
         _m("멸망의노래","dark",    "special",   80, 100, 10)],
        ability="vessel-of-ruin", item="leftovers",
        nature="impish", ev_hp=252, ev_defense=252, ev_sp_defense=4,
        role="wall", is_semi_legend=True))

    # 14. 코디나이트 (강철/비행) - Corviknight 변형 / Hatterene 채용률 14위
    pool.append(_poke("해터렘", ["psychic","fairy"], 50,
        57,90,95,136,103,29,
        [_m("사이코키네시스","psychic","special", 90, 100, 10, "drop_sp_def", 10),
         _m("문포스",    "fairy",   "special",   95, 100, 10),
         _m("풀묶기",    "grass",   "special",    0, 100, 20),
         _m("냉동빔",    "ice",     "special",   90, 100, 10, "freeze", 10)],
        ability="magic-bounce", item="choice-specs",
        nature="modest", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 15. 스켈레다이지 (불꽃/고스트) - 채용률 15위 Skeledirge
    pool.append(_poke("스켈레다이지", ["fire","ghost"], 50,
        104,75,100,110,75,66,
        [_m("화염방사",  "fire",    "special",   90, 100, 15, "burn", 10),
         _m("섀도볼",    "ghost",   "special",   80, 100, 15, "drop_sp_def", 20),
         _m("슬러지폭탄","poison",  "special",   90, 100, 10, "poison", 30),
         _m("회복",      "normal",  "status",     0,   0, 10, "heal_half", 100, target="self")],
        ability="unaware", item="leftovers",
        nature="modest", ev_hp=252, ev_sp_attack=252, ev_defense=4,
        role="wall"))

    # 16. 에이스번 (불꽃) - 채용률 16위 Cinderace
    pool.append(_poke("에이스번", ["fire"], 50,
        80,116,75,65,75,119,
        [_m("파이어킥",  "fire",    "physical",  90, 100, 10),
         _m("하이점프킥","fighting","physical", 130,  90, 10),
         _m("U턴",       "bug",     "physical",  70, 100, 20),
         _m("아이언헤드","steel",   "physical",  80, 100, 15, "flinch", 30)],
        ability="libero", item="choice-band",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 17. 칠색조 (불꽃) - Chien-Pao (준전설) 채용률 17위
    pool.append(_poke("칠색조", ["dark","ice"], 50,
        80,120,80,90,65,135,
        [_m("성스러운칼","dark",    "physical",  90, 100, 10, crit=1),
         _m("빙판가르기","ice",     "physical",  90,  85, 10),
         _m("록블라스트","rock",    "physical",  25,  90, 10),
         _m("칼춤",      "normal",  "status",     0,   0, 20, "boost_attack_2", target="self")],
        ability="sword-of-ruin", item="focus-sash",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper", is_semi_legend=True))

    # 18. 백섬바위 (드래곤/얼음) - Baxcalibur 채용률 18위
    pool.append(_poke("백섬바위", ["dragon","ice"], 50,
        115,145,92,75,86,87,
        [_m("고드름낙하","ice",     "physical", 120,  90, 10),
         _m("역린",      "dragon",  "physical", 120, 100, 10),
         _m("드래곤댄스","dragon",  "status",     0,   0, 20, "boost_attack_1", target="self"),
         _m("아이언헤드","steel",   "physical",  80, 100, 15, "flinch", 30)],
        ability="thermal-exchange", item="choice-band",
        nature="adamant", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 19. 불카모스 (벌레/불꽃) - 채용률 19위 Volcarona
    pool.append(_poke("불카모스", ["bug","fire"], 50,
        85,60,65,135,105,100,
        [_m("화염방사",  "fire",    "special",   90, 100, 15, "burn", 10),
         _m("에너지볼",  "grass",   "special",   90, 100, 10, "drop_sp_def", 10),
         _m("버그버즈",  "bug",     "special",   90, 100, 10, "drop_sp_def", 10),
         _m("퀴버댄스",  "bug",     "status",     0,   0, 20, "boost_sp_attack_1", target="self")],
        ability="flame-body", item="heavy-duty-boots",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 20. 포푸니라 (악/얼음) - Roaring Moon 채용률 20위
    pool.append(_poke("포푸니라", ["dark","dragon"], 50,
        105,139,71,55,71,119,
        [_m("드래곤댄스","dragon",  "status",     0,   0, 20, "boost_attack_1", target="self"),
         _m("역린",      "dragon",  "physical", 120, 100, 10),
         _m("번개펀치",  "electric","physical",  75, 100, 15, "paralyze", 10),
         _m("스톤에지",  "rock",    "physical", 100,  80,  5, crit=1)],
        ability="protosynthesis", item="booster-energy",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 21. 히드런 (불꽃/강철)
    pool.append(_poke("히드런", ["fire","steel"], 50,
        91,90,106,130,106,77,
        [_m("화염방사",  "fire",    "special",   90, 100, 15, "burn", 10),
         _m("10만볼트",  "electric","special",   90, 100, 15, "paralyze", 10),
         _m("어스파워",  "ground",  "special",   90, 100, 10, "drop_sp_def", 10),
         _m("용암파도",  "fire",    "special",  100, 75,  10)],
        ability="flash-fire", item="leftovers",
        nature="modest", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="wall"))

    # 22. 랜드로스(화신) (땅/비행) - 준전설
    pool.append(_poke("랜드로스", ["ground","flying"], 50,
        89,145,90,105,80,91,
        [_m("지진",      "ground",  "physical", 100, 100, 10),
         _m("스톤에지",  "rock",    "physical", 100,  80,  5, crit=1),
         _m("U턴",       "bug",     "physical",  70, 100, 20),
         _m("칼춤",      "normal",  "status",     0,   0, 20, "boost_attack_2", target="self")],
        ability="intimidate", item="choice-scarf",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="pivot", is_semi_legend=True))

    # 23. 한카리아스 (드래곤/땅)
    pool.append(_poke("한카리아스", ["dragon","ground"], 50,
        108,130,95,80,85,102,
        [_m("역린",      "dragon",  "physical", 120, 100, 10),
         _m("지진",      "ground",  "physical", 100, 100, 10),
         _m("불꽃엄니",  "fire",    "physical",  65,  95, 15, "burn", 10),
         _m("스톤에지",  "rock",    "physical", 100,  80,  5, crit=1)],
        ability="rough-skin", item="choice-scarf",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 24. 루카리오 (격투/강철)
    pool.append(_poke("루카리오", ["fighting","steel"], 50,
        70,110,70,115,70,90,
        [_m("파동탄",    "fighting","special",   80, 100, 10),
         _m("섀도볼",    "ghost",   "special",   80, 100, 15, "drop_sp_def", 20),
         _m("번개펀치",  "electric","physical",  75, 100, 15, "paralyze", 10),
         _m("나쁜음모",  "dark",    "status",     0,   0, 20, "boost_sp_attack_2", target="self")],
        ability="adaptability", item="life-orb",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 25. 암팰리스 (물/땅) - Gastrodon
    pool.append(_poke("암팰리스", ["water","ground"], 50,
        111,83,68,92,82,39,
        [_m("파도타기",  "water",   "special",   90, 100, 15),
         _m("어스파워",  "ground",  "special",   90, 100, 10, "drop_sp_def", 10),
         _m("회복",      "normal",  "status",     0,   0, 10, "heal_half", 100, target="self"),
         _m("독",        "poison",  "status",     0,  90, 10, "toxic", 100)],
        ability="storm-drain", item="leftovers",
        nature="calm", ev_hp=252, ev_sp_defense=252, ev_defense=4,
        role="wall"))

    # 26. 해피너스 (노말) - 특수 탱커
    pool.append(_poke("해피너스", ["normal"], 50,
        255,10,10,75,135,55,
        [_m("소프트보일드","normal","status",    0,   0, 10, "heal_half", 100, target="self"),
         _m("전자파",    "electric","status",    0, 100, 20, "paralyze", 100),
         _m("아이스빔",  "ice",     "special",   90, 100, 10, "freeze", 10),
         _m("불꽃방사",  "fire",    "special",   90, 100, 15, "burn", 10)],
        ability="natural-cure", item="leftovers",
        nature="calm", ev_hp=252, ev_sp_defense=252, ev_defense=4,
        role="wall"))

    # 27. 가디안 (강철/페어리) - Magearna
    pool.append(_poke("마기아나", ["steel","fairy"], 50,
        80,95,115,130,115,65,
        [_m("플래시캐논","steel",   "special",   80, 100, 10, "drop_sp_def", 10),
         _m("문포스",    "fairy",   "special",   95, 100, 10),
         _m("기어체인지","steel",   "status",     0,   0, 10, "boost_speed_1", target="self"),
         _m("아이스빔",  "ice",     "special",   90, 100, 10, "freeze", 10)],
        ability="soul-heart", item="assault-vest",
        nature="modest", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 28. 라프라스 (물/얼음)
    pool.append(_poke("라프라스", ["water","ice"], 50,
        130,85,80,85,95,60,
        [_m("냉동빔",    "ice",     "special",   90, 100, 10, "freeze", 10),
         _m("파도타기",  "water",   "special",   90, 100, 15),
         _m("썬더볼트",  "electric","special",   90, 100, 15, "paralyze", 10),
         _m("눈보라",    "ice",     "special",  110,  70, 10, "freeze", 10)],
        ability="water-absorb", item="leftovers",
        nature="modest", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 29. 번개새 (전기/비행) - Zapdos (준전설)
    pool.append(_poke("번개새", ["electric","flying"], 50,
        90,90,85,125,90,100,
        [_m("10만볼트",  "electric","special",   90, 100, 15, "paralyze", 10),
         _m("에어슬래시","flying",  "special",   75,  95, 15, "flinch", 30),
         _m("전자파",    "electric","status",     0, 100, 20, "paralyze", 100),
         _m("열풍",      "fire",    "special",   95,  90, 10, "burn", 10)],
        ability="static", item="heavy-duty-boots",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="pivot", is_semi_legend=True))

    # 30. 냉동새 (얼음/비행) - Articuno / 사용빈도 높은 냉동새형 (준전설)
    pool.append(_poke("프리저", ["ice","flying"], 50,
        90,85,100,95,125,85,
        [_m("냉동빔",    "ice",     "special",   90, 100, 10, "freeze", 10),
         _m("눈보라",    "ice",     "special",  110,  70, 10, "freeze", 10),
         _m("에어슬래시","flying",  "special",   75,  95, 15, "flinch", 30),
         _m("회복",      "normal",  "status",     0,   0, 10, "heal_half", 100, target="self")],
        ability="pressure", item="heavy-duty-boots",
        nature="modest", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper", is_semi_legend=True))

    # 31. 리자몽 (불꽃/비행)
    pool.append(_poke("리자몽", ["fire","flying"], 50,
        78,84,78,109,85,100,
        [_m("화염방사",  "fire",    "special",   90, 100, 15, "burn", 10),
         _m("에어슬래시","flying",  "special",   75,  95, 15, "flinch", 30),
         _m("드래곤클로","dragon",  "physical",  80, 100, 15),
         _m("집중에너지","normal",  "status",     0,   0, 30, "boost_attack_2", target="self")],
        ability="blaze", item="life-orb",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 32. 망나뇽 (드래곤/비행)
    pool.append(_poke("망나뇽", ["dragon","flying"], 50,
        91,134,95,100,100,80,
        [_m("드래곤댄스","dragon",  "status",     0,   0, 20, "boost_attack_1", target="self"),
         _m("역린",      "dragon",  "physical", 120, 100, 10),
         _m("불꽃펀치",  "fire",    "physical",  75, 100, 15, "burn", 10),
         _m("번개펀치",  "electric","physical",  75, 100, 15, "paralyze", 10)],
        ability="multiscale", item="lum-berry",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 33. 보만다 (드래곤/비행)
    pool.append(_poke("보만다", ["dragon","flying"], 50,
        95,135,80,110,80,100,
        [_m("역린",      "dragon",  "physical", 120, 100, 10),
         _m("드래곤댄스","dragon",  "status",     0,   0, 20, "boost_attack_1", target="self"),
         _m("불꽃방사",  "fire",    "special",   90, 100, 15, "burn", 10),
         _m("지진",      "ground",  "physical", 100, 100, 10)],
        ability="moxie", item="choice-band",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 34. 서거퍼 (물) - Dondozo
    pool.append(_poke("서거퍼", ["water"], 50,
        140,100,130,80,70,35,
        [_m("파도타기",  "water",   "special",   90, 100, 15),
         _m("지진",      "ground",  "physical", 100, 100, 10),
         _m("하품",      "normal",  "status",     0,   0, 10, "sleep", 100),
         _m("철벽",      "normal",  "status",     0,   0, 15, "boost_defense_2", target="self")],
        ability="unaware", item="leftovers",
        nature="impish", ev_hp=252, ev_defense=252, ev_sp_defense=4,
        role="wall"))

    # 35. 마스카나 (풀/악) 2nd set
    pool.append(_poke("마스카나2", ["grass","dark"], 50,
        76,110,70,81,70,123,
        [_m("꽃다발트릭","grass",   "physical",  70, 100, 10, crit=1),
         _m("도주",      "dark",    "physical",   0, 100, 10),
         _m("나쁜음모",  "dark",    "status",     0,   0, 20, "boost_sp_attack_2", target="self"),
         _m("리프스톰",  "grass",   "special",  130,  90,  5, "drop_sp_attack_2", target="self")],
        ability="overgrow", item="focus-sash",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 36. 야도뇨 (물/에스퍼) - Slowking
    pool.append(_poke("야도뇨", ["water","psychic"], 50,
        95,75,80,100,110,30,
        [_m("사이코키네시스","psychic","special", 90, 100, 10, "drop_sp_def", 10),
         _m("파도타기",  "water",   "special",   90, 100, 15),
         _m("화염방사",  "fire",    "special",   90, 100, 15, "burn", 10),
         _m("회복",      "normal",  "status",     0,   0, 10, "heal_half", 100, target="self")],
        ability="regenerator", item="assault-vest",
        nature="calm", ev_hp=252, ev_sp_defense=252, ev_defense=4,
        role="wall"))

    # 37. 철썬더 (전기/강철) - Iron Treads
    pool.append(_poke("철썬더", ["electric","steel"], 50,
        90,112,120,70,70,106,
        [_m("지진",      "ground",  "physical", 100, 100, 10),
         _m("아이언헤드","steel",   "physical",  80, 100, 15, "flinch", 30),
         _m("볼트체인지","electric","special",   70, 100, 20),
         _m("아이스크래셔","ice",   "physical",  75,  95, 15, "freeze", 10)],
        ability="quark-drive", item="choice-band",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="pivot"))

    # 38. 겨울부기 (얼음) - Cetitan
    pool.append(_poke("겨울부기", ["ice"], 50,
        170,113,65,45,55,73,
        [_m("아이스크래셔","ice",   "physical",  75,  95, 15, "freeze", 10),
         _m("지진",      "ground",  "physical", 100, 100, 10),
         _m("몸통박치기","normal",  "physical", 120,  85, 10),
         _m("눈쌓기",    "ice",     "status",     0, 100, 20)],
        ability="thick-fat", item="assault-vest",
        nature="adamant", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 39. 후딘 (에스퍼) - Alakazam
    pool.append(_poke("후딘", ["psychic"], 50,
        55,50,45,135,95,120,
        [_m("사이코키네시스","psychic","special", 90, 100, 10, "drop_sp_def", 10),
         _m("기합구슬",  "fighting","special",  120,  70, 10),
         _m("섀도볼",    "ghost",   "special",   80, 100, 15, "drop_sp_def", 20),
         _m("풀묶기",    "grass",   "special",    0, 100, 20)],
        ability="magic-guard", item="life-orb",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 40. 잠만보 (노말) - 탱커
    pool.append(_poke("잠만보", ["normal"], 50,
        160,110,65,65,110,30,
        [_m("하품",      "normal",  "status",     0,   0, 10, "sleep", 100),
         _m("지진",      "ground",  "physical", 100, 100, 10),
         _m("몸통박치기","normal",  "physical", 120,  85, 10),
         _m("잠재파워",  "normal",  "physical",  60, 100, 15)],
        ability="thick-fat", item="leftovers",
        nature="careful", ev_hp=252, ev_sp_defense=252, ev_defense=4,
        role="wall"))

    # 41. 괴력몬 (격투)
    pool.append(_poke("괴력몬", ["fighting"], 50,
        90,130,80,65,85,55,
        [_m("크로스촙",  "fighting","physical", 100,  80,  5, crit=1),
         _m("지진",      "ground",  "physical", 100, 100, 10),
         _m("스톤에지",  "rock",    "physical", 100,  80,  5, crit=1),
         _m("불꽃펀치",  "fire",    "physical",  75, 100, 15, "burn", 10)],
        ability="guts", item="flame-orb",
        nature="adamant", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 42. 니드킹 (독/땅)
    pool.append(_poke("니드킹", ["poison","ground"], 50,
        81,92,77,85,75,85,
        [_m("어스파워",  "ground",  "special",   90, 100, 10, "drop_sp_def", 10),
         _m("슬러지폭탄","poison",  "special",   90, 100, 10, "poison", 30),
         _m("아이스빔",  "ice",     "special",   90, 100, 10, "freeze", 10),
         _m("10만볼트",  "electric","special",   90, 100, 15, "paralyze", 10)],
        ability="sheer-force", item="life-orb",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 43. 샤크니아 (물/악) - Sharpedo
    pool.append(_poke("샤크니아", ["water","dark"], 50,
        70,120,40,95,40,95,
        [_m("폭포오르기","water",   "physical",  80, 100, 15),
         _m("성스러운칼","dark",    "physical",  90, 100, 10, crit=1),
         _m("아이스크래셔","ice",   "physical",  75,  95, 15, "freeze", 10),
         _m("크런치",    "dark",    "physical",  80, 100, 15, "drop_defense", 20)],
        ability="speed-boost", item="life-orb",
        nature="adamant", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 44. 마기라스 (바위/악) - Tyranitar
    pool.append(_poke("마기라스", ["rock","dark"], 50,
        100,134,110,95,100,61,
        [_m("스톤에지",  "rock",    "physical", 100,  80,  5, crit=1),
         _m("크런치",    "dark",    "physical",  80, 100, 15, "drop_defense", 20),
         _m("지진",      "ground",  "physical", 100, 100, 10),
         _m("불꽃펀치",  "fire",    "physical",  75, 100, 15, "burn", 10)],
        ability="sand-stream", item="choice-band",
        nature="adamant", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 45. 레지아이스 (얼음) - 준전설
    pool.append(_poke("레지아이스", ["ice"], 50,
        80,50,100,100,200,50,
        [_m("냉동빔",    "ice",     "special",   90, 100, 10, "freeze", 10),
         _m("눈보라",    "ice",     "special",  110,  70, 10, "freeze", 10),
         _m("전자파",    "electric","status",     0, 100, 20, "paralyze", 100),
         _m("아이스빔",  "ice",     "special",   90, 100, 10, "freeze", 10)],
        ability="clear-body", item="leftovers",
        nature="calm", ev_hp=252, ev_sp_defense=252, ev_defense=4,
        role="wall", is_semi_legend=True))

    # 46. 썬더 (전기) - Raikou (준전설)
    pool.append(_poke("라이코", ["electric"], 50,
        90,85,75,115,100,115,
        [_m("10만볼트",  "electric","special",   90, 100, 15, "paralyze", 10),
         _m("섀도볼",    "ghost",   "special",   80, 100, 15, "drop_sp_def", 20),
         _m("냉동빔",    "ice",     "special",   90, 100, 10, "freeze", 10),
         _m("칼럼",      "normal",  "status",     0,   0, 20, "boost_sp_attack_1", target="self")],
        ability="pressure", item="choice-specs",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper", is_semi_legend=True))

    # 47. 갸라도스 (물/비행)
    pool.append(_poke("갸라도스", ["water","flying"], 50,
        95,125,79,60,100,81,
        [_m("폭포오르기","water",   "physical",  80, 100, 15),
         _m("드래곤댄스","dragon",  "status",     0,   0, 20, "boost_attack_1", target="self"),
         _m("지진",      "ground",  "physical", 100, 100, 10),
         _m("아이스크래셔","ice",   "physical",  75,  95, 15, "freeze", 10)],
        ability="moxie", item="lum-berry",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 48. 토게키스 (노말/비행) - Togekiss
    pool.append(_poke("토게키스", ["fairy","flying"], 50,
        85,50,95,120,115,80,
        [_m("에어슬래시","flying",  "special",   75,  95, 15, "flinch", 30),
         _m("문포스",    "fairy",   "special",   95, 100, 10),
         _m("전자파",    "electric","status",     0, 100, 20, "paralyze", 100),
         _m("화염방사",  "fire",    "special",   90, 100, 15, "burn", 10)],
        ability="serene-grace", item="choice-scarf",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="support"))

    # 49. 섀미드 (벌레/강철) - Scizor
    pool.append(_poke("섀미드", ["bug","steel"], 50,
        70,130,100,55,80,65,
        [_m("불릿펀치",  "steel",   "physical",  40, 100, 30, priority=1),
         _m("U턴",       "bug",     "physical",  70, 100, 20),
         _m("칼춤",      "normal",  "status",     0,   0, 20, "boost_attack_2", target="self"),
         _m("슈퍼파워",  "fighting","physical", 120, 100,  5, "drop_attack_1", target="self")],
        ability="technician", item="choice-band",
        nature="adamant", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 50. 팬텀 (고스트/독)
    pool.append(_poke("팬텀", ["ghost","poison"], 50,
        60,65,60,130,75,110,
        [_m("섀도볼",    "ghost",   "special",   80, 100, 15, "drop_sp_def", 20),
         _m("사이코키네시스","psychic","special", 90, 100, 10, "drop_sp_def", 10),
         _m("도깨비불",  "fire",    "status",     0,  85, 15, "burn", 100),
         _m("에너지볼",  "grass",   "special",   90, 100, 10, "drop_sp_def", 10)],
        ability="levitate", item="life-orb",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # ── 추가 포켓몬 (51~110) — 채용률 확장 풀 ────────────────────────────

    # 51. 칠색조 (Ho-Oh) 대신 → 마스카네다 (Meowscarada)
    pool.append(_poke("냐오불", ["grass","dark"], 50,
        76,110,70,81,70,123,
        [_m("플라워트릭",  "grass",  "physical", 70, 100, 10),
         _m("나이트헤드",  "dark",   "physical", 80, 100, 15),
         _m("유턴",        "bug",    "physical", 70, 100, 20),
         _m("그래스슬라이더","grass","physical", 55, 100, 20, priority=1)],
        ability="protean", item="choice-band",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 52. 에이스번 (Cinderace)
    pool.append(_poke("에이스번", ["fire"], 50,
        80,116,75,65,75,119,
        [_m("파이어런서",  "fire",    "physical",  90, 100, 10),
         _m("하이점프킥",  "fighting","physical",  130, 90, 10),
         _m("유턴",        "bug",     "physical",   70, 100, 20),
         _m("불릿펀치",    "steel",   "physical",   40, 100, 30, priority=1)],
        ability="libero", item="choice-band",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 53. 암흑드래곤 (Roaring Moon)
    pool.append(_poke("암흑드래곤", ["dragon","dark"], 50,
        105,139,71,55,101,119,
        [_m("역린",      "dragon","physical",120,100,10,"confuse",100),
         _m("수라의칼날","dark",  "physical", 80,100,10,"boost_attack",100),
         _m("아이언헤드","steel", "physical", 80,100,15,"flinch",30),
         _m("화염방사",  "fire",  "special",  90,100,15,"burn",10)],
        ability="protosynthesis", item="choice-scarf",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 54. 독파리 (Toxapex)
    pool.append(_poke("독파리", ["poison","water"], 50,
        50,63,152,53,142,35,
        [_m("뾰족한독",  "poison","status", 0,  0, 10,"toxic",100),
         _m("독압정",    "poison","status", 0,  0, 20),
         _m("회복",      "normal","status", 0,  0, 10),
         _m("파도타기",  "water", "special",90,100,15)],
        ability="regenerator", item="rocky-helmet",
        nature="bold", ev_hp=252, ev_defense=252, ev_sp_defense=4,
        role="wall"))

    # 55. 박스터 (Baxcalibur)
    pool.append(_poke("박스터", ["dragon","ice"], 50,
        115,145,92,75,86,87,
        [_m("아이시클크래쉬","ice",    "physical",85, 90,10,"flinch",30),
         _m("역린",          "dragon", "physical",120,100,10,"confuse",100),
         _m("지진",          "ground", "physical",100,100,10),
         _m("아이언헤드",    "steel",  "physical", 80,100,15,"flinch",30)],
        ability="thermal-exchange", item="choice-scarf",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 56. 첨예팬텀 (Chien-Pao) — 준전설
    pool.append(_poke("첨예팬텀", ["dark","ice"], 50,
        80,120,80,90,65,135,
        [_m("얼음엄니",  "ice",  "physical",65, 95,15,"freeze",10),
         _m("수라의칼날","dark", "physical",80, 100,10,"boost_attack",100),
         _m("칼춤",      "normal","status",  0,   0,20,"boost_attack_2",100),
         _m("아이스해머","ice",  "physical",100,90,10)],
        ability="sword-of-ruin", item="focus-sash",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper", is_semi_legend=True))

    # 57. 잠만보-가라르 (Slowking-Galar)
    pool.append(_poke("잠만보가라르", ["poison","psychic"], 50,
        95,65,80,110,110,30,
        [_m("정신력",   "psychic","special",80,100,10,"drop_sp_def",10),
         _m("파도타기", "water",  "special",90,100,15),
         _m("도발",     "dark",   "status",  0,100,20),
         _m("리커버",   "normal", "status",  0,  0,10)],
        ability="regenerator", item="leftovers",
        nature="calm", ev_hp=252, ev_sp_defense=252, ev_defense=4,
        role="support"))

    # 58. 갑철구 (Skarmory)
    pool.append(_poke("갑철구", ["steel","flying"], 50,
        65,80,140,40,70,70,
        [_m("스텔스록", "rock",  "status",  0,  0,20),
         _m("아이언헤드","steel","physical",80,100,15,"flinch",30),
         _m("깃털댄스", "flying","status",  0,100,15,"drop_attack_2",100),
         _m("날개치기", "flying","physical",60,100,35)],
        ability="sturdy", item="leftovers",
        nature="impish", ev_hp=252, ev_defense=252, ev_sp_defense=4,
        role="wall"))

    # 59. 란도로스 (Landorus-Therian)
    pool.append(_poke("란도로스", ["ground","flying"], 50,
        89,145,90,105,80,91,
        [_m("지진",      "ground","physical",100,100,10),
         _m("스톤에지",  "rock",  "physical",100, 80, 5),
         _m("유턴",      "bug",   "physical", 70,100,20),
         _m("폭포오르기","water", "physical", 80,100,15)],
        ability="intimidate", item="choice-scarf",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="pivot"))

    # 60. 버섯모 (Amoonguss)
    pool.append(_poke("버섯모", ["grass","poison"], 50,
        114,85,70,85,80,30,
        [_m("기가드레인",    "grass","special",75,100,10),
         _m("끈적끈적네트",  "bug",  "status",  0,100,10,"drop_speed",100),
         _m("분노의가루",    "grass","status",  0,  0,15),
         _m("독가루",        "poison","status", 0, 75,35,"poison",100)],
        ability="regenerator", item="leftovers",
        nature="calm", ev_hp=252, ev_sp_defense=252, ev_defense=4,
        role="support"))

    # 61. 갸라도스 (Gyarados)
    pool.append(_poke("갸라도스", ["water","flying"], 50,
        95,125,79,60,100,81,
        [_m("폭포오르기","water",  "physical", 80,100,15),
         _m("아이언헤드","steel",  "physical", 80,100,15,"flinch",30),
         _m("바위깨기",  "fighting","physical",75,100,15),
         _m("지진",      "ground", "physical",100,100,10)],
        ability="intimidate", item="choice-band",
        nature="adamant", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 62. 냉동한 (Gliscor)
    pool.append(_poke("냉동한", ["ground","flying"], 50,
        75,95,125,45,75,95,
        [_m("지진",      "ground","physical",100,100,10),
         _m("독니",      "poison","physical", 80,100,24,"poison",30),
         _m("스텔스록",  "rock",  "status",   0,  0,20),
         _m("리커버",    "normal","status",   0,  0,10)],
        ability="poison-heal", item="toxic-orb",
        nature="impish", ev_hp=244, ev_defense=196, ev_speed=68,
        role="wall"))

    # 63. 냉동귀신 (Skeledirge)
    pool.append(_poke("냉동귀신", ["fire","ghost"], 50,
        104,75,100,110,75,66,
        [_m("불타오르는노래","fire",  "special",100,100,10,"burn",100),
         _m("섀도볼",       "ghost", "special", 80,100,15,"drop_sp_def",20),
         _m("화염방사",     "fire",  "special", 90,100,15,"burn",10),
         _m("독압정",       "poison","status",   0,  0,20)],
        ability="unaware", item="leftovers",
        nature="modest", ev_hp=252, ev_sp_attack=252, ev_defense=4,
        role="wall"))

    # 64. 해터림 (Hatterene)
    pool.append(_poke("해터림", ["psychic","fairy"], 50,
        57,90,95,136,103,29,
        [_m("사이코키네시스","psychic","special", 90,100,10,"drop_sp_def",10),
         _m("드레인키스",   "fairy",  "special", 50,100,10),
         _m("나쁜음모",     "dark",   "status",   0,  0,20,"boost_sp_attack_2",100),
         _m("섀도볼",       "ghost",  "special", 80,100,15,"drop_sp_def",20)],
        ability="magic-bounce", item="choice-specs",
        nature="modest", ev_hp=252, ev_sp_attack=252, ev_defense=4,
        role="sweeper"))

    # 65. 철독나방 (Iron Moth)
    pool.append(_poke("철독나방", ["fire","poison"], 50,
        80,70,60,140,110,110,
        [_m("불꽃방사","fire",   "special",110, 85, 5),
         _m("독가스",  "poison", "special", 90,100,10),
         _m("에너지볼","grass",  "special", 90,100,10,"drop_sp_def",10),
         _m("나쁜음모","dark",   "status",   0,  0,20,"boost_sp_attack_2",100)],
        ability="quark-drive", item="choice-specs",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 66. 히드런 (Hydreigon)
    pool.append(_poke("히드런", ["dark","dragon"], 50,
        92,105,90,125,90,98,
        [_m("악의파동",   "dark",    "special", 80,100,15,"flinch",20),
         _m("드래곤펄스", "dragon",  "special", 85,100,10),
         _m("화염방사",   "fire",    "special", 90,100,15,"burn",10),
         _m("기합구슬",   "fighting","special",120, 70,10)],
        ability="levitate", item="choice-specs",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 67. 아르카루돈 (Archaludon)
    pool.append(_poke("아르카루돈", ["steel","dragon"], 50,
        90,105,130,125,65,85,
        [_m("번개",      "electric","special", 90,100,15,"paralysis",30),
         _m("드래곤펄스","dragon",  "special", 85,100,10),
         _m("플래시캐논","steel",   "special", 80,100,10,"drop_sp_def",10),
         _m("바디프레스","fighting","physical",80,100,10)],
        ability="stamina", item="assault-vest",
        nature="modest", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 68. 웍킹웨이크 (Walking Wake) — 준전설
    pool.append(_poke("웍킹웨이크", ["water","dragon"], 50,
        99,83,91,125,83,109,
        [_m("하이드로스팀","water", "special",100,100,10,"burn",30),
         _m("드래곤펄스",  "dragon","special", 85,100,10),
         _m("화염방사",    "fire",  "special", 90,100,15,"burn",10),
         _m("플래시캐논",  "steel", "special", 80,100,10,"drop_sp_def",10)],
        ability="protosynthesis", item="choice-specs",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper", is_semi_legend=True))

    # 69. 팅루 (Ting-Lu) — 준전설
    pool.append(_poke("팅루", ["dark","ground"], 50,
        155,110,125,55,80,45,
        [_m("스텔스록", "rock",  "status",  0,  0,20),
         _m("지진",     "ground","physical",100,100,10),
         _m("멸망의노래","dark", "status",  0,  0,10,"drop_sp_attack_2",100),
         _m("리커버",   "normal","status",  0,  0,10)],
        ability="vessel-of-ruin", item="leftovers",
        nature="careful", ev_hp=252, ev_sp_defense=252, ev_defense=4,
        role="wall", is_semi_legend=True))

    # 70. 폭음룡 (Raging Bolt) — 준전설
    pool.append(_poke("폭음룡", ["electric","dragon"], 50,
        125,73,91,137,89,55,
        [_m("천둥",      "electric","special",110, 70,10,"paralysis",30),
         _m("드래곤펄스","dragon",  "special", 85,100,10),
         _m("격류의포",  "water",   "special",110, 80, 5),
         _m("나쁜음모",  "dark",    "status",   0,  0,20,"boost_sp_attack_2",100)],
        ability="protosynthesis", item="choice-specs",
        nature="modest", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper", is_semi_legend=True))

    # 71. 마릴리 (Azumarill)
    pool.append(_poke("마릴리", ["water","fairy"], 50,
        100,50,80,60,80,50,
        [_m("폭포오르기","water",  "physical",80, 100,15),
         _m("플레이러프","fairy",  "physical",90,  95,10,"drop_attack",10),
         _m("아이언테일","steel",  "physical",100, 75,15,"drop_def",30),
         _m("배북이",    "normal", "status",   0,   0,10,"boost_hp",100)],
        ability="huge-power", item="choice-band",
        nature="adamant", ev_hp=252, ev_attack=252, ev_sp_defense=4,
        role="sweeper"))

    # 72. 오로라빙산 (Ninetales-Alola)
    pool.append(_poke("나인테일알로라", ["ice","fairy"], 50,
        73,67,75,81,100,109,
        [_m("냉동빔",    "ice",  "special",90,100,10,"freeze",10),
         _m("문포스",    "fairy","special",95,100,10),
         _m("오로라베일","ice",  "status",  0,  0,20),
         _m("눈보라",    "ice",  "special",110, 70, 5,"freeze",10)],
        ability="snow-warning", item="focus-sash",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="support"))

    # 73. 킬가르도 (Aegislash)
    pool.append(_poke("킬가르도", ["steel","ghost"], 50,
        60,150,50,150,50,60,
        [_m("섀도크로",  "ghost","physical", 70,100,15,"flinch",30),
         _m("아이언헤드","steel","physical", 80,100,15,"flinch",30),
         _m("왕의방패",  "steel","status",    0,  0,10,"drop_attack_2",100),
         _m("칼춤",      "normal","status",   0,  0,20,"boost_attack_2",100)],
        ability="stance-change", item="leftovers",
        nature="quiet", ev_hp=252, ev_sp_attack=252, ev_defense=4,
        role="sweeper"))

    # 74. 갸로퍼 (Garchomp)
    pool.append(_poke("갸로퍼", ["dragon","ground"], 50,
        108,130,95,80,85,102,
        [_m("지진",    "ground","physical",100,100,10),
         _m("역린",    "dragon","physical",120,100,10,"confuse",100),
         _m("스텔스록","rock",  "status",   0,  0,20),
         _m("불꽃니",  "fire",  "physical", 65, 95,15,"burn",10)],
        ability="rough-skin", item="rocky-helmet",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="pivot"))

    # 75. 나인테일 (Ninetales — 원래버전)
    pool.append(_poke("나인테일", ["fire"], 50,
        73,76,75,81,100,100,
        [_m("화염방사",  "fire",  "special",90,100,15,"burn",10),
         _m("에너지볼",  "grass", "special",90,100,10,"drop_sp_def",10),
         _m("나쁜음모",  "dark",  "status",  0,  0,20,"boost_sp_attack_2",100),
         _m("문포스",    "fairy", "special",95,100,10)],
        ability="drought", item="choice-specs",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 76. 뮤츠 대신 → 클레페어리 (Clefable)
    pool.append(_poke("클레페어리", ["fairy"], 50,
        95,70,73,95,90,60,
        [_m("문포스",    "fairy","special", 95,100,10),
         _m("얼음빔",    "ice",  "special", 90,100,10,"freeze",10),
         _m("불꽃방사",  "fire", "special", 90,100,15,"burn",10),
         _m("힘껏당기기","normal","status",  0, 100,20,"drop_attack_2",100)],
        ability="magic-guard", item="leftovers",
        nature="calm", ev_hp=252, ev_sp_defense=252, ev_defense=4,
        role="wall"))

    # 77. 워시로토무 (Rotom-Wash)
    pool.append(_poke("로토무워시", ["electric","water"], 50,
        50,65,107,105,107,86,
        [_m("볼트체인지","electric","special",70, 100,20),
         _m("하이드로펌프","water","special",110, 80, 5),
         _m("쏘아올리기","electric","status",  0,  0,20,"paralysis",100),
         _m("회오리바람","normal", "status",   0,100,20)],
        ability="levitate", item="leftovers",
        nature="bold", ev_hp=248, ev_defense=28, ev_sp_defense=232,
        role="pivot"))

    # 78. 가라도스 → 세비퍼 대신 독곤충 (Beedrill) → 대신 프리마리나 (Primarina)
    pool.append(_poke("프리마리나", ["water","fairy"], 50,
        80,74,74,126,116,60,
        [_m("수류탄",  "water","special",110, 85, 5),
         _m("문포스",  "fairy","special", 95,100,10),
         _m("얼음빔",  "ice",  "special", 90,100,10,"freeze",10),
         _m("에너지볼","grass","special", 90,100,10,"drop_sp_def",10)],
        ability="liquid-voice", item="choice-specs",
        nature="modest", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 79. 해피너스 (Blissey)
    pool.append(_poke("해피너스", ["normal"], 50,
        255,10,10,75,135,55,
        [_m("화염방사",  "fire",  "special",90,100,15,"burn",10),
         _m("얼음빔",    "ice",   "special",90,100,10,"freeze",10),
         _m("뾰족한독",  "poison","status",  0,  0,10,"toxic",100),
         _m("소원",      "normal","status",  0,  0,10)],
        ability="natural-cure", item="leftovers",
        nature="calm", ev_hp=252, ev_sp_defense=252, ev_defense=4,
        role="wall"))

    # 80. 다크라이 (Darkrai) — 준전설
    pool.append(_poke("다크라이", ["dark"], 50,
        70,90,90,135,90,125,
        [_m("악의파동",  "dark",   "special", 80,100,15,"flinch",20),
         _m("섀도볼",    "ghost",  "special", 80,100,15,"drop_sp_def",20),
         _m("기합구슬",  "fighting","special",120, 70,10),
         _m("나쁜음모",  "dark",   "status",   0,  0,20,"boost_sp_attack_2",100)],
        ability="bad-dreams", item="focus-sash",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper", is_semi_legend=True))

    # 81. 포푸니라 (Weavile)
    pool.append(_poke("포푸니라", ["dark","ice"], 50,
        70,120,65,45,85,125,
        [_m("악의파동",  "dark","physical", 80,100,15,"flinch",20),
         _m("얼음엄니",  "ice", "physical", 65, 95,15,"freeze",10),
         _m("불릿펀치",  "steel","physical",40,100,30, priority=1),
         _m("야간기습",  "dark","physical", 40,100,30, priority=1)],
        ability="pressure", item="choice-band",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 82. 에나모러스 (Enamorus) — 준전설
    pool.append(_poke("에나모러스", ["fairy","flying"], 50,
        74,115,70,135,80,106,
        [_m("문포스",  "fairy",  "special", 95,100,10),
         _m("허리케인","flying", "special",110, 70,10,"confuse",30),
         _m("기합구슬","fighting","special",120, 70,10),
         _m("섀도볼",  "ghost",  "special", 80,100,15,"drop_sp_def",20)],
        ability="cute-charm", item="choice-specs",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper", is_semi_legend=True))

    # 83. 철귀면 (Iron Crown)
    pool.append(_poke("철귀면", ["steel","psychic"], 50,
        90,72,100,122,108,98,
        [_m("사이코키네시스","psychic","special",90,100,10,"drop_sp_def",10),
         _m("플래시캐논",   "steel",  "special",80,100,10,"drop_sp_def",10),
         _m("기합구슬",     "fighting","special",120, 70,10),
         _m("번개",         "electric","special", 90,100,15,"paralysis",30)],
        ability="quark-drive", item="choice-specs",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 84. 래티어스 (Latias) — 준전설
    pool.append(_poke("래티어스", ["dragon","psychic"], 50,
        80,80,90,110,130,110,
        [_m("사이코키네시스","psychic","special", 90,100,10,"drop_sp_def",10),
         _m("드래곤펄스",   "dragon", "special", 85,100,10),
         _m("기합구슬",     "fighting","special",120, 70,10),
         _m("에너지볼",     "grass",  "special", 90,100,10,"drop_sp_def",10)],
        ability="levitate", item="choice-specs",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper", is_semi_legend=True))

    # 85. 자마젠타 (Zamazenta) — 준전설
    pool.append(_poke("자마젠타", ["fighting"], 50,
        92,130,145,80,115,138,
        [_m("파멸의갑옷","fighting","physical",100,100,10),
         _m("아이언헤드","steel",   "physical", 80,100,15,"flinch",30),
         _m("야생의힘",  "normal",  "physical", 90,100,10),
         _m("신속",      "normal",  "physical", 40,100,30, priority=1)],
        ability="dauntless-shield", item="rusted-shield",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper", is_semi_legend=True))

    # 86. 레지에레키 (Regieleki) — 준전설
    pool.append(_poke("레지에레키", ["electric"], 50,
        80,100,50,100,50,200,
        [_m("천둥",      "electric","special",110, 70,10,"paralysis",30),
         _m("볼트체인지","electric","special", 70,100,20),
         _m("스피드스타","normal",  "special", 60,100,20),
         _m("번개",      "electric","special", 90,100,15,"paralysis",30)],
        ability="transistor", item="choice-scarf",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper", is_semi_legend=True))

    # 87. 볼케니온 (Volcarona)
    pool.append(_poke("볼케니온", ["fire","bug"], 50,
        85,60,65,135,105,100,
        [_m("불꽃춤",   "fire", "special",110, 85, 5,"boost_sp_attack",100),
         _m("버그버즈",  "bug",  "special", 90,100,10,"drop_sp_def",10),
         _m("사이코키네시스","psychic","special",90,100,10,"drop_sp_def",10),
         _m("기가드레인","grass","special", 75,100,10)],
        ability="flame-body", item="choice-specs",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 88. 강철톤 (Corviknight)
    pool.append(_poke("강철톤", ["steel","flying"], 50,
        98,87,105,53,85,67,
        [_m("아이언헤드","steel",  "physical",80,100,15,"flinch",30),
         _m("날개치기",  "flying", "physical",60,100,35),
         _m("깃털댄스",  "flying", "status",   0,100,15,"drop_attack_2",100),
         _m("불릿펀치",  "steel",  "physical",40,100,30, priority=1)],
        ability="mirror-armor", item="leftovers",
        nature="impish", ev_hp=252, ev_defense=252, ev_sp_defense=4,
        role="wall"))

    # 89. 가르침온 다른빌드 (Gholdengo — 생명의구슬)
    pool.append(_poke("가르침온B", ["steel","ghost"], 50,
        87,60,95,133,91,84,
        [_m("섀도볼",    "ghost",  "special", 80,100,15,"drop_sp_def",20),
         _m("기어체인지","steel",  "special",100,100,10),
         _m("파워젬",    "rock",   "special", 80,100,20),
         _m("나쁜음모",  "dark",   "status",   0,  0,20,"boost_sp_attack_2",100)],
        ability="good-as-gold", item="life-orb",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 90. 그레이트터스크 다른빌드 (돌격조끼)
    pool.append(_poke("그레이트터스크B", ["ground","fighting"], 50,
        115,131,131,53,53,87,
        [_m("헤드롱러시","ground",  "physical",100,100,10),
         _m("클로즈컴뱃","fighting","physical",120,100, 5),
         _m("아이스스피너","ice",   "physical", 80,100,15),
         _m("록슬라이드", "rock",   "physical", 75, 90,10,"flinch",30)],
        ability="protosynthesis", item="assault-vest",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="pivot"))

    # 91. 소금돌 (Garganacl)
    pool.append(_poke("소금돌", ["rock"], 50,
        100,100,130,45,90,35,
        [_m("솔트큐어", "rock",  "status",  0,  0,15),
         _m("스톤에지", "rock",  "physical",100, 80, 5),
         _m("헤비슬램", "steel", "physical", 80,100,10),
         _m("방어",     "normal","status",   0,  0,10)],
        ability="purifying-salt", item="leftovers",
        nature="careful", ev_hp=252, ev_sp_defense=252, ev_defense=4,
        role="wall"))

    # 92. 드래곤나이트 (Dragonite)
    pool.append(_poke("드래곤나이트", ["dragon","flying"], 50,
        91,134,95,100,100,80,
        [_m("극한속도", "normal","physical", 80,100, 5, priority=2),
         _m("드래곤크로","dragon","physical", 80,100,10),
         _m("불릿펀치", "steel", "physical", 40,100,30, priority=1),
         _m("아이언헤드","steel","physical", 80,100,15,"flinch",30)],
        ability="multiscale", item="choice-band",
        nature="adamant", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 93. 킹갬빗 다른빌드 (기합의띠)
    pool.append(_poke("킹갬빗B", ["dark","steel"], 50,
        100,135,120,60,85,50,
        [_m("수프림오버로드","dark", "physical",100,100,10),
         _m("철퇴",          "steel","physical", 80,100,15),
         _m("칼춤",          "normal","status",   0,  0,20,"boost_attack_2",100),
         _m("불릿펀치",      "steel","physical", 40,100,30, priority=1)],
        ability="supreme-overlord", item="focus-sash",
        nature="adamant", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 94. 냐오불 특수세트 (Meowscarada Special)
    pool.append(_poke("냐오불S", ["grass","dark"], 50,
        76,110,70,81,70,123,
        [_m("에너지볼",  "grass","special",90,100,10,"drop_sp_def",10),
         _m("악의파동",  "dark", "special",80,100,15,"flinch",20),
         _m("유턴",      "bug",  "physical",70,100,20),
         _m("섀도볼",    "ghost","special", 80,100,15,"drop_sp_def",20)],
        ability="protean", item="life-orb",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 95. 철가시 물리세트 (Iron Valiant Physical)
    pool.append(_poke("철가시P", ["fairy","fighting"], 50,
        74,130,90,120,60,116,
        [_m("클로즈컴뱃","fighting","physical",120,100, 5),
         _m("플레이러프","fairy",  "physical", 90, 95,10,"drop_attack",10),
         _m("칼춤",      "normal", "status",    0,  0,20,"boost_attack_2",100),
         _m("섀도클로",  "ghost",  "physical",  70,100,15,"flinch",30)],
        ability="quark-drive", item="focus-sash",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 96. 드래파르트 특수세트 (Dragapult Special)
    pool.append(_poke("드래파르트S", ["dragon","ghost"], 50,
        88,120,75,100,75,142,
        [_m("드래곤파동","dragon","special",85,100,10),
         _m("섀도볼",    "ghost", "special",80,100,15,"drop_sp_def",20),
         _m("화염방사",  "fire",  "special",90,100,15,"burn",10),
         _m("번개",      "electric","special",90,100,15,"paralysis",30)],
        ability="clear-body", item="choice-specs",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 97. 독파리 공격세트
    pool.append(_poke("독파리A", ["poison","water"], 50,
        50,63,152,53,142,35,
        [_m("독찌르기","poison","physical",80,100,20,"poison",30),
         _m("폭포오르기","water","physical",80,100,15),
         _m("회복",     "normal","status",  0,  0,10),
         _m("아이언테일","steel","physical",100, 75,15,"drop_def",30)],
        ability="merciless", item="assault-vest",
        nature="adamant", ev_hp=252, ev_attack=252, ev_defense=4,
        role="wall"))

    # 98. 발리져 (Garganacl 대신) → 파비코 (Palafin)
    pool.append(_poke("파비코", ["water"], 50,
        100,160,97,106,87,100,
        [_m("파도타기",  "water",  "physical",90,100,15),
         _m("폭포오르기","water",  "physical",80,100,15),
         _m("클로즈컴뱃","fighting","physical",120,100,5),
         _m("아이스펀치","ice",    "physical", 75,100,15,"freeze",10)],
        ability="zero-to-hero", item="choice-band",
        nature="adamant", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 99. 해피너스 공격세트
    pool.append(_poke("해피너스A", ["normal"], 50,
        255,10,10,75,135,55,
        [_m("냉동빔","ice",   "special",90,100,10,"freeze",10),
         _m("번개",  "electric","special",90,100,15,"paralysis",30),
         _m("화염방사","fire","special",90,100,15,"burn",10),
         _m("기합구슬","fighting","special",120,70,10)],
        ability="serene-grace", item="assault-vest",
        nature="modest", ev_hp=252, ev_sp_attack=252, ev_sp_defense=4,
        role="sweeper"))

    # 100. 냉동무 (Kyurem) — 준전설
    pool.append(_poke("냉동무", ["dragon","ice"], 50,
        125,130,90,130,90,95,
        [_m("냉동빔",    "ice",    "special", 90,100,10,"freeze",10),
         _m("드래곤펄스","dragon", "special", 85,100,10),
         _m("지구던지기","ground", "special",100,100,10),
         _m("아이스해머","ice",    "physical",100, 90,10)],
        ability="pressure", item="choice-specs",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper", is_semi_legend=True))

    # 101. 오쟈마르 (Ogerpon-Wellspring) — 준전설
    pool.append(_poke("오쟈마르", ["grass","water"], 50,
        80,120,84,60,96,110,
        [_m("담쟁이채찍","grass",  "physical", 45,100,20),
         _m("파워휩",    "grass",  "physical",120, 85,10),
         _m("폭포오르기","water",  "physical", 80,100,15),
         _m("칼춤",      "normal", "status",    0,  0,20,"boost_attack_2",100)],
        ability="water-absorb", item="wellspring-mask",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper", is_semi_legend=True))

    # 102. 가르침온 — 기교가 (다양한 역할)
    pool.append(_poke("가르침온C", ["steel","ghost"], 50,
        87,60,95,133,91,84,
        [_m("기어체인지", "steel",  "special",100,100,10),
         _m("파워젬",     "rock",   "special", 80,100,20),
         _m("기합구슬",   "fighting","special",120, 70,10),
         _m("만근봄",     "steel",  "status",   0,  0,15)],
        ability="good-as-gold", item="assault-vest",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="pivot"))

    # 103. 철가시 서포트 (Iron Valiant Support)
    pool.append(_poke("철가시S", ["fairy","fighting"], 50,
        74,130,90,120,60,116,
        [_m("전자기파",  "electric","status",   0, 90,20,"paralysis",100),
         _m("문포스",    "fairy",   "special",  95,100,10),
         _m("클로즈컴뱃","fighting","special",120,100, 5),
         _m("도발",      "dark",    "status",   0,100,20)],
        ability="quark-drive", item="focus-sash",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="support"))

    # 104. 드래곤나이트 (극한속도 다른 조합)
    pool.append(_poke("드래곤나이트B", ["dragon","flying"], 50,
        91,134,95,100,100,80,
        [_m("역린",      "dragon", "physical",120,100,10,"confuse",100),
         _m("극한속도",  "normal", "physical", 80,100, 5, priority=2),
         _m("아이언헤드","steel",  "physical", 80,100,15,"flinch",30),
         _m("지진",      "ground", "physical",100,100,10)],
        ability="inner-focus", item="life-orb",
        nature="adamant", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 105. 버섯모 (지원형 다른조합)
    pool.append(_poke("버섯모B", ["grass","poison"], 50,
        114,85,70,85,80,30,
        [_m("기가드레인","grass",  "special", 75,100,10),
         _m("포자",       "grass", "status",   0,100,15,"sleep",100),
         _m("독가루",     "poison","status",   0, 75,35,"poison",100),
         _m("보호",       "normal","status",   0,  0,10)],
        ability="effect-spore", item="black-sludge",
        nature="relaxed", ev_hp=252, ev_defense=252, ev_sp_defense=4,
        role="support"))

    # 106. 바리어도 (Dondozo)
    pool.append(_poke("왕큰입", ["water"], 50,
        150,100,115,65,65,35,
        [_m("파도타기",  "water","physical",90,100,15),
         _m("지진",      "ground","physical",100,100,10),
         _m("용의춤",    "dragon","status",  0,  0,20,"boost_attack_speed",100),
         _m("폭포오르기","water","physical",80,100,15)],
        ability="unaware", item="leftovers",
        nature="careful", ev_hp=252, ev_sp_defense=252, ev_defense=4,
        role="wall"))

    # 107. 미끄래곤 (Cyclizar)
    pool.append(_poke("미끄래곤", ["dragon","normal"], 50,
        70,95,65,85,65,121,
        [_m("유턴",     "bug",   "physical",70,100,20),
         _m("드래곤크로","dragon","physical",80,100,10),
         _m("지진",     "ground","physical",100,100,10),
         _m("스텔스록", "rock",  "status",  0,  0,20)],
        ability="shed-skin", item="choice-scarf",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="pivot"))

    # 108. 카나자라기 (Samurott-Hisui)
    pool.append(_poke("카나자라기", ["water","dark"], 50,
        90,108,80,100,65,85,
        [_m("아쿠아스텝","water","physical",80,100,10,"boost_speed",100),
         _m("독니",      "poison","physical",80,100,24,"poison",30),
         _m("야간기습",  "dark","physical",40,100,30, priority=1),
         _m("칼춤",      "normal","status",0,0,20,"boost_attack_2",100)],
        ability="sharpness", item="focus-sash",
        nature="jolly", ev_attack=252, ev_speed=252, ev_hp=4,
        role="sweeper"))

    # 109. 헤이라 (Glimmora)
    pool.append(_poke("헤이라", ["rock","poison"], 50,
        83,55,90,130,81,86,
        [_m("파워젬",    "rock",  "special", 80,100,20),
         _m("오물폭탄",  "poison","special", 90,100,10,"poison",30),
         _m("독압정",    "poison","status",  0,  0,20),
         _m("스텔스록",  "rock",  "status",  0,  0,20)],
        ability="corrosion", item="focus-sash",
        nature="timid", ev_sp_attack=252, ev_speed=252, ev_hp=4,
        role="support"))

    # 110. 크레세리아 (Cresselia) — 준전설
    pool.append(_poke("크레세리아", ["psychic"], 50,
        120,70,120,75,130,85,
        [_m("사이코키네시스","psychic","special",90,100,10,"drop_sp_def",10),
         _m("얼음빔",       "ice",   "special", 90,100,10,"freeze",10),
         _m("달빛",         "normal","status",   0,  0, 5),
         _m("전자기파",     "electric","status",  0, 90,20,"paralysis",100)],
        ability="levitate", item="leftovers",
        nature="bold", ev_hp=252, ev_defense=252, ev_sp_defense=4,
        role="wall", is_semi_legend=True))

    return pool


# ════════════════════════════════════════════════════════════════
# 준전설 1마리 제한 + 역할 밸런스 팀 빌더
# ════════════════════════════════════════════════════════════════

# 역할 가중치 (팀 구성 시 다양성 확보)
ROLE_WEIGHTS = {
    "sweeper":     0.45,
    "wall":        0.25,
    "pivot":       0.15,
    "support":     0.10,
    "semi_legend": 0.05,
}

def make_meta_team(pool: list, size: int = 3) -> list:
    """
    메타 풀에서 균형잡힌 팀 구성:
    - 준전설 최대 1마리
    - 역할 다양성 (sweeper + wall/support/pivot 조합)
    - 중복 타입 최소화
    """
    import copy

    semi_legends = [p for p in pool if getattr(p, '_is_semi_legend', False)]
    normals      = [p for p in pool if not getattr(p, '_is_semi_legend', False)]

    team = []
    used_types = set()
    has_semi_legend = False

    attempts = 0
    while len(team) < size and attempts < 200:
        attempts += 1

        # 역할 선택 가중치
        if len(team) == 0:
            # 첫 번째: sweeper 위주
            candidates = [p for p in normals if getattr(p, '_role', '') == 'sweeper']
        elif len(team) == 1:
            # 두 번째: wall 또는 pivot
            roles = ['wall', 'pivot', 'support']
            candidates = [p for p in normals if getattr(p, '_role', '') in roles]
        else:
            # 세 번째: 남은 역할 or 준전설 30% 확률
            if not has_semi_legend and random.random() < 0.3 and semi_legends:
                candidates = semi_legends
            else:
                candidates = normals

        if not candidates:
            candidates = pool

        # 이미 팀에 있는 포켓몬 제외
        team_names = {p.name for p in team}
        candidates = [p for p in candidates if p.name not in team_names]
        if not candidates:
            break

        # 타입 다양성 고려 (같은 타입 2개 이상 있으면 페널티)
        def type_score(p):
            overlap = len(set(p.types) & used_types)
            return -overlap + random.random() * 0.5

        candidates.sort(key=type_score, reverse=True)
        # 상위 5개 중 랜덤
        pick = random.choice(candidates[:min(5, len(candidates))])

        if getattr(pick, '_is_semi_legend', False):
            if has_semi_legend:
                continue
            has_semi_legend = True

        team.append(copy.deepcopy(pick))
        used_types.update(pick.types)

    # 부족하면 채우기
    while len(team) < size:
        remaining = [p for p in normals if p.name not in {t.name for t in team}]
        if not remaining:
            break
        team.append(copy.deepcopy(random.choice(remaining)))

    return team
