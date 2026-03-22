"""
play.py — 사람 vs 학습된 AI 대전 인터페이스 (터미널)

사용법:
  python play.py --model checkpoints/best_model.pt
"""
import argparse
import os
import sys
import numpy as np

from env.battle_env import PokemonBattleEnv, make_sample_pokemon_pool, make_team
from env.damage_calc import get_type_multiplier
from agents.ppo_agent import PPOAgent


# ── ANSI 색상 ──────────────────────────────────
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


def hp_bar(current, max_hp, width=20) -> str:
    ratio = current / max_hp
    filled = int(ratio * width)
    bar = "█" * filled + "░" * (width - filled)
    color = GREEN if ratio > 0.5 else (YELLOW if ratio > 0.25 else RED)
    return f"{color}{bar}{RESET} {current}/{max_hp}"


def status_tag(status: str) -> str:
    tags = {
        "burn": f"{RED}[화상]{RESET}",
        "paralysis": f"{YELLOW}[마비]{RESET}",
        "sleep": f"{BLUE}[수면]{RESET}",
        "freeze": f"{CYAN}[얼음]{RESET}",
        "poison": f"{RED}[독]{RESET}",
        "toxic": f"{RED}[맹독]{RESET}",
        "none": "",
    }
    return tags.get(status, "")


def print_battle_screen(env: PokemonBattleEnv):
    """배틀 화면 출력"""
    p = env.player_active
    o = env.opponent_active

    print("\n" + "━" * 55)
    print(f"  📍 {BOLD}턴 {env.turn}{RESET}")
    print("━" * 55)

    # 상대 (위쪽)
    o_types = "/".join(o.types)
    print(f"  {BOLD}상대{RESET}  {RED}{o.name}{RESET} [{o_types}] {status_tag(o.status)}")
    print(f"  HP  {hp_bar(o.current_hp, o.max_hp)}")

    # 상대 파티 현황
    opp_party = " ".join(
        f"{'🟢' if not p_.is_fainted else '⚫'}{p_.name[:3]}"
        for p_ in env.opponent_team
    )
    print(f"  파티 {opp_party}")

    print("  " + "·" * 50)

    # 내 포켓몬 (아래쪽)
    p_types = "/".join(p.types)
    print(f"  {BOLD}나의{RESET}  {GREEN}{p.name}{RESET} [{p_types}] {status_tag(p.status)}")
    print(f"  HP  {hp_bar(p.current_hp, p.max_hp)}")

    # 내 파티 현황
    my_party = " ".join(
        f"{'🟢' if not p_.is_fainted else '⚫'}{p_.name[:3]}"
        for p_ in env.player_team
    )
    print(f"  파티 {my_party}")
    print("━" * 55)


def print_move_menu(env: PokemonBattleEnv):
    """기술 선택 메뉴"""
    p = env.player_active
    o = env.opponent_active

    print(f"\n  {BOLD}🎯 기술 선택{RESET}")
    for i, move in enumerate(p.moves[:4]):
        if move.pp <= 0:
            pp_str = f"{RED}PP 없음{RESET}"
            usable = "  "
        else:
            pp_str = f"PP {move.pp}/{move.max_pp}"
            usable = f"[{i+1}]"

        # 타입 상성 힌트
        mult = get_type_multiplier(move.type_, o.types)
        if mult == 0:
            eff = f"{RED}✗무효{RESET}"
        elif mult >= 2:
            eff = f"{GREEN}◎효과적!{RESET}"
        elif mult <= 0.5:
            eff = f"{YELLOW}△비효과적{RESET}"
        else:
            eff = ""

        cat_icon = "⚔️" if move.category == "physical" else ("✨" if move.category == "special" else "🔮")
        print(f"  {usable} {cat_icon} {BOLD}{move.name:12s}{RESET} "
              f"[{move.type_:8s}] 위력:{move.power:3d}  {pp_str}  {eff}")


def print_switch_menu(env: PokemonBattleEnv):
    """교체 메뉴"""
    switch_options = [
        (i, p) for i, p in enumerate(env.player_team)
        if i != env.player_active_idx and not p.is_fainted
    ]
    if not switch_options:
        print(f"  {RED}교체 가능한 포켓몬이 없습니다.{RESET}")
        return

    print(f"\n  {BOLD}🔄 포켓몬 교체{RESET}")
    for slot, (i, poke) in enumerate(switch_options):
        print(f"  [{5+slot}] {poke.name:10s} HP: {hp_bar(poke.current_hp, poke.max_hp, 10)}")


def get_player_action(env: PokemonBattleEnv) -> int:
    """플레이어 행동 입력"""
    while True:
        try:
            raw = input(f"\n  행동을 입력하세요 [1-4: 기술 | 5+: 교체 | q: 포기]: ").strip()

            if raw.lower() == "q":
                print("게임을 포기합니다.")
                sys.exit(0)

            action = int(raw) - 1  # 1-based → 0-based

            # 기술 선택
            if 0 <= action <= 3:
                moves = env.player_active.moves
                if action < len(moves) and moves[action].pp > 0:
                    return action
                else:
                    print(f"  {RED}사용할 수 없는 기술입니다.{RESET}")

            # 교체 선택
            elif 4 <= action <= 4 + env.team_size - 2:
                switch_options = [
                    i for i in range(env.team_size)
                    if i != env.player_active_idx and not env.player_team[i].is_fainted
                ]
                slot = action - 4
                if slot < len(switch_options):
                    return action
                else:
                    print(f"  {RED}교체할 포켓몬이 없습니다.{RESET}")
            else:
                print(f"  {RED}1~{4 + env.team_size - 1} 사이의 숫자를 입력하세요.{RESET}")

        except (ValueError, KeyboardInterrupt):
            print(f"  {RED}올바른 숫자를 입력하세요.{RESET}")


def play(model_path: str | None, team_size: int = 3):
    """메인 대전 루프"""
    env = PokemonBattleEnv(team_size=team_size, max_turns=100, render_mode=None)
    obs_dim = env.observation_space.shape[0]
    n_actions = env.action_space.n

    # AI 에이전트 로드
    agent = PPOAgent(obs_dim=obs_dim, n_actions=n_actions)
    if model_path and os.path.exists(model_path):
        agent.load(model_path)
        print(f"\n🤖 AI 트레이너 로드 완료: {model_path}")
    else:
        print(f"\n⚠️  모델 파일 없음 — 랜덤 AI로 대전합니다.")

    print(f"\n{'='*55}")
    print(f"  🎮 {BOLD}포켓몬 싱글 배틀{RESET}")
    print(f"  팀 크기: {team_size}마리  |  최대 {100}턴")
    print(f"{'='*55}")

    # 팀 구성
    obs, _ = env.reset()
    print(f"\n  📋 {BOLD}내 팀{RESET}")
    for p in env.player_team:
        types_str = "/".join(p.types)
        print(f"  - {p.name} [{types_str}] HP:{p.max_hp} ATK:{p.attack} SPD:{p.speed}")

    print(f"\n  📋 {BOLD}상대 팀{RESET}")
    for p in env.opponent_team:
        types_str = "/".join(p.types)
        print(f"  - {p.name} [{types_str}]")

    input(f"\n  Enter를 눌러 배틀 시작...")

    # 배틀 루프
    done = False
    total_reward = 0.0

    while not done:
        print_battle_screen(env)
        print_move_menu(env)
        print_switch_menu(env)

        # 플레이어 입력
        action = get_player_action(env)

        # 상대 AI 행동 (PPO)
        import numpy as np
        from train import make_action_mask
        opp_obs = env._get_obs()  # 상대 시점 (현재는 같은 obs 사용)
        ai_action = env._opponent_policy()  # 규칙 기반 또는 PPO

        # 스텝 실행
        obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated
        total_reward += reward

        # 배틀 로그 출력 (마지막 로그)
        if env.battle_log:
            recent_logs = env.battle_log[-8:]
            print(f"\n  {'─'*50}")
            for log_line in recent_logs:
                print(f"  {log_line}")

    # 결과
    p_alive = sum(1 for p in env.player_team if not p.is_fainted)
    o_alive = sum(1 for p in env.opponent_team if not p.is_fainted)

    print(f"\n{'='*55}")
    if o_alive == 0:
        print(f"  🏆 {GREEN}{BOLD}승리!{RESET} 상대 트레이너를 물리쳤다!")
    elif p_alive == 0:
        print(f"  💀 {RED}{BOLD}패배...{RESET} 다음엔 더 잘 해보자!")
    else:
        print(f"  ⏰ {YELLOW}시간 초과{RESET} (내 잔존 {p_alive}마리 vs 상대 {o_alive}마리)")
    print(f"  총 보상: {total_reward:+.2f}  |  턴 수: {env.turn}")
    print(f"{'='*55}\n")

    again = input("  다시 대전할까요? [y/n]: ").strip().lower()
    if again == "y":
        play(model_path, team_size)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default="checkpoints/best_model.pt",
                        help="학습된 모델 경로")
    parser.add_argument("--team-size", type=int, default=3)
    args = parser.parse_args()
    play(args.model, args.team_size)


if __name__ == "__main__":
    main()
