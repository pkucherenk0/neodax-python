"""Phase 1 spike -- PASSED. Proves the mock-wallet mechanism ports to Python with zero Node/JS
bundling.

Ported from @johanneskares/wallet-mock's actual internals (see e2e/node_modules/@johanneskares/
wallet-mock/dist/{installMockWallet,createWallet}.js): the browser-side script is a tiny, generic
EIP-6963 "announce a fake wallet" shim with NO crypto in it -- every request() call forwards to
the test runner over page.exposeFunction(). Signing happens runner-side (Node+viem there,
Python+eth_account here) -- so there's nothing to bundle, the shim below is copied as plain JS.

Validated live: mint wallet -> install -> a simulated dapp discovers it via EIP-6963 ->
eth_requestAccounts returns the right address -> personal_sign returns a REAL signature ->
Account.recover_message confirms the signer. This becomes lib/wallet.py once e2e_py/'s real
page objects exist; kept here as-is (not wired into pytest yet) as the Phase 1 record.

Run: python3 e2e_py/spike_wallet_mock.py
"""
from eth_account import Account
from eth_account.messages import encode_defunct
from playwright.sync_api import sync_playwright

# generic EIP-6963 announce shim -- copied from the npm package's actual browser-side code
# (see docstring). `eip1193Request` is the Python-exposed function; wrapped in parens to
# actually IIFE (a bare `() => {}()` is a JS syntax error, not a call -- cost an hour to find).
WALLET_ANNOUNCE_JS = """
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

# what a real dapp does to discover a wallet (wagmi/AppKit do this internally) -- used here just
# to drive the provider from Python via page.evaluate, no real FE involved yet.
DISCOVER_AND_CALL_JS = """
async (request) => {
  const found = await new Promise((resolve) => {
    window.addEventListener("eip6963:announceProvider", (e) => resolve(e.detail.provider));
    window.dispatchEvent(new Event("eip6963:requestProvider"));
  });
  return await found.request(request);
}
"""


def main() -> None:
    Account.enable_unaudited_hdwallet_features()
    account, mnemonic = Account.create_with_mnemonic()
    print(f"minted throwaway wallet: {account.address}")

    def eip1193_request(request: dict):
        """runner-side signing -- the browser shim above never sees a private key."""
        method = request.get("method")
        params = request.get("params") or []
        if method in ("eth_requestAccounts", "eth_accounts"):
            return [account.address]
        if method == "personal_sign":
            message_hex = params[0]
            message_bytes = bytes.fromhex(message_hex[2:] if message_hex.startswith("0x") else message_hex)
            signed = Account.sign_message(encode_defunct(message_bytes), private_key=account.key)
            sig = signed.signature.hex()
            return sig if sig.startswith("0x") else "0x" + sig
        raise NotImplementedError(f"spike doesn't handle {method} yet")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.expose_function("eip1193Request", eip1193_request)
        page.add_init_script(script=WALLET_ANNOUNCE_JS)
        page.goto("data:text/html,<html><body>spike</body></html>")

        # 1 -- a "dapp" discovers the wallet and requests accounts, exactly like wagmi/AppKit would.
        accounts = page.evaluate(DISCOVER_AND_CALL_JS, {"method": "eth_requestAccounts"})
        assert accounts == [account.address], f"expected [{account.address}], got {accounts}"
        print(f"eth_requestAccounts -> {accounts}  (matches minted address: OK)")

        # 2 -- a "dapp" asks for a personal_sign, same as this app's connect-handshake signature.
        message = "sign in to nimbus (spike test)"
        message_hex = "0x" + message.encode().hex()
        signature = page.evaluate(DISCOVER_AND_CALL_JS, {"method": "personal_sign", "params": [message_hex, account.address]})
        print(f"personal_sign -> {signature}")

        # 3 -- prove it's a REAL signature: recover the signer and confirm it's our address.
        recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
        assert recovered == account.address, f"recovered {recovered}, expected {account.address}"
        print(f"recovered signer: {recovered}  (matches: OK)")

        browser.close()

    print("\nSPIKE PASSED: mock-wallet mechanism works from Python, zero Node/JS bundling needed.")


if __name__ == "__main__":
    main()
