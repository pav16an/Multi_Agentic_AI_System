import argparse
import json
import os
from pathlib import Path

from document_processor import DocumentProcessor
from llm_providers import (
    DEFAULT_MODELS,
    ProviderError,
    get_provider_adapter,
    normalize_provider,
    resolve_model,
)
from orchestrator import Orchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze a document with provider-selected AI agents."
    )
    parser.add_argument("document", type=Path, help="Path to .txt, .pdf, or .docx document")
    parser.add_argument(
        "--provider",
        default="groq",
        choices=list(DEFAULT_MODELS.keys()),
        help="LLM provider to use",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Model name. If omitted, provider default is used.",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="Provider API key. If omitted, uses PROVIDER_API_KEY env var.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    file_path = args.document

    if not file_path.exists():
        print(f"File not found: {file_path}")
        return

    try:
        provider = normalize_provider(args.provider)
        model = resolve_model(provider, args.model)
        api_key = (args.api_key or os.getenv(f"{provider.upper()}_API_KEY", "")).strip()
        if not api_key:
            print(
                "Missing API key. Pass --api-key or set "
                f"{provider.upper()}_API_KEY in your environment."
            )
            return

        print(f"Processing: {file_path.name}")
        print(f"Provider: {provider} | Model: {model}")

        text = DocumentProcessor.load_document(file_path)
        text = DocumentProcessor.preprocess(text)

        llm_provider = get_provider_adapter(provider)
        orchestrator = Orchestrator(llm_provider=llm_provider, model=model)
        result = orchestrator.process_document(text=text, api_key=api_key)

        print("\n" + "=" * 60)
        print("RESULTS")
        print("=" * 60)
        print(json.dumps(result.model_dump(), indent=2))

    except ProviderError as exc:
        print(f"Provider configuration error: {exc}")
    except Exception as exc:
        print(f"Analysis failed: {exc}")


if __name__ == "__main__":
    main()
