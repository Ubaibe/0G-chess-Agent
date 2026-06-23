import streamlit as st
import chess
import chess.pgn
import requests
import os
import tempfile
import subprocess
from pathlib import Path
from dotenv import load_dotenv

# Web3 & Wallet
from web3 import Web3
from eth_account import Account
from mnemonic import Mnemonic

# 0G Compute
from zerog_py_sdk import create_broker

# 0G Storage
from core.indexer import Indexer
from core.file import ZgFile

load_dotenv()

st.set_page_config(page_title="0G Chess Agent ♟️", layout="wide")
st.title("0G Chess Agent — AI + Wallet + Storage on 0G")

# ====================== STOCKFISH RUNTIME DOWNLOAD ======================
@st.cache_resource
def get_stockfish_path():
    path = "./stockfish/stockfish"
    if os.path.exists(path):
        return path

    with st.spinner("Downloading Stockfish (first time only)..."):
        os.makedirs("stockfish", exist_ok=True)
        try:
            # Download Linux binary
            url = "https://github.com/official-stockfish/Stockfish/releases/download/sf_16/stockfish-ubuntu-x86-64-avx2.tar.zst"
            subprocess.run(["wget", "-q", url, "-O", "stockfish.tar.zst"], check=True)
            subprocess.run(["tar", "-xf", "stockfish.tar.zst", "-C", "stockfish", "--strip-components=1"], check=True)
            os.rename("stockfish/stockfish-ubuntu-x86-64-avx2", path)
            os.chmod(path, 0o755)
            st.success("✅ Stockfish downloaded!")
        except:
            st.error("Failed to download Stockfish. Using fallback mode.")
            return None
    return path

stockfish_path = get_stockfish_path()

# ====================== CONFIG ======================
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
BLOCKCHAIN_RPC = "https://evmrpc-testnet.0g.ai"
INDEXER_RPC = "https://indexer-storage-testnet-turbo.0g.ai"

# ====================== WEB3 + CONTRACT ======================
@st.cache_resource
def get_web3():
    w3 = Web3(Web3.HTTPProvider(BLOCKCHAIN_RPC))
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
        st.warning("Play a game first!")
        return None
    try:
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

        result, err = storage_indexer.upload(file_obj, BLOCKCHAIN_RPC, account, upload_opts)
        Path(tmp_path).unlink(missing_ok=True)

        if result:
            merkle_root = result.get("rootHash") or result.get("merkleRoot")
            tx_hash = result.get("txHash")
            st.success("✅ Game saved permanently on 0G Storage!")
            if merkle_root:
                st.info(f"Merkle Root: `{merkle_root[:20]}...`")
            if tx_hash:
                st.markdown(f"[🔗 View Tx](https://chainscan-galileo.0g.ai/tx/{tx_hash})")
            return merkle_root
        else:
            st.error("Upload error, but transaction may have succeeded.")
            return None
    except Exception as e:
        st.error(f"Storage failed: {e}")
        return None

# ====================== WALLET ======================
if "agent_wallet" not in st.session_state:
    st.session_state.agent_wallet = None

def generate_new_wallet():
    mnemo = Mnemonic("english")
    mnemonic_phrase = mnemo.generate(strength=256)
    Account.enable_unaudited_hdwallet_features()
    acct = Account.from_mnemonic(mnemonic_phrase)
    st.session_state.agent_wallet = {
        "address": acct.address,
        "mnemonic": mnemonic_phrase,
        "private_key": acct.key.hex()
    }
    return st.session_state.agent_wallet

# ====================== SIDEBAR ======================
with st.sidebar:
    st.header("🔑 Agent Wallet")
    if st.button("🆕 Generate New Agent Wallet", type="primary"):
        generate_new_wallet()
        st.success("Wallet Generated!")
        st.rerun()

    if st.session_state.agent_wallet:
        st.code(st.session_state.agent_wallet["address"])
        with st.expander("Show 24-Word Seed Phrase"):
            st.code(st.session_state.agent_wallet["mnemonic"])
        temp_pk = st.text_input("Private Key (Session Only)", type="password")
        if temp_pk:
            st.session_state.temp_private_key = temp_pk
    else:
        st.info("Generate wallet first")

    # Wager
    st.header("💰 Wager")
    if st.session_state.get("agent_wallet") and st.session_state.get("temp_private_key"):
        wager = st.number_input("Wager (A0G)", 0.001, 1.0, 0.01)
        if st.button("Deposit Wager"):
            try:
                acct = Account.from_key(st.session_state.temp_private_key)
                tx = contract.functions.depositWager().build_transaction({
                    'from': acct.address,
                    'value': w3.to_wei(wager, 'ether'),
                    'gas': 300000,
                    'nonce': w3.eth.get_transaction_count(acct.address),
                })
                signed = acct.sign_transaction(tx)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                st.success(f"Deposited! Tx: {tx_hash.hex()[:12]}...")
            except Exception as e:
                st.error(f"Deposit failed: {e}")

    # Agent Settings
    st.header("Agent Settings")
    agent_name = st.text_input("Agent Name", "GambitZero")
    difficulty = st.slider("Stockfish Depth", 8, 20, 12)

# ====================== GAME ======================
if "board" not in st.session_state:
    st.session_state.board = chess.Board()
    st.session_state.game_history = []
    st.session_state.agent_persona = f"You are {agent_name}, a witty 0G chess agent."

board_svg = chess.svg.board(st.session_state.board, size=420)
st.markdown(f'<div style="display:flex;justify-content:center;">{board_svg}</div>', unsafe_allow_html=True)

col1, col2 = st.columns([3, 2])

with col1:
    move = st.text_input("Your move (UCI e.g. e2e4)", key="move_input")
    if st.button("Make Move", type="primary"):
        try:
            move_obj = chess.Move.from_uci(move)
            if move_obj in st.session_state.board.legal_moves:
                st.session_state.board.push(move_obj)
                st.session_state.game_history.append(move)

                if stockfish_path:
                    stockfish = Stockfish(path=stockfish_path)
                    stockfish.set_depth(difficulty)
                    stockfish.set_fen_position(st.session_state.board.fen())
                    best_move = stockfish.get_best_move()

                    if best_move:
                        st.session_state.board.push_uci(best_move)
                        st.session_state.game_history.append(best_move)
                        st.success(f"Agent played: {best_move}")

                        comment = get_agent_response(
                            f"Recent moves: {st.session_state.game_history[-6:]}. Short witty comment.",
                            st.session_state.agent_persona
                        )
                        st.info(f"💬 {agent_name}: {comment}")
                        st.rerun()
        except Exception as e:
            st.error(f"Error: {e}")

with col2:
    st.subheader("Game Status")
    if st.session_state.board.is_checkmate():
        winner = "Black (Agent)" if st.session_state.board.turn else "White (You)"
        st.error(f"🏆 Checkmate! {winner} wins!")
        save_game_to_0g_storage()

    st.subheader("💾 0G Storage")
    if st.button("Save Game to 0G Storage", type="primary"):
        save_game_to_0g_storage()

st.caption("0G-Native Project — Compute + Chain + Storage")
