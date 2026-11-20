"""Gateway request headers (OpenCode Go session header + product User-Agent)."""

import unittest

from steward.providers.gateways import gateway_headers

GO_CHAT_URL = "https://opencode.ai/inference/go/v1/chat/completions"
GO_MODELS_URL = "https://opencode.ai/inference/go/v1/models"


class TestGatewayHeaders(unittest.TestCase):
    def test_opencode_host_gets_session_and_user_agent(self) -> None:
        headers = gateway_headers(GO_CHAT_URL, "session-42")
        self.assertEqual(set(headers), {"x-opencode-session", "User-Agent"})
        self.assertTrue(headers["User-Agent"].startswith("steward/"))

    def test_session_token_is_a_stable_hash_of_the_session_id(self) -> None:
        first = gateway_headers(GO_CHAT_URL, "session-42")["x-opencode-session"]
        second = gateway_headers(GO_MODELS_URL, "session-42")["x-opencode-session"]
        self.assertEqual(first, second)
        self.assertNotIn("session-42", first)  # raw ids never leave the process
        self.assertEqual(len(first), 32)

    def test_requests_outside_a_conversation_stay_stable(self) -> None:
        fallback = gateway_headers(GO_CHAT_URL)["x-opencode-session"]
        self.assertEqual(fallback, gateway_headers(GO_CHAT_URL)["x-opencode-session"])
        self.assertEqual(len(fallback), 32)

    def test_other_hosts_are_untouched(self) -> None:
        self.assertEqual(gateway_headers("https://api.deepseek.com/v1/chat/completions"), {})
        self.assertEqual(gateway_headers("https://evil-opencode-ai.example.com/v1"), {})

    def test_subdomains_match_the_gateway_suffix(self) -> None:
        headers = gateway_headers("https://api.opencode.ai/v1/models", "s")
        self.assertIn("x-opencode-session", headers)


if __name__ == "__main__":
    unittest.main()
