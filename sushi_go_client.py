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
    played_cards: list[str] = None # type: ignore
    has_chopsticks: bool = False
    has_unused_wasabi: bool = False
    puddings: int = 0

    def __post_init__(self):
        if self.played_cards is None:
            self.played_cards = []


class SushiGoClient:
    """A client for playing Sushi Go."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self.state: Optional[GameState] = None
        self._recv_buffer = ""
        self.mode = list(strategies.intersection(implemented))[0]
        # Track what cards each player has played so far (by player name)
        self.other_players_played: dict[str, list[str]] = {}


        self.cards_weights = {
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
        }

        self.other_players_scores: dict[str, int] = {}

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
        self.sock.sendall(message.encode("utf-8")) # type: ignore
        print(f">>> {command}")

    def receive(self) -> str:
        """Receive one line-delimited message from the server."""
        while True:
            if "\n" in self._recv_buffer:
                line, self._recv_buffer = self._recv_buffer.split("\n", 1)
                message = line.strip()
                print(f"<<< {message}")
                return message

            chunk = self.sock.recv(4096) # type: ignore
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
                # Update chopsticks/wasabi tracking based on played cards
                self.state.has_chopsticks = "Chopsticks" in self.state.played_cards
                self.state.has_unused_wasabi = any(
                    c == "Wasabi" for c in self.state.played_cards
                ) and not any(
                    c in ("Egg Nigiri", "Salmon Nigiri", "Squid Nigiri")
                    for c in self.state.played_cards
                )

    def parse_played(self, message: str):
        """Parse a PLAYED message to track other players' cards.
        
        Format: PLAYED Alice:Salmon Nigiri; Bob:Tempura; Carol:Maki Roll (2)
        """
        if message.startswith("PLAYED"):
            payload = message[len("PLAYED "):]
            # Split by semicolon to get each player's card
            for player_card in payload.split(";"):
                player_card = player_card.strip()
                if ":" in player_card:
                    player_name, card = player_card.split(":", 1)
                    player_name = player_name.strip()
                    card = card.strip()
                    
                    # Initialize player's list if not exists
                    if player_name not in self.other_players_played:
                        self.other_players_played[player_name] = []
                    
                    # Track the card they played
                    self.other_players_played[player_name].append(card)

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
        """Implementing this function is our priority."""

        if self.mode == "nigiri":
            if GameState.has_unused_wasabi == True:
                return self.highest_nigiri(hand)

            if highest_nigiri := self.highest_nigiri(hand):
                return highest_nigiri
            



            


            


        
            


        # Fallback: random
        return random.randint(0, len(hand) - 1)

    #return index of highest value nigiri card, or -1
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
                pudding_count = self.other_players_played[player].count("Pudding")
                self.other_players_played[player] = ["Pudding"] * pudding_count
        elif message.startswith("GAME_END"):
            print("Game over!")
            return False
        elif message.startswith("WAITING"):
            # Our move was accepted, waiting for others
            pass
        return True

    def play_turn(self):
        """Play a single turn."""
        if not self.state or not self.state.hand:
            return

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


def main():
    if len(sys.argv) != 5:
        print("Usage: python sushi_go_client.py <host> <port> <game_id> <player_name>")
        print("Example: python sushi_go_client.py localhost 7878 abc123 MyBot")
        sys.exit(1)

    host = sys.argv[1]
    port = int(sys.argv[2])
    game_id = sys.argv[3]
    player_name = sys.argv[4]

    client = SushiGoClient(host, port)
    client.run(game_id, player_name)


if __name__ == "__main__":
    main()
