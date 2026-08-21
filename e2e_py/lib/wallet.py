"""Mock EIP-1193 wallet -- real signatures, no browser extension. See ../spike_wallet_mock.py
for how this was proven out and why there's no Node/JS bundling involved: the browser-side
shim below is generic (copied from @johanneskares/wallet-mock's actual internals, which has no
crypto in it at all) -- signing happens here, in Python, via eth_account.
"""
from __future__ import annotations

from eth_account import Account
from eth_account.messages import encode_defunct
from playwright.sync_api import Page

Account.enable_unaudited_hdwallet_features()

# generic EIP-6963 "announce a fake wallet" shim -- forwards every request() to the Python
# callback registered below via page.expose_function. Wrapped in parens to actually IIFE
# (`() => {}()` is a JS syntax error, not a call).
_WALLET_ANNOUNCE_JS = """
(() => {
  function announceMockWallet() {
    const provider = {
      request: async (request) => eip1193Request(request),
      on: () => {},
      removeListener: () => {},
    };
    const info = {
      uuid: "11111111-1111-1111-1111-111111111111",
      name: "Python Mock Wallet",
      icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'></svg>",
      rdns: "com.example.python-mock-wallet",
    };
    window.dispatchEvent(new CustomEvent("eip6963:announceProvider", {
      detail: Object.freeze({ info, provider }),
    }));
  }
  announceMockWallet();
  window.addEventListener("eip6963:requestProvider", announceMockWallet);
  window.addEventListener("DOMContentLoaded", announceMockWallet);
})();
"""


def install_wallet_for(page: Page, mnemonic: str) -> None:
    """Installs the mock wallet for `mnemonic` on `page` -- call before page.goto(), same as
    the old wallet.ts (add_init_script only applies to the next navigation). Chain choice
    (mainnet, see eth_chainId below) doesn't matter: this app never does a real on-chain
    read/write, it's just a stable default matching the old wallet.ts's own choice."""
    account = Account.from_mnemonic(mnemonic)

    def eip1193_request(request: dict):
        """Runner-side signing -- the browser shim never sees a private key. Method coverage
        mirrors @johanneskares/wallet-mock's createWallet.js exactly (see e2e/node_modules/
        @johanneskares/wallet-mock/dist/createWallet.js) -- the FIRST live run of this port
        missed wallet_getPermissions/wallet_switchEthereumChain/eth_chainId (wagmi calls these
        during its own connector init, before ever calling eth_requestAccounts) and hung
        forever waiting for a connected-state signal that never arrived, since the original's
        real viem walletClient answers these silently -- my port just threw instead."""
        method = request.get("method")
        params = request.get("params") or []
        if method in ("eth_requestAccounts", "eth_accounts"):
            return [account.address]
        if method in ("wallet_requestPermissions", "wallet_revokePermissions"):
            return [{"parentCapability": "eth_accounts"}]
        if method == "wallet_getPermissions":
            return []
        if method == "wallet_switchEthereumChain":
            return None
        if method == "eth_chainId":
            return "0x1"  # mainnet -- this app never does a real on-chain read/write, see install_wallet_for
        if method == "personal_sign":
            message_hex = params[0]
            message_bytes = bytes.fromhex(message_hex[2:] if message_hex.startswith("0x") else message_hex)
            signed = Account.sign_message(encode_defunct(message_bytes), private_key=account.key)
            sig = signed.signature.hex()
            return sig if sig.startswith("0x") else "0x" + sig
        # eth_signTypedData_v4 not implemented -- matches the old wallet-mock's own gap (see
        # ../README.md "Known issues"): hasn't been needed by this app's connect/order flows.
        raise NotImplementedError(f"mock wallet doesn't handle {method} yet")

    page.expose_function("eip1193Request", eip1193_request)
    page.add_init_script(script=_WALLET_ANNOUNCE_JS)
