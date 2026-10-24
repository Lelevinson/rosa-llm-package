"""Run from the project root: uv run python examples/chat.py"""

from rosa_llm import Message, create_client

with create_client() as client:
    history = [Message("user", "Explain ROS 2 in one sentence.")]
    first = client.generate(history)
    print(first.text)
    history += [first.message, Message("user", "What is a node? One sentence, please.")]
    print(client.generate(history).text)
