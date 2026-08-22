import asyncio
from core.engine import ChatEngine

async def main():
    print("\n--- PriestyAI CLI Test Harness ---\n")
    test_context = """<context>
  <message id="1" user_id="1001" username="dev_user" display_name="Dev" is_invoking_user="true" timestamp="2026-08-22T17:00:00Z">
    How do I optimize a Docker build cache with BuildKit?
  </message>
</context>"""

    prompt = "How do I optimize a Docker build cache with BuildKit?"
    print(f"Testing Prompt: '{prompt}'\n")

    async for event_type, payload in ChatEngine.stream_chat(
        prompt=prompt,
        context_xml=test_context,
        bot_user_id=999999999
    ):
        if event_type == "ROUTED":
            print(f"[ROUTER] Target: {payload.target_model} | Thinking: {payload.thinking_level}")
            print(f"[ROUTER] Witty Statuses: {payload.witty_statuses}")
            print(f"[ROUTER] Reasoning: {payload.reasoning_summary}\n")
            print("[AI STREAM BEGINS]:")
        elif event_type == "CONTENT":
            print(payload, end="", flush=True)
        elif event_type == "ERROR":
            print(f"\n[ERROR]: {payload}")

    print("\n\n[✓] CLI Stream test completed successfully.\n")

if __name__ == "__main__":
    asyncio.run(main())