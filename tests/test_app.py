import unittest
from unittest.mock import MagicMock
from fastapi import HTTPException
from app.main import ChatRequest, health, chat


class FastAPISmokeTests(unittest.IsolatedAsyncioTestCase):

    async def test_health_returns_ok(self):
        self.assertEqual(await health(), {"status": "ok"})

    async def test_chat_rejects_blank_message_before_agent_access(self):
        with self.assertRaises(HTTPException) as exc:
            await chat(ChatRequest(message="   "), agent=MagicMock())
        self.assertEqual(exc.exception.status_code, 400)
        self.assertEqual(exc.exception.detail, "message must not be empty")


if __name__ == "__main__":
    unittest.main()