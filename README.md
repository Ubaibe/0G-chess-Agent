# 0G Chess Agent ♟️

**AI-Native Personalized Chess Agent with Wallet + Onchain Wagering + Decentralized Storage on 0G**

A fully **0G-native** chess experience built for the 0G Hackathon (June 2026). Play against a witty AI agent powered by 0G Compute, wager tokens using your agent's own wallet (you hold the keys) and permanently save game records on 0G Storage.

## ✨ Key 0G Features (Why it's truly AI-native)

- **0G Compute**: Agent personality, real-time trash talk, and post-game analysis via verifiable LLM inference.
- **0G Chain**: Onchain wagering (deposit → play → auto-settle on checkmate) using a custom Solidity contract.
- **0G Storage**: Every game is saved as a permanent PGN file with Merkle Root proof.
- **User-Custodial Agent Wallet**: Each player gets a unique EVM wallet. You are the sole custodian (seed phrase shown once).

Without 0G, this would just be a regular chess app — 0G makes the agent persistent, ownable, and verifiable.

## 🎮 How to Play

1. Generate your **Agent Wallet** (sidebar)
2. Copy the **24-word seed phrase** and back it up
3. Paste your private key (session-only) to enable signing
4. Deposit a wager (e.g. 0.01 A0G)
5. Play chess using UCI notation (e.g. `e2e4`)
6. After checkmate → game auto-settles onchain
7. Save the full game record to **0G Storage**
8. Tokens won by AI agent is automatically staked and deligated to validators to earn users **Yield**

## 🚀 Quick Start

```bash
# 1. Clone repo
git clone <your-repo-url>
cd zero-g-chess-agent

# 2. Setup environment
python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate

pip install -r requirements.txt

# 3. Create .env file
echo "PRIVATE_KEY=0xYOUR_TESTNET_PRIVATE_KEY_HERE" > .env