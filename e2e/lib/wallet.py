"""mock EIP-1193 wallet. real signatures, no browser extension. shim below copied from
@johanneskares/wallet-mock internals (no crypto in it) -- signing happens here, via
eth_account. see ../spike_wallet_mock.py for proof, no Node/JS bundling needed.
"""
from __future__ import annotations

from eth_account import Account
from eth_account.messages import encode_defunct
from playwright.sync_api import Page

Account.enable_unaudited_hdwallet_features()

# EIP-6963 "announce fake wallet" shim. forwards request() to python callback below via
# expose_function. wrapped in parens -- `() => {}()` is JS syntax error, not a call.
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
    """installs mock wallet for `mnemonic` on `page`. call before goto() -- add_init_script
    only applies to next navigation. chain mainnet (see eth_chainId below), doesn't matter,
    app never does real on-chain read/write."""
    account = Account.from_mnemonic(mnemonic)

    def eip1193_request(request: dict):
        """runner-side signing, browser shim never sees private key. method coverage mirrors
        wallet-mock's createWallet.js. missing wallet_getPermissions/wallet_switchEthereumChain/
        eth_chainId hung the first live run 20s -- wagmi calls these before eth_requestAccounts."""
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
            return "0x1"  # mainnet, see install_wallet_for
        if method == "personal_sign":
            message_hex = params[0]
            message_bytes = bytes.fromhex(message_hex[2:] if message_hex.startswith("0x") else message_hex)
            signed = Account.sign_message(encode_defunct(message_bytes), private_key=account.key)
            sig = signed.signature.hex()
            return sig if sig.startswith("0x") else "0x" + sig
        # eth_signTypedData_v4 unimplemented -- matches original's own gap, never needed. see README.
        raise NotImplementedError(f"mock wallet doesn't handle {method} yet")

    page.expose_function("eip1193Request", eip1193_request)
    page.add_init_script(script=_WALLET_ANNOUNCE_JS)
