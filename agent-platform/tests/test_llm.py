import json, os, unittest
from unittest import mock
from app import http, llm


class T(unittest.TestCase):
    def test_openai_request_and_parse(self):
        seen = {}
        def tr(method, url, headers=None, json_body=None, timeout=0):
            seen.update(url=url, h=headers, b=json_body)
            return json.dumps({"choices": [{"message": {"content": " سلام "}}]})
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k"}):
            out = llm.OpenAIClient(transport=tr).complete("sys", "usr", 50)
        self.assertEqual(out, "سلام")
        self.assertTrue(seen["url"].endswith("/chat/completions"))
        self.assertEqual(seen["h"]["Authorization"], "Bearer k")
        self.assertEqual(seen["b"]["messages"][0]["role"], "system")

    def test_http_error_becomes_llmerror(self):
        def tr(*a, **k): raise http.HttpError(429, "slow down")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k"}), self.assertRaises(llm.LLMError):
            llm.OpenAIClient(transport=tr).complete("s", "u")

    def test_bad_payload(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k"}), self.assertRaises(llm.LLMError):
            llm.OpenAIClient(transport=lambda *a, **k: "{}").complete("s", "u")

    def test_missing_key(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(llm.LLMError):
            llm.OpenAIClient().complete("s", "u")

    def test_provider_selection(self):
        llm.set_client(None)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k"}, clear=True):
            self.assertIsInstance(llm.get_client(), llm.OpenAIClient)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k", "ANTHROPIC_API_KEY": "a"}, clear=True):
            self.assertIsInstance(llm.get_client(), llm.AnthropicClient)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k", "ANTHROPIC_API_KEY": "a", "APP_LLM_PROVIDER": "openai"}, clear=True):
            self.assertIsInstance(llm.get_client(), llm.OpenAIClient)


if __name__ == "__main__":
    unittest.main()
