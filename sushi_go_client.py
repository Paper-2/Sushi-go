#!/usr/bin/env python3
"""
Sushi Go Client - Python Starter Kit

This client connects to the Sushi Go server and plays using a simple strategy.
Modify the `choose_card` method to implement your own AI!

Usage:
    python sushi_go_client.py <server_host> <server_port> <game_id> <player_name>

Example:
    python sushi_go_client.py localhost 7878 abc123 MyBot
"""

import argparse
import json
import random
import re
import socket
import sys
from dataclasses import dataclass
from typing import Optional

# Card names used by the protocol (now using full names instead of codes)
CARD_NAMES = {
    "Tempura": "Tempura",
    "Sashimi": "Sashimi",
    "Dumpling": "Dumpling",
    "Maki Roll (1)": "Maki Roll (1)",
    "Maki Roll (2)": "Maki Roll (2)",
    "Maki Roll (3)": "Maki Roll (3)",
    "Egg Nigiri": "Egg Nigiri",
    "Salmon Nigiri": "Salmon Nigiri",
    "Squid Nigiri": "Squid Nigiri",
    "Pudding": "Pudding",
    "Wasabi": "Wasabi",
    "Chopsticks": "Chopsticks",
}

strategies = {"nigiri", "maki", "tempura", "sashimi", "dumpling", "pudding"}
implemented = {"nigiri"}


@dataclass
class GameState:
    """Tracks the current state of the game."""

    game_id: str
    player_id: int
    hand: list[str]
    round: int = 1
    turn: int = 1
    score: int = 0
    played_cards: list[str] = None  # type: ignore
    has_chopsticks: bool = False
    has_unused_wasabi: bool = False
    puddings: int = 0

    def __post_init__(self):
        if self.played_cards is None:
            self.played_cards = []


class Deck:
    """Represents the deck of cards in Sushi Go."""

    def __init__(self):
        self.cards = (
            ["Tempura"] * 14
            + ["Sashimi"] * 14
            + ["Dumpling"] * 14
            + ["Maki Roll (1)"] * 6
            + ["Maki Roll (2)"] * 12
            + ["Maki Roll (3)"] * 8
            + ["Egg Nigiri"] * 5
            + ["Salmon Nigiri"] * 10
            + ["Squid Nigiri"] * 5
            + ["Pudding"] * 10
            + ["Wasabi"] * 6
            + ["Chopsticks"] * 4
        )


class SushiGoClient:
    """A client for playing Sushi Go."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self.state: Optional[GameState] = None
        self._recv_buffer = ""
        self.mode = list(strategies.intersection(implemented))[0]
        # Track what cards each player has played so far (by player name -> card name -> count)
        self.other_players_played: dict[str, dict[str, int]] = {}

        self.cards_weights: dict[str, float] = {
            "Tempura": 0,
            "Sashimi": 0,
            "Dumpling": 0,
            "Maki Roll (1)": 0,
            "Maki Roll (2)": 0,
            "Maki Roll (3)": 0,
            "Egg Nigiri": 0,
            "Salmon Nigiri": 0,
            "Squid Nigiri": 0,
            "Pudding": 0,
            "Wasabi": 0,
            "Chopsticks": 0,
            "Use chopsticks": 0,
        }

        self.other_players_scores: dict[str, int] = {}
        
        # Tournament state
        self.tournament_id: Optional[str] = None
        self.tournament_rejoin_token: Optional[str] = None
        self.in_tournament: bool = False
        self.current_match_token: Optional[str] = None
        self.tournament_round: int = 0
        self.opponent_name: Optional[str] = None

    def __post_init__(self):
        if self.played_cards is None:
            self.played_cards = []

    def connect(self):
        """Connect to the server."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        self._recv_buffer = ""
        print(f"Connected to {self.host}:{self.port}")

    def disconnect(self):
        """Disconnect from the server."""
        if self.sock:
            self.sock.close()
            self.sock = None

    def send(self, command: str):
        """Send a command to the server."""
        message = command + "\n"
        self.sock.sendall(message.encode("utf-8"))  # type: ignore
        print(f">>> {command}")

    def receive(self) -> str:
        """Receive one line-delimited message from the server."""
        while True:
            if "\n" in self._recv_buffer:
                line, self._recv_buffer = self._recv_buffer.split("\n", 1)
                message = line.strip()
                print(f"<<< {message}")
                return message

            chunk = self.sock.recv(4096)  # type: ignore
            if not chunk:
                raise ConnectionError("Server closed connection")
            self._recv_buffer += chunk.decode("utf-8", errors="replace")

    def receive_until(self, predicate) -> str:
        """Read lines until one matches predicate."""
        while True:
            message = self.receive()
            if not message:
                continue
            if predicate(message):
                return message

    def join_game(self, game_id: str, player_name: str) -> bool:
        """Join a game."""
        self.send(f"JOIN {game_id} {player_name}")
        response = self.receive_until(
            lambda line: line.startswith("WELCOME") or line.startswith("ERROR")
        )

        if response.startswith("WELCOME"):
            parts = response.split()
            self.state = GameState(game_id=parts[1], player_id=int(parts[2]), hand=[])
            return True
        elif response.startswith("ERROR"):
            print(f"Failed to join: {response}")
            return False
        return False

    def signal_ready(self):
        """Signal that we're ready to start."""
        self.send("READY")
        return self.receive()

    def leave_game(self):
        """Leave the current game so we can join the next match."""
        self.send("LEAVE")
        self.receive_until(
            lambda line: line.startswith("OK") or line.startswith("ERROR")
        )
        self.state = None

    def join_tournament(self, tournament_id: str, player_name: str) -> bool:
        """Join a tournament."""
        self.send(f"TOURNEY {tournament_id} {player_name}")
        response = self.receive_until(
            lambda line: line.startswith("TOURNAMENT_WELCOME") or line.startswith("ERROR")
        )

        if response.startswith("TOURNAMENT_WELCOME"):
            # TOURNAMENT_WELCOME <tournament_id> <count>/<max> <rejoin_token>
            parts = response.split()
            self.tournament_id = parts[1]
            self.tournament_rejoin_token = parts[3] if len(parts) > 3 else None
            self.in_tournament = True
            print(f"Joined tournament {self.tournament_id} ({parts[2]})")
            return True
        elif response.startswith("ERROR"):
            print(f"Failed to join tournament: {response}")
            return False
        return False

    def join_match(self, match_token: str) -> bool:
        """Join a tournament match using TJOIN."""
        self.send(f"TJOIN {match_token}")
        response = self.receive_until(
            lambda line: line.startswith("WELCOME") or line.startswith("ERROR")
        )

        if response.startswith("WELCOME"):
            parts = response.split()
            self.state = GameState(game_id=parts[1], player_id=int(parts[2]), hand=[])
            return True
        elif response.startswith("ERROR"):
            print(f"Failed to join match: {response}")
            return False
        return False

    def play_card(self, card_index: int):
        """Play a card by index."""
        self.send(f"PLAY {card_index}")
        return self.receive()

    def play_chopsticks(self, index1: int, index2: int):
        """Use chopsticks to play two cards."""
        self.send(f"CHOPSTICKS {index1} {index2}")
        return self.receive()

    def parse_hand(self, message: str):
        """Parse a HAND message and update state."""
        if message.startswith("HAND"):
            payload = message[len("HAND ") :]
            cards = []
            for match in re.finditer(r"(\d+):(.*?)(?=\s\d+:|$)", payload):
                cards.append(match.group(2).strip())
            if self.state:
                self.state.hand = cards
                # Update chopsticks tracking based on played cards
                self.state.has_chopsticks = "Chopsticks" in self.state.played_cards
                # Wasabi tracking: each nigiri uses one wasabi (if available)
                # Unused wasabi = wasabi_count - nigiri_count (if positive)
                wasabi_count = self.state.played_cards.count("Wasabi")
                nigiri_count = sum(
                    1 for c in self.state.played_cards
                    if c in ("Egg Nigiri", "Salmon Nigiri", "Squid Nigiri")
                )
                self.state.has_unused_wasabi = wasabi_count > nigiri_count

    def parse_played(self, message: str):
        """Parse a PLAYED message to track other players' cards.

        Format: PLAYED Alice:Salmon Nigiri; Bob:Tempura; Carol:Maki Roll (2)
        """
        if message.startswith("PLAYED"):
            payload = message[len("PLAYED ") :]
            # Split by semicolon to get each player's card
            for player_card in payload.split(";"):
                player_card = player_card.strip()
                if ":" in player_card:
                    player_name, card = player_card.split(":", 1)
                    player_name = player_name.strip()
                    card = card.strip()

                    # Initialize player's dict if not exists
                    if player_name not in self.other_players_played:
                        self.other_players_played[player_name] = {}

                    # Track the card they played (increment count)
                    self.other_players_played[player_name][card] = (
                        self.other_players_played[player_name].get(card, 0) + 1
                    )

    def parse_round_end(self, message: str):
        """Parse a ROUND_END message to track player scores.

        Format: ROUND_END 1 {"Alice":12,"Bob":8,"Carol":15}
        """
        if message.startswith("ROUND_END"):
            try:
                # Extract JSON part after the round number
                parts = message.split(maxsplit=2)
                if len(parts) >= 3:
                    scores_json = parts[2]
                    scores = json.loads(scores_json)

                    # Update other players' scores
                    for player_name, score in scores.items():
                        self.other_players_scores[player_name] = score

                    # Update current player's score if available
                    if self.state and self.state.game_id:
                        # Try to find our score by checking if our player_id matches
                        # For now, we'll assume the first score is ours if we have a state
                        for player_name, score in scores.items():
                            # Update our score based on game state
                            self.other_players_scores[player_name] = score
            except (json.JSONDecodeError, ValueError, IndexError):
                pass  # Silently ignore parsing errors

    def choose_card(self, hand: list[str]) -> int:
        """Choose the best card to play based on calculated weights."""
        if not hand:
            return 0

        # Find the card with highest weight
        best_index = 0
        best_weight = -float("inf")

        for i, card in enumerate(hand):
            weight = self.cards_weights.get(card, 0)
            if weight > best_weight:
                best_weight = weight
                best_index = i

        return best_index

    # return index of highest value nigiri card, or -1
    def highest_nigiri(self, hand: list[str]) -> int:
        """Choose the highest value nigiri card."""
        best_index = -1
        best_value = -1
        for i, card in enumerate(hand):
            if card == "Egg Nigiri":
                value = 1
            elif card == "Salmon Nigiri":
                value = 2
            elif card == "Squid Nigiri":
                value = 3
            else:
                continue

            if value > best_value:
                best_value = value
                best_index = i

        return best_index

    def handle_message(self, message: str):
        """Handle a message from the server."""
        if message.startswith("HAND"):
            self.parse_hand(message)
        elif message.startswith("ROUND_START"):
            parts = message.split()
            if self.state:
                self.state.round = int(parts[1])
                self.state.turn = 1
                self.state.played_cards = []
        elif message.startswith("PLAYED"):
            # Cards were revealed, parse what other players played
            self.parse_played(message)
            if self.state:
                self.state.turn += 1
        elif message.startswith("ROUND_END"):
            # Round ended - parse scores and clear played cards except pudding
            if self.state:
                self.state.played_cards = []
            # Parse scores from ROUND_END message
            self.parse_round_end(message)
            # Clear other players' cards except pudding (which persists)
            for player in self.other_players_played:
                pudding_count = self.other_players_played[player].get("Pudding", 0)
                self.other_players_played[player] = (
                    {"Pudding": pudding_count} if pudding_count > 0 else {}
                )
        elif message.startswith("GAME_END"):
            print("Game over!")
            if not self.in_tournament:
                return False
            # In tournament mode, game end means match is over, wait for next match
            return True
        elif message.startswith("WAITING"):
            # Our move was accepted, waiting for others
            pass
        # Tournament-specific messages
        elif message.startswith("TOURNAMENT_JOINED"):
            # TOURNAMENT_JOINED <tournament_id> <player_name> <count>/<max>
            parts = message.split()
            print(f"Player {parts[2]} joined tournament ({parts[3]})")
        elif message.startswith("TOURNAMENT_MATCH"):
            # TOURNAMENT_MATCH <tournament_id> <match_token|BYE> <round> [opponent_name]
            parts = message.split()
            self.tournament_round = int(parts[3])
            if parts[2] == "BYE":
                print(f"Round {self.tournament_round}: Received BYE (auto-advance)")
                self.current_match_token = None
            else:
                self.current_match_token = parts[2]
                self.opponent_name = parts[4] if len(parts) > 4 else "Unknown"
                print(f"Round {self.tournament_round}: Match vs {self.opponent_name}")
        elif message.startswith("TOURNAMENT_COMPLETE"):
            # TOURNAMENT_COMPLETE <tournament_id> <winner_name>
            parts = message.split()
            winner = parts[2] if len(parts) > 2 else "Unknown"
            print(f"Tournament complete! Winner: {winner}")
            self.in_tournament = False
            return False
        return True

    def play_turn(self):
        """Play a single turn."""
        if not self.state or not self.state.hand:
            return
        self.calculate_weights()

        # Check if we should use chopsticks
        use_chopsticks_value = self.cards_weights.get("Use chopsticks", -1)
        if (
            use_chopsticks_value > 1
            and self.state.has_chopsticks
            and len(self.state.hand) >= 2
        ):
            # Find the top 2 cards by weight
            card_values = [
                (i, card, self.cards_weights.get(card, 0))
                for i, card in enumerate(self.state.hand)
            ]
            card_values.sort(key=lambda x: x[2], reverse=True)

            first_idx, first_card, _ = card_values[0]
            second_idx, second_card, _ = card_values[1]

            if first_idx != second_idx:
                response = self.play_chopsticks(first_idx, second_idx)
                if response.startswith("OK"):
                    if self.state:
                        self.state.played_cards.append(first_card)
                        self.state.played_cards.append(second_card)
                        if "Chopsticks" in self.state.played_cards:
                            self.state.played_cards.remove("Chopsticks")
                        self.state.has_chopsticks = False  # Used our chopsticks
                    return  # Only return if chopsticks succeeded

        card_index = self.choose_card(self.state.hand)

        # Track the card we're about to play
        played_card = self.state.hand[card_index]

        response = self.play_card(card_index)

        if response.startswith("OK"):
            if self.state:
                self.state.played_cards.append(played_card)

    def run(self, game_id: str, player_name: str):
        """Main game loop."""
        try:
            self.connect()

            if not self.join_game(game_id, player_name):
                return

            # Signal ready
            response = self.signal_ready()

            # Main game loop
            running = True
            while running:
                # Check for incoming messages
                message = self.receive()
                running = self.handle_message(message)

                # If we received our hand, play a card
                if message.startswith("HAND") and self.state and self.state.hand:
                    self.play_turn()

        except KeyboardInterrupt:
            print("\nDisconnecting...")
        except Exception as e:
            print(f"Error: {e}")
        finally:
            self.disconnect()

    def play_game(self) -> Optional[str]:
        """Play a full game. Returns a tournament message if one arrived during the game, else None."""
        while True:
            message = self.receive()

            # Tournament messages can arrive during a game
            if message.startswith("TOURNAMENT_MATCH") or message.startswith("TOURNAMENT_COMPLETE"):
                return message

            game_running = self.handle_message(message)

            if message.startswith("HAND") and self.state and self.state.hand:
                self.play_turn()

            if not game_running:
                return None

    def run_tournament(self, tournament_id: str, player_name: str):
        """Main tournament loop."""
        try:
            self.connect()

            if not self.join_tournament(tournament_id, player_name):
                return

            print("Waiting for tournament to start...")

            pending_message = None

            # Tournament loop - wait for match assignments
            while True:
                if pending_message:
                    msg = pending_message
                    pending_message = None
                else:
                    msg = self.receive()

                if not msg:
                    continue

                if msg.startswith("TOURNAMENT_MATCH"):
                    # TOURNAMENT_MATCH <tid> <match_token> <round> [<opponent>]
                    parts = msg.split()
                    match_token = parts[2]
                    round_num = parts[3]
                    opponent = parts[4] if len(parts) > 4 else "unknown"

                    if match_token == "BYE" or opponent == "BYE":
                        print(f"Round {round_num}: got a BYE, auto-advancing...")
                        continue

                    print(f"Round {round_num}: matched vs {opponent}")

                    if not self.join_match(match_token):
                        continue

                    self.signal_ready()

                    # Reset game state for new match
                    self.other_players_played = {}
                    self.other_players_scores = {}

                    # Play the game - may return a tournament message that arrived mid-game
                    pending_message = self.play_game()

                    # Leave the game so we can join the next match
                    self.leave_game()

                elif msg.startswith("TOURNAMENT_COMPLETE"):
                    # TOURNAMENT_COMPLETE <tid> <winner>
                    parts = msg.split()
                    winner = parts[2] if len(parts) > 2 else "unknown"
                    print(f"Tournament complete! Winner: {winner}")
                    break

                elif msg.startswith("TOURNAMENT_JOINED"):
                    print(f"  {msg}")

                # Ignore other messages

        except KeyboardInterrupt:
            print("\nDisconnecting...")
        except Exception as e:
            print(f"Error: {e}")
        finally:
            self.disconnect()

    def calculate_weights(self):
        if not self.state:
            return

        hand = self.state.hand
        played = self.state.played_cards
        hand_size = len(hand)
        current_round = self.state.round
        turn = self.state.turn

        # === WASABI === (PPC 4.5 with squid, 3 with salmon)
        # ALWAYS first pick - best value in game when paired with squid (9 pts)
        if not self.state.has_unused_wasabi:
            if turn <= 2:
                self.cards_weights["Wasabi"] = 8  # Excellent chance to hit squid/salmon
            elif turn <= 4:
                self.cards_weights["Wasabi"] = 5  # Still good odds
            else:
                self.cards_weights["Wasabi"] = 2  # Likely to only hit egg or nothing
        else:
            self.cards_weights["Wasabi"] = 0.5  # Already have one, don't stack

        # === NIGIRI === (Egg=1, Salmon=2, Squid=3; with wasabi: 3/6/9)
        wasabi_mult = 3 if self.state.has_unused_wasabi else 1
        self.cards_weights["Egg Nigiri"] = 1 * wasabi_mult
        self.cards_weights["Salmon Nigiri"] = 2 * wasabi_mult
        self.cards_weights["Squid Nigiri"] = 3 * wasabi_mult

        if not self.state.has_chopsticks:
            if turn == 1:
                self.cards_weights["Chopsticks"] = 7  #  high combo potential
            elif turn == 2:
                self.cards_weights["Chopsticks"] = 5  #  good
            elif turn <= 4 and hand_size >= 4:
                self.cards_weights["Chopsticks"] = 3  # Moderate value
            else:
                self.cards_weights["Chopsticks"] = 0.5  # Too late, won't get value
        else:
            self.cards_weights["Chopsticks"] = 0  # Already have one

        # === TEMPURA === (PPC 2.5)
        # Decent consistency, easier to complete than sashimi
        tempura_count = played.count("Tempura")
        if tempura_count % 2 == 1:
            self.cards_weights["Tempura"] = 5  # Complete the pair!
        else:
            self.cards_weights["Tempura"] = 2.5  # Starting fresh

        # === SASHIMI === (PPC 3.33 but IT'S A TRAP!)
        # Only ~13% chance (14/108) per card. Easy to block, often doesn't exist in pool.
        sashimi_count = played.count("Sashimi")
        if sashimi_count % 3 == 2:
            self.cards_weights["Sashimi"] = 10  # MUST complete - 2 dead cards otherwise
        elif sashimi_count % 3 == 1:
            self.cards_weights["Sashimi"] = 2
        else:
            self.cards_weights["Sashimi"] = 0.5  # Starting fresh

        # Dumplings: 1, 3, 6, 10, 15 points for 1-5+ dumplings
        dumpling_count = played.count("Dumpling")

        # === MAKI === (PPC 6 at best)
        my_maki = (
            played.count("Maki Roll (1)") * 1
            + played.count("Maki Roll (2)") * 2
            + played.count("Maki Roll (3)") * 3
        )

        # Get max opponent maki
        max_opponent_maki = 0
        for player, cards in self.other_players_played.items():
            opponent_maki = (
                cards.get("Maki Roll (1)", 0) * 1
                + cards.get("Maki Roll (2)", 0) * 2
                + cards.get("Maki Roll (3)", 0) * 3
            )
            max_opponent_maki = max(max_opponent_maki, opponent_maki)

        if my_maki > max_opponent_maki + 3:
            # leading by a lot
            self.cards_weights["Maki Roll (1)"] = 0.5
            self.cards_weights["Maki Roll (2)"] = 1
            self.cards_weights["Maki Roll (3)"] = 1.5
        elif my_maki < max_opponent_maki:
            # Behind
            self.cards_weights["Maki Roll (1)"] = 2
            self.cards_weights["Maki Roll (2)"] = 3
            self.cards_weights["Maki Roll (3)"] = 4.5

        # === PUDDING === (PPC 6 at best)
        my_pudding = self.state.puddings + played.count("Pudding")

        # Get opponent pudding counts
        min_opponent_pudding = float("inf")
        max_opponent_pudding = 0
        for player, cards in self.other_players_played.items():
            p_count = cards.get("Pudding", 0)
            min_opponent_pudding = min(min_opponent_pudding, p_count)
            max_opponent_pudding = max(max_opponent_pudding, p_count)

        if min_opponent_pudding == float("inf"):
            min_opponent_pudding = 0

        if current_round == 1:
            # Round 1: low priority, let it float
            self.cards_weights["Pudding"] = 1
        elif current_round == 2:

            if my_pudding <= min_opponent_pudding:
                self.cards_weights["Pudding"] = 3  # Risk of last place
            elif my_pudding >= max_opponent_pudding:
                self.cards_weights["Pudding"] = 1  # Already leading
            else:
                self.cards_weights["Pudding"] = 2
        else:  # Round 3
            # Round 3: high priority to avoid last or secure first
            if my_pudding < min_opponent_pudding:
                self.cards_weights["Pudding"] = 6
            elif (
                my_pudding == min_opponent_pudding
                and min_opponent_pudding < max_opponent_pudding
            ):
                self.cards_weights["Pudding"] = 5
            elif my_pudding >= max_opponent_pudding:
                self.cards_weights["Pudding"] = 2
            else:
                self.cards_weights["Pudding"] = 3

        # === USE CHOPSTICKS ===
        self.cards_weights["Use chopsticks"] = 0
        if self.state.has_chopsticks and len(hand) >= 2:
            card_values = [
                (i, self.cards_weights.get(card, 0)) for i, card in enumerate(hand)
            ]
            card_values.sort(key=lambda x: x[1], reverse=True)

            if len(card_values) >= 2:
                top_two_value = card_values[0][1] + card_values[1][1]
                best_single = card_values[0][1]
                # Use chopsticks if playing 2 cards is significantly better
                # (top 2 combined > best single + 2 for opportunity cost)
                if top_two_value > best_single + 2:
                    self.cards_weights["Use chopsticks"] = top_two_value


def main():
    parser = argparse.ArgumentParser(
        description="Sushi Go Client - Connect to a Sushi Go server and play",
        usage="%(prog)s <host> <port> <game_id> <player_name> [-t]"
    )
    parser.add_argument("host", help="Server host (e.g., localhost or 10.8.1.191)")
    parser.add_argument("port", type=int, help="Server port (e.g., 7878)")
    parser.add_argument("game_id", help="Game ID to join")
    parser.add_argument("player_name", help="Name of the player")
    parser.add_argument(
        "-t", "--tournament",
        action="store_true",
        help="Tournament mode (use TOURNEY instead of JOIN)"
    )
    
    args = parser.parse_args()
    
    client = SushiGoClient(args.host, args.port)
    
    if args.tournament:
        print("Running in tournament mode...")
        client.run_tournament(args.game_id, args.player_name)
    else:
        client.run(args.game_id, args.player_name)


if __name__ == "__main__":
    main()
