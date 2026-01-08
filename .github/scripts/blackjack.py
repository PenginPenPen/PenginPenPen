import json
import os
import random
import re
import sys
import urllib.request
import urllib.error

STATE_MARKER = "BLACKJACK_STATE"
SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
VALID_STATUSES = {"idle", "playing", "dealer_turn", "finished"}


def build_deck():
    return [{"suit": suit, "rank": rank} for suit in SUITS for rank in RANKS]


def calculate_score(hand):
    score = 0
    aces = 0
    for card in hand:
        rank = card["rank"]
        if rank in {"J", "Q", "K"}:
            score += 10
        elif rank == "A":
            aces += 1
            score += 11
        else:
            score += int(rank)

    while score > 21 and aces > 0:
        score -= 10
        aces -= 1

    return score


def card_label(card):
    return f"{card['rank']}{card['suit']}"


def card_value_label(card):
    rank = card["rank"]
    if rank in {"J", "Q", "K"}:
        return "10"
    if rank == "A":
        return "11/1"
    return rank


def render_hand_table(cards, hide_hidden=False):
    faces = [card_label(card) for card in cards]
    values = [card_value_label(card) for card in cards]

    if hide_hidden:
        faces.append("🂠")
        values.append("?")

    header = "| " + " | ".join(faces) + " |"
    sep = "| " + " | ".join(["---"] * len(faces)) + " |"
    row = "| " + " | ".join(values) + " |"
    return "\n".join([header, sep, row])


def default_state():
    return {
        "status": "idle",
        "deck": [],
        "player": {"hand": [], "score": 0},
        "dealer": {"hand": [], "score": 0, "hidden_card": None},
        "result": None,
        "history": [],
    }


def parse_state(body):
    if not body:
        return None
    pattern = rf"<!--\s*{STATE_MARKER}\s*([\s\S]*?)\s*{STATE_MARKER}\s*-->"
    match = re.search(pattern, body)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def valid_card(card):
    if not isinstance(card, dict):
        return False
    suit = card.get("suit")
    rank = card.get("rank")
    return suit in SUITS and rank in RANKS


def validate_state(state):
    if not isinstance(state, dict):
        return False
    if state.get("status") not in VALID_STATUSES:
        return False

    deck = state.get("deck")
    player = state.get("player")
    dealer = state.get("dealer")

    if not isinstance(deck, list):
        return False
    if not isinstance(player, dict) or not isinstance(dealer, dict):
        return False
    if not isinstance(player.get("hand"), list) or not isinstance(dealer.get("hand"), list):
        return False

    hidden = dealer.get("hidden_card")
    if hidden is not None and not valid_card(hidden):
        return False

    all_cards = list(deck) + list(player["hand"]) + list(dealer["hand"]) + ([hidden] if hidden else [])
    seen = set()
    for card in all_cards:
        if not valid_card(card):
            return False
        key = (card["suit"], card["rank"])
        if key in seen:
            return False
        seen.add(key)

    return len(seen) <= 52


def full_dealer_hand(state):
    hand = list(state["dealer"]["hand"])
    hidden = state["dealer"].get("hidden_card")
    if hidden:
        hand.append(hidden)
    return hand


def sync_scores(state):
    state["player"]["score"] = calculate_score(state["player"]["hand"])
    if state["dealer"].get("hidden_card"):
        state["dealer"]["score"] = calculate_score(state["dealer"]["hand"])
    else:
        state["dealer"]["score"] = calculate_score(state["dealer"]["hand"])


def render_display(state):
    status = state["status"]
    lines = []

    if status == "idle":
        lines.append("## 🃏 Blackjack")
        lines.append("")
        lines.append("ゲームを開始するには `/blackjack start` とコメントしてください！")
        lines.append("")
        lines.append("💡 コマンド: `/blackjack start`")
        return "\n".join(lines)

    finished = status == "finished"
    title = "## 🃏 Blackjack - ゲーム終了！" if finished else "## 🃏 Blackjack"
    lines.append(title)
    lines.append("")

    lines.append("### ディーラーの手札")
    if state["dealer"].get("hidden_card"):
        dealer_visible = state["dealer"]["hand"]
        lines.append(render_hand_table(dealer_visible, hide_hidden=True))
        lines.append("")
        lines.append(f"**スコア: {calculate_score(dealer_visible)} + ?**")
    else:
        dealer_hand = state["dealer"]["hand"]
        dealer_score = calculate_score(dealer_hand)
        bust = " 💥 BUST!" if dealer_score > 21 else ""
        lines.append(render_hand_table(dealer_hand))
        lines.append("")
        lines.append(f"**スコア: {dealer_score}{bust}**")

    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("### あなたの手札")
    player_hand = state["player"]["hand"]
    player_score = calculate_score(player_hand)
    player_bust = " 💥 BUST!" if player_score > 21 else ""
    lines.append(render_hand_table(player_hand))
    lines.append("")
    lines.append(f"**スコア: {player_score}{player_bust}**")

    lines.append("")
    lines.append("---")
    lines.append("")

    if finished:
        result = state.get("result")
        if result == "player_win":
            lines.append("## 🎉 あなたの勝ち！")
        elif result == "dealer_win":
            lines.append("## 💥 ディーラーの勝ち！")
        elif result == "push":
            lines.append("## 🤝 引き分け（プッシュ）")
        else:
            lines.append("## ✅ ゲーム終了")

        lines.append("")
        lines.append("💡 `/blackjack start` で新しいゲームを開始")
    else:
        lines.append("💡 コマンド: `/hit` でカードを引く | `/stand` で勝負")
        lines.append("")
        lines.append("💡 `/status` で状態確認 | `/reset` でリセット")

    return "\n".join(lines)


def render_issue_body(state):
    display = render_display(state)
    hidden = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    return f"{display}\n\n<!-- {STATE_MARKER}\n{hidden}\n{STATE_MARKER} -->"


def parse_command(text):
    if not text:
        return None
    raw = text.strip()
    if not raw.startswith("/"):
        return None

    parts = raw.lower().split()
    if not parts:
        return None

    if parts[0] == "/blackjack" and len(parts) >= 2 and parts[1] == "start":
        return "start"
    if parts[0] == "/hit":
        return "hit"
    if parts[0] == "/stand":
        return "stand"
    if parts[0] == "/status":
        return "status"
    if parts[0] == "/reset":
        return "reset"
    return None


def draw_card(state):
    if not state["deck"]:
        used = state["player"]["hand"] + full_dealer_hand(state)
        used_keys = {(card["suit"], card["rank"]) for card in used}
        remaining = [card for card in build_deck() if (card["suit"], card["rank"]) not in used_keys]
        random.shuffle(remaining)
        state["deck"] = remaining
    if not state["deck"]:
        raise RuntimeError("デッキが空になりました")
    return state["deck"].pop()


def start_game(state):
    new_state = default_state()
    new_state["status"] = "playing"
    new_state["deck"] = build_deck()
    random.shuffle(new_state["deck"])

    new_state["player"]["hand"].append(draw_card(new_state))
    new_state["player"]["hand"].append(draw_card(new_state))
    new_state["dealer"]["hand"].append(draw_card(new_state))
    new_state["dealer"]["hidden_card"] = draw_card(new_state)

    sync_scores(new_state)

    player_score = calculate_score(new_state["player"]["hand"])
    dealer_full_score = calculate_score(full_dealer_hand(new_state))

    if player_score == 21 or dealer_full_score == 21:
        reveal_hidden(new_state)
        sync_scores(new_state)
        if player_score == 21 and dealer_full_score == 21:
            new_state["result"] = "push"
        elif player_score == 21:
            new_state["result"] = "player_win"
        else:
            new_state["result"] = "dealer_win"
        new_state["status"] = "finished"
        return new_state, "🃏 ゲーム開始！ ブラックジャック！"

    return new_state, "🃏 ゲームを開始しました！"


def reveal_hidden(state):
    hidden = state["dealer"].get("hidden_card")
    if hidden:
        state["dealer"]["hand"].append(hidden)
        state["dealer"]["hidden_card"] = None


def hit(state):
    if state["status"] != "playing":
        return state, "⚠️ 今はヒットできません。`/blackjack start` で新しいゲームを始めてください。"

    card = draw_card(state)
    state["player"]["hand"].append(card)
    sync_scores(state)

    if state["player"]["score"] > 21:
        reveal_hidden(state)
        sync_scores(state)
        state["status"] = "finished"
        state["result"] = "dealer_win"
        return state, f"💥 バスト！ {card_label(card)} を引きました。"

    return state, f"🃏 {card_label(card)} を引きました。"


def stand(state):
    if state["status"] != "playing":
        return state, "⚠️ 今はスタンドできません。`/blackjack start` で新しいゲームを始めてください。"

    state["status"] = "dealer_turn"
    reveal_hidden(state)

    while calculate_score(state["dealer"]["hand"]) < 17:
        state["dealer"]["hand"].append(draw_card(state))

    player_score = calculate_score(state["player"]["hand"])
    dealer_score = calculate_score(state["dealer"]["hand"])

    if dealer_score > 21:
        state["result"] = "player_win"
    elif player_score > dealer_score:
        state["result"] = "player_win"
    elif player_score < dealer_score:
        state["result"] = "dealer_win"
    else:
        state["result"] = "push"

    state["status"] = "finished"
    sync_scores(state)
    return state, "🧑‍⚖️ 勝負！"


def reset_game(_state):
    return default_state(), "🔄 ゲームをリセットしました。"


def github_request(method, url, data=None):
    headers = {
        "Authorization": f"token {os.environ.get('GITHUB_TOKEN', '')}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "blackjack-action",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req) as response:
        return response.read().decode("utf-8")


def update_issue(owner, repo, issue_number, body):
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}"
    github_request("PATCH", url, {"body": body})


def post_comment(owner, repo, issue_number, body):
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
    github_request("POST", url, {"body": body})


def main():
    comment_body = os.environ.get("COMMENT_BODY", "")
    command = parse_command(comment_body)
    if not command:
        return 0

    issue_body = os.environ.get("ISSUE_BODY", "")
    state = parse_state(issue_body)
    state_reset = False

    if state is None or not validate_state(state):
        state = default_state()
        state_reset = True

    message = ""
    try:
        if command == "start":
            state, message = start_game(state)
        elif command == "hit":
            state, message = hit(state)
        elif command == "stand":
            state, message = stand(state)
        elif command == "status":
            message = "📊 現在の状態です。"
        elif command == "reset":
            state, message = reset_game(state)
    except RuntimeError as exc:
        state = default_state()
        message = f"⚠️ {exc}。ゲームをリセットしました。"

    if state_reset and command != "start":
        message = f"⚠️ 状態が壊れていたためリセットしました。\n\n{message}"

    sync_scores(state)

    repo_full = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo_full or "/" not in repo_full:
        print("GITHUB_REPOSITORY is missing", file=sys.stderr)
        return 1
    owner, repo = repo_full.split("/", 1)

    issue_number = os.environ.get("ISSUE_NUMBER")
    if not issue_number:
        print("ISSUE_NUMBER is missing", file=sys.stderr)
        return 1

    issue_body_new = render_issue_body(state)
    update_issue(owner, repo, issue_number, issue_body_new)

    comment_display = render_display(state)
    comment = f"{message}\n\n{comment_display}" if message else comment_display
    post_comment(owner, repo, issue_number, comment)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8"), file=sys.stderr)
        sys.exit(1)
