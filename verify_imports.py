"""Minimal check that LangGraph and LangChain import in this environment."""

def main() -> None:
    import langgraph  # noqa: F401
    import langchain  # noqa: F401

    print("imports ok: langgraph, langchain")


if __name__ == "__main__":
    main()
