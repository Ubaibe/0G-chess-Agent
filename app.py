import streamlit as st
import chess
import chess.pgn
import chess.engine
import requests
from stockfish import Stockfish
from openai import OpenAI
from zerog_py_sdk import create_broker
from zerog_py_sdk.utils import og_to_wei
import os
from dotenv import load_dotenv
from eth_account import Account
from mnemonic import Mnemonic
from web3 import Web3
import secrets
from pathlib import Path
import tempfile
from core.indexer import Indexer
from core.file import ZgFile


load_dotenv(override=True)

# Debug helper - remove after it works
if not os.getenv("PRIVATE_KEY"):
    st.error("❌ PRIVATE_KEY not found! Check your .env file")
    st.info("Make sure the file is named exactly `.env` (with the dot) and is in the same folder as app.py")

st.set_page_config(page_title="0G Chess Agent ♟️", layout="wide")
st.title("0G Chess Agent — Your Personalized AI with Wallet")

# ====================== CONFIG ======================
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
BLOCKCHAIN_RPC = "https://evmrpc-testnet.0g.ai"
INDEXER_RPC = "https://indexer-storage-testnet-turbo.0g.ai"  # Common testnet indexer


# ====================== WEB3 + CONTRACT ======================
@st.cache_resource
def get_web3():
    w3 = Web3(Web3.HTTPProvider(BLOCKCHAIN_RPC))
    if not w3.is_connected():
        st.error("Cannot connect to 0G Testnet")
    return w3


w3 = get_web3()

CONTRACT_ADDRESS = "0x0e19AD499f5462fF00106F88393f2F1eb205B46c"
contract_abi = [
    {"inputs":[],"name":"depositWager","outputs":[],"stateMutability":"payable","type":"function"},
    {"inputs":[{"internalType":"address","name":"player","type":"address"},{"internalType":"uint256","name":"wagerAmount","type":"uint256"},{"internalType":"bool","name":"playerWon","type":"bool"}],"name":"resolveGame","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[],"name":"withdraw","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"internalType":"address","name":"","type":"address"}],"name":"balances","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}
]

contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=contract_abi)


# ====================== 0G STORAGE CLIENT ======================
@st.cache_resource
def get_storage_indexer():
    try:
        return Indexer(INDEXER_RPC)
    except Exception as e:
        st.error(f"Storage indexer failed: {e}")
        return None


storage_indexer = get_storage_indexer()


def save_game_to_0g_storage():
    if not st.session_state.get("game_history"):
        st.warning("Play a game first!")
        return None

    try:
        # Create PGN
        game = chess.pgn.Game()
        game.headers["White"] = "Human"
        game.headers["Black"] = st.session_state.get("agent_name", "GambitZero")
        game.headers["Site"] = "0G Chess Agent"
        game.headers["Date"] = "2026.06.20"

        node = game
        for move_uci in st.session_state.game_history:
            node = node.add_variation(chess.Move.from_uci(move_uci))

        pgn_string = str(game)

        # Save to temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pgn", delete=False, encoding="utf-8") as tmp:
            tmp.write(pgn_string)
            tmp_path = tmp.name

        # === REAL 0G STORAGE UPLOAD ===
        file_obj = ZgFile.from_file_path(tmp_path)

        account = Account.from_key(PRIVATE_KEY)

        upload_opts = {
            "tags": b'\x00',
            "finalityRequired": True,
            "taskSize": 10,
            "expectedReplica": 1,
            "skipTx": False,
            "account": account,
        }

        result, err = storage_indexer.upload(
            file_obj,
            BLOCKCHAIN_RPC,
            account,
            upload_opts
        )

        Path(tmp_path).unlink()  # Clean up temp file

        if err is None and result:
            merkle_root = result.get("rootHash") or result.get("merkleRoot")
            tx_hash = result.get("txHash")

            st.success(f"✅ Game saved permanently on 0G Storage!")
            st.info(f"Merkle Root: `{merkle_root[:16]}...`")
            if tx_hash:
                st.caption(f"Tx: {tx_hash[:12]}...")

            # Store in session
            if "saved_games" not in st.session_state:
                st.session_state.saved_games = []
            st.session_state.saved_games.append({
                "merkle_root": merkle_root,
                "tx_hash": tx_hash,
                "pgn_preview": pgn_string[:150] + "..."
            })
            return merkle_root
        else:
            st.error(f"Upload error: {err}")
            return None

    except Exception as e:
        st.error(f"Storage upload failed: {e}")
        return None


# ====================== 0G CHAIN + WAGER CONTRACT ======================

@st.cache_resource
def get_web3():
    w3 = Web3(Web3.HTTPProvider("https://evmrpc-testnet.0g.ai"))
    if not w3.is_connected():
        st.error("❌ Cannot connect to 0G Testnet")
    return w3

w3 = get_web3()

# === YOUR DEPLOYED CONTRACT ===
CONTRACT_ADDRESS = "0x0e19AD499f5462fF00106F88393f2F1eb205B46c"

contract_abi = [
    {"inputs":[],"name":"depositWager","outputs":[],"stateMutability":"payable","type":"function"},
    {"inputs":[{"internalType":"address","name":"player","type":"address"},{"internalType":"uint256","name":"wagerAmount","type":"uint256"},{"internalType":"bool","name":"playerWon","type":"bool"}],"name":"resolveGame","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[],"name":"withdraw","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"internalType":"address","name":"","type":"address"}],"name":"balances","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}
]

contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=contract_abi)

# ====================== 0G COMPUTE ======================
@st.cache_resource
def get_0g_broker():
    try:
        return create_broker(private_key=PRIVATE_KEY, network="testnet")
    except Exception as e:
        st.error(f"0G Broker failed: {e}")
        return None

broker = get_0g_broker()

def get_agent_response(prompt: str, system_prompt: str = None) -> str:
    if not broker:
        return "0G Compute offline — Nice move! 😏"
    try:
        services = broker.inference.list_service()
        chatbot_services = [s for s in services if "chat" in s.service_type.lower()]
        if not chatbot_services:
            return "No chatbot available."

        provider = chatbot_services[0]

        try:
            broker.inference.acknowledge_provider_signer(provider)
        except:
            pass

        metadata = broker.inference.get_service_metadata(provider)
        headers = broker.inference.get_request_headers(provider)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = requests.post(
            f"{metadata['endpoint']}/chat/completions",
            headers={"Content-Type": "application/json", **headers},
            json={"model": metadata.get("model", "meta-llama/Meta-Llama-3.1-8B-Instruct"),
                  "messages": messages, "max_tokens": 220, "temperature": 0.8},
            timeout=25
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"0G Agent: {str(e)[:80]}"

# ====================== 0G STORAGE ======================
@st.cache_resource
def get_storage_indexer():
    try:
        return Indexer(INDEXER_RPC)
    except:
        return None

storage_indexer = get_storage_indexer()

def save_game_to_0g_storage():
    if not st.session_state.get("game_history"):
        st.warning("Play at least one full game first!")
        return None

    try:
        # Create PGN
        game = chess.pgn.Game()
        game.headers["White"] = "Human"
        game.headers["Black"] = st.session_state.get("agent_name", "GambitZero")
        game.headers["Site"] = "0G Chess Agent"
        game.headers["Date"] = "2026.06.20"

        node = game
        for move_uci in st.session_state.game_history:
            node = node.add_variation(chess.Move.from_uci(move_uci))

        pgn_string = str(game)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".pgn", delete=False, encoding="utf-8") as tmp:
            tmp.write(pgn_string)
            tmp_path = tmp.name

        file_obj = ZgFile.from_file_path(tmp_path)
        account = Account.from_key(PRIVATE_KEY)

        upload_opts = {
            "tags": b'\x00',
            "finalityRequired": True,
            "taskSize": 8,
            "expectedReplica": 1,
            "skipTx": False,
            "account": account,
        }

        result, err = storage_indexer.upload(
            file_obj, BLOCKCHAIN_RPC, account, upload_opts
        )

        Path(tmp_path).unlink(missing_ok=True)
        # === SUCCESS CHECK ===
        merkle_root = None
        tx_hash = None

        if result:
            merkle_root = result.get("rootHash") or result.get("merkleRoot")
            tx_hash = result.get("txHash")

        if tx_hash or merkle_root:
            st.success("✅ Game saved on 0G Storage! Transaction confirmed.")
            if merkle_root:
                st.info(f"**Merkle Root:** `{merkle_root[:20]}...`")
            if tx_hash:
                st.success(f"**Tx:** `{tx_hash[:20]}...`")
                st.markdown(f"[🔗 View on Explorer](https://chainscan-galileo.0g.ai/tx/{tx_hash})")



            # Store in session
            if "saved_games" not in st.session_state:
                st.session_state.saved_games = []
            st.session_state.saved_games.append({
                "merkle_root": merkle_root,
                "tx_hash": tx_hash
            })
            return merkle_root
        else:
            st.error(f"Upload error: {err}")
            st.info("But transaction may have succeeded — check the explorer with your wallet address.")
            return None

    except Exception as e:
        st.error(f"Storage failed: {str(e)}")
        st.info("⚠️ Check the explorer anyway — transaction often still goes through.")
        return None

# ====================== WALLET ======================
if "agent_wallet" not in st.session_state:
    st.session_state.agent_wallet = None

def generate_new_wallet():
    # Generate strong mnemonic
    mnemo = Mnemonic("english")
    mnemonic_phrase = mnemo.generate(strength=256)  # 24 words

    # Derive account from mnemonic
    Account.enable_unaudited_hdwallet_features()
    acct = Account.from_mnemonic(mnemonic_phrase)

    st.session_state.agent_wallet = {
        "address": acct.address,
        "mnemonic": mnemonic_phrase,
        "private_key": acct.key.hex()  # Only kept in session, never saved
    }
    return st.session_state.agent_wallet


# ====================== SIDEBAR - Wallet Section ======================
with st.sidebar:
    st.header("🔑 Agent Wallet")

    if st.button("🆕 Generate New Agent Wallet", type="primary"):
        wallet = generate_new_wallet()
        st.success("New Agent Wallet Generated!")
        st.rerun()

    if st.session_state.agent_wallet:
        st.write("**Agent Address:**")
        st.code(st.session_state.agent_wallet["address"])

        st.warning("⚠️ **BACK UP YOUR SEED PHRASE NOW**")
        st.info("You are the **sole custodian**. We never store your private key.")

        with st.expander("📋 Show 24-Word Seed Phrase (Click to Reveal)"):
            st.code(st.session_state.agent_wallet["mnemonic"], language=None)
            st.caption("⚠️ Save this somewhere safe. If you lose it, the wallet is gone forever.")

        # Temporary private key input for signing (session only)
        temp_pk = st.text_input(
            "Enter Private Key to Sign Transactions (Session Only)",
            type="password",
            placeholder="0x..."
        )
        if temp_pk:
            st.session_state.temp_private_key = temp_pk
            st.success("Private key loaded for this session")
    else:
        st.info("Generate an Agent Wallet to start wagering")

    # Wager Section
    st.header("💰 Wager")
    if st.session_state.get("agent_wallet") and st.session_state.get("temp_private_key"):
        wager_amount = st.number_input("Wager (A0G)", min_value=0.001, value=0.01, step=0.001)

        if st.button("Deposit Wager"):
            try:
                acct = Account.from_key(st.session_state.temp_private_key)
                tx = contract.functions.depositWager().build_transaction({
                    'from': acct.address,
                    'value': w3.to_wei(wager_amount, 'ether'),
                    'gas': 300000,
                    'nonce': w3.eth.get_transaction_count(acct.address),
                })
                signed = acct.sign_transaction(tx)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                st.success(f"Deposited! Tx: {tx_hash.hex()[:10]}...")
            except Exception as e:
                st.error(f"Deposit failed: {e}")

        if st.button("Withdraw Balance"):
            try:
                acct = Account.from_key(st.session_state.temp_private_key)
                tx = contract.functions.withdraw().build_transaction({
                    'from': acct.address,
                    'gas': 200000,
                    'nonce': w3.eth.get_transaction_count(acct.address),
                })
                signed = acct.sign_transaction(tx)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                st.success(f"Withdrawn! Tx: {tx_hash.hex()[:10]}...")
            except Exception as e:
                st.error(f"Withdraw failed: {e}")
    else:
        st.info("Wallet + Private Key required for wagering")

# Sidebar for settings
with st.sidebar:
    st.header("Agent Settings")
    agent_name = st.text_input("Agent Name", value="GambitZero")
    difficulty = st.slider("Stockfish Depth (Elo ~)", 8, 20, 12)

    stockfish_path = st.text_input(
        "Stockfish Executable Path",
        value=r"C:\Users\Ibe\Downloads\stockfish-windows-x86-64-avx2\stockfish\stockfish-windows-x86-64-avx2.exe"
    )
    if st.button("Generate New Persona on 0G"):
        system = "You are a witty, slightly cocky chess grandmaster living on the 0G blockchain."
        persona = get_agent_response(
            f"Create a short, fun personality bio for chess agent named {agent_name}. Include trash-talk style.",
            system
        )
        st.session_state.agent_persona = persona
        st.success("New persona generated on 0G Compute!")

    if st.button("Test Stockfish Path"):
        try:
            sf = Stockfish(path=stockfish_path)
            st.success(f"✅ Stockfish working! Version: {sf.get_stockfish_version()}")
        except Exception as e:
            st.error(f"❌ Path error: {e}")

    # Main area wallet status
    if st.session_state.agent_wallet:
        st.caption(f"🪪 Agent Wallet: `{st.session_state.agent_wallet['address'][:8]}...`")
    else:
        st.warning("No Agent Wallet yet — Generate one in the sidebar")

# Initialize board
if "board" not in st.session_state:
    st.session_state.board = chess.Board()
    st.session_state.game_history = []  # list of moves
    st.session_state.agent_persona = f"You are {agent_name}, a witty chess agent on 0G."

# Display board (simple SVG for now)
board_svg = chess.svg.board(st.session_state.board, size=400)
st.markdown(f'<div style="display: flex; justify-content: center;">{board_svg}</div>',
            unsafe_allow_html=True)

col1, col2 = st.columns([3, 2])

with col1:
    move = st.text_input("Your move (e.g. e2e4)", key="move_input")
    if st.button("Make Move", type="primary"):
        try:
            move_obj = chess.Move.from_uci(move)
            if move_obj in st.session_state.board.legal_moves:
                st.session_state.board.push(move_obj)
                st.session_state.game_history.append(move)
                st.success(f"You played: {move}")

                # Agent (Stockfish) responds
                try:
                    stockfish = Stockfish(path=stockfish_path)
                    stockfish.set_depth(difficulty)
                    stockfish.set_fen_position(st.session_state.board.fen())
                    best_move = stockfish.get_best_move()

                    if best_move:
                        st.session_state.board.push_uci(best_move)
                        st.session_state.game_history.append(best_move)
                        st.success(f"Agent played: {best_move}")

                        # === AGENT COMMENT via 0G Compute ===
                        comment_prompt = f"Game so far: {st.session_state.game_history[-6:]}. You just played {best_move} as Black. Give short witty comment or trash talk."
                        comment = get_agent_response(comment_prompt, st.session_state.agent_persona)
                        st.info(f"💬 {agent_name}: {comment}")

                        # THIS IS THE KEY FIX
                        st.rerun()  # ← Forces immediate board update

                    else:
                        st.info("Game over!")

                except Exception as e:
                    st.error(f"Stockfish Error: {e}")
                    st.info("Tip: Check your Stockfish .exe path in sidebar")

            else:
                st.error("Illegal move! Try again.")
        except Exception as e:
            st.error(f"Invalid move format: {e}")
            st.info("Use UCI notation like: e2e4, g1f3, e7e8q")
with col2:
    st.subheader("Game Status")
    st.write(f"Status: {'White to move' if st.session_state.board.turn else 'Black to move'}")
    if st.session_state.board.is_checkmate():
        winner = "Black" if st.session_state.board.turn else "White"
        player_won = not st.session_state.board.turn
        st.error(f"Checkmate! {winner} wins")
        # Auto resolve onchain
        if st.session_state.get("agent_wallet") and st.session_state.get("temp_private_key"):
            try:
                wager_wei = w3.to_wei(0.01, 'ether')  # Change to your actual wager
                acct = Account.from_key(st.session_state.temp_private_key)
                tx = contract.functions.resolveGame(
                    st.session_state.agent_wallet["address"],
                    wager_wei,
                    player_won
                ).build_transaction({
                    'from': acct.address,
                    'gas': 300000,
                    'nonce': w3.eth.get_transaction_count(acct.address),
                })
                signed = acct.sign_transaction(tx)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                st.success(f"Onchain settled! Tx: {tx_hash.hex()[:12]}...")
            except Exception as e:
                st.error(f"Settlement failed: {e}")

        # Analysis
        analysis = get_agent_response(
            f"Briefly analyze the game: {st.session_state.game_history}",
            st.session_state.agent_persona
        )
        st.write("**0G Analysis:**", analysis)

    # 0G Storage Section
    st.subheader("💾 0G Storage")
    if st.button("💾 Save Game to 0G Storage (PGN)", type="primary"):
        result = save_game_to_0g_storage()
        if result:
            st.success("Game saved successfully on 0G!")

    # Show previously saved games
    if st.session_state.get("saved_games"):
        st.write("**Saved Games on 0G:**")
        for game in st.session_state.saved_games[-3:]:  # last 3
            st.code(game["merkle_root"][:20] + "...", language=None)



    if st.session_state.get("saved_games"):
        st.write("**Recently Saved Games:**")
        for g in st.session_state.saved_games[-3:]:
            st.code(g["merkle_root"][:20] + "...", language=None)
            st.caption(f"Tx: {g.get('tx_hash', '')[:12]}...")

# At the very end
st.caption("0G-Native: Compute (Personality) + Chain (Wagers) + Storage (Permanent PGN)")

if st.button("🔄 Reset Game"):
        st.session_state.board = chess.Board()
        st.session_state.game_history = []
        st.rerun()

st.subheader("Agent Persona")
st.write(st.session_state.agent_persona)
