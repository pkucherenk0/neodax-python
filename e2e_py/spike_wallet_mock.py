"""phase 1 spike -- passed. proves mock-wallet mechanism ports to python, zero Node/JS bundling.

wallet-mock's browser script is generic EIP-6963 shim, no crypto -- every request() forwards to
runner over page.exposeFunction(). signing runner-side (node+viem there, python+eth_account
here) -- nothing to bundle.

validated live: mint wallet -> install -> dapp discovers via EIP-6963 -> eth_requestAccounts
returns right address -> personal_sign returns real sig -> recover_message confirms signer.
became lib/wallet.py once real page objects existed; kept as phase 1 record, not wired to pytest.

run: python3 e2e_py/spike_wallet_mock.py
"""
from eth_account import Account
from eth_account.messages import encode_defunct
from playwright.sync_api import sync_playwright

# EIP-6963 announce shim, copied from npm package browser code. eip1193Request is python-
# exposed fn. wrapped in parens to IIFE -- bare `() => {}()` is JS syntax error, not a call.
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

# real dapp wallet discovery (wagmi/AppKit do this internally) -- drives provider from python
# via page.evaluate, no real FE yet.
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
        """runner-side signing. browser shim never sees private key."""
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

        # 1 -- dapp discovers wallet, requests accounts, like wagmi/AppKit.
        accounts = page.evaluate(DISCOVER_AND_CALL_JS, {"method": "eth_requestAccounts"})
        assert accounts == [account.address], f"expected [{account.address}], got {accounts}"
        print(f"eth_requestAccounts -> {accounts}  (matches minted address: OK)")

        # 2 -- dapp asks personal_sign, same as app's connect-handshake sig.
        message = "sign in to nimbus (spike test)"
        message_hex = "0x" + message.encode().hex()
        signature = page.evaluate(DISCOVER_AND_CALL_JS, {"method": "personal_sign", "params": [message_hex, account.address]})
        print(f"personal_sign -> {signature}")

        # 3 -- prove real sig: recover signer, confirm our address.
        recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
        assert recovered == account.address, f"recovered {recovered}, expected {account.address}"
        print(f"recovered signer: {recovered}  (matches: OK)")

        browser.close()

    print("\nSPIKE PASSED: mock-wallet mechanism works from Python, zero Node/JS bundling needed.")


if __name__ == "__main__":
    main()
