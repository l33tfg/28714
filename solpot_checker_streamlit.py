import streamlit as st
import asyncio
import aiohttp
from datetime import datetime
from typing import List, Dict, Optional
import uuid
import logging

# Logging (invisible to user, for debugging)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
RPC_ENDPOINT = "https://maximum-virulent-gadget.solana-mainnet.quiknode.pro/2b8372ac220a9f255020d4d8a27ecd50c2ad672e/"
AMOUNT_TOLERANCE = 0.01
PAYOUTS_WALLET = "F5YtngCQs6QCUdy2vqT6hMtFyNkLpkJSTQF2WZKV1y8e"
JACKPOT_WALLET = "CC4524TTSUScbYFhAecjBXQumQcn627EpiDUauSyr3EY"
MAX_RETRIES = 6
DEFAULT_TX_LIMIT = 75

# RPC helpers
async def make_jsonrpc_request(session: aiohttp.ClientSession, payload: Dict, retries: int = MAX_RETRIES) -> Dict:
    for attempt in range(retries):
        try:
            async with session.post(RPC_ENDPOINT, json=payload) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 429:
                await asyncio.sleep((2 ** attempt) + 0.1)
                continue
            elif e.status == 401:
                raise Exception("401 Unauthorized: Check your RPC endpoint")
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            raise
    raise Exception("Max retries exceeded for 429 error")

async def get_signatures(session: aiohttp.ClientSession, address: str, limit: int = DEFAULT_TX_LIMIT) -> List[Dict]:
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "getSignaturesForAddress",
        "params": [address, {"limit": limit}]
    }
    response = await make_jsonrpc_request(session, payload)
    return response.get("result", [])

async def get_transaction(session: aiohttp.ClientSession, signature: str) -> Optional[Dict]:
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "getTransaction",
        "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
    }
    response = await make_jsonrpc_request(session, payload)
    return response.get("result")

def lamports_to_sol(lamports: int) -> float:
    return lamports / 1_000_000_000

async def scan(wallet: str, target_amount: float, tx_limit: int, is_payout: bool):
    results = []
    min_amt = target_amount - AMOUNT_TOLERANCE
    max_amt = target_amount + AMOUNT_TOLERANCE

    async with aiohttp.ClientSession() as session:
        signatures = await get_signatures(session, wallet, tx_limit)
        for i, sig in enumerate(signatures):
            tx = await get_transaction(session, sig["signature"])
            if not tx or tx.get("meta", {}).get("err"):
                continue

            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
            pre_balances = tx.get("meta", {}).get("preBalances", [])
            post_balances = tx.get("meta", {}).get("postBalances", [])

            try:
                wallet_idx = next(i for i, acc in enumerate(accounts) if acc["pubkey"] == wallet)
            except StopIteration:
                continue

            delta = lamports_to_sol(
                pre_balances[wallet_idx] - post_balances[wallet_idx] if is_payout else post_balances[wallet_idx] - pre_balances[wallet_idx]
            )

            if min_amt <= delta <= max_amt:
                timestamp = datetime.utcfromtimestamp(tx["blockTime"]).strftime('%Y-%m-%d %H:%M:%S')
                other_party = next((acc["pubkey"] for acc in accounts if acc["pubkey"] != wallet), "unknown")

                results.append({
                    "timestamp": timestamp,
                    "amount": delta,
                    "other": other_party,
                    "signature": sig["signature"]
                })
                break

    return results

# Streamlit UI
st.title("🔍 SolPot Wallet Scanner")

option = st.radio("What do you want to do?", [
    "Find the wallet of the last jackpot winner",
    "Identify a wallet that entered the current jackpot"
])

amount = st.number_input("Enter the exact amount of SOL (from SolPot)", min_value=0.01, step=0.01)
tx_limit = st.slider("Transactions to scan", 10, 1000, DEFAULT_TX_LIMIT)

if st.button("Start Scan"):
    is_payout = option == "Find the wallet of the last jackpot winner"
    wallet = PAYOUTS_WALLET if is_payout else JACKPOT_WALLET
    with st.spinner("Scanning transactions..."):
        results = asyncio.run(scan(wallet, amount, tx_limit, is_payout))

    if results:
        for r in results:
            st.success(f"✅ Found matching transaction at {r['timestamp']}")
            st.markdown(f"**Amount:** {r['amount']:.4f} SOL")
            st.markdown(f"**Other Wallet:** [{r['other']}](https://solscan.io/account/{r['other']})")
            st.markdown(f"**Signature:** [{r['signature']}](https://solscan.io/tx/{r['signature']})")
    else:
        st.warning("No matching transaction found. Try increasing transaction range or adjusting the amount.")
