import os
import requests
import traceback
from dotenv import load_dotenv

load_dotenv()
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
MODEL_NAME = "claude-3-5-haiku-20241022"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


def generate_sql_query(schema: str, question: str) -> tuple[str, str]:
    """
    Given a database schema and a user question, use Claude to generate either a SQL query or a natural language explanation.
    Returns a tuple: ("sql" or "text", content)
    """
    prompt = (
        "You are a helpful AI assistant for a database application. "
        "If the user asks about the database structure, respond in natural language. "
        "If the user asks for data or analysis, respond ONLY with a valid SQL query.\n\n"
        f"Schema:\n{schema}\n\n"
        f"User Question: {question}\n"
    )
    try:
        response = requests.post(
            API_URL,
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": API_VERSION,
                "content-type": "application/json"
            },
            json={
                "model": MODEL_NAME,
                "max_tokens": 500,
                "temperature": 0.2,
                "system": "You are a helpful assistant for a database application.",
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=60
        )
        if response.status_code != 200:
            return ("text", f"❌ API returned status {response.status_code}: {response.text}")
        data = response.json()
        # Claude 3.5 returns the message in data["content"][0]["text"]
        output = data.get("content", [{}])[0].get("text", "").strip()
        if not output:
            return ("text", "⚠️ Claude did not return a response. Try rephrasing your question or check your usage quota.")
        is_sql = output.lower().startswith("select") or output.lower().startswith("with")
        return ("sql", output) if is_sql else ("text", output)
    except Exception:
        return ("text", f"❌ Claude API error:\n{traceback.format_exc()}")
