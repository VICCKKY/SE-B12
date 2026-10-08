from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4o-mini")

prompts = [
    "Explain LangChain in one simple sentence.",
    "Explain the difference between LangChain and LangSmith in five short bullet points.",
    "Explain how LangSmith tracing helps debug an AI application in about 250 words."
]

for number, prompt in enumerate(prompts, start=1):
    print(f"\n--- Run {number} ---")
    print("Prompt:", prompt)

    response = llm.invoke(prompt)

    print("Answer:", response.content)
