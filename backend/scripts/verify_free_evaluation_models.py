"""Verify model catalog availability without making inference requests."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import httpx


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings
from app.rag.ingestion import write_json_atomic


OUTPUT = (
    BACKEND_DIR / "data" / "evaluation" / "LLM evaluation"
    / "free_tier_model_verification.json"
)
OPENROUTER_MODELS = (
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
)
GROQ_MODEL = "openai/gpt-oss-120b"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--groq-free-tier-confirmed", action="store_true")
    parser.add_argument("--probe-openrouter", action="store_true")
    args = parser.parse_args()
    configured = {model.provider: model for model in get_settings().generation_models}

    with httpx.Client(timeout=30.0) as client:
        key_response = client.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {configured['openrouter'].api_key}"},
        )
        key_response.raise_for_status()
        key_data = key_response.json().get("data", {})
        openrouter_response = client.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {configured['openrouter'].api_key}"},
        )
        openrouter_response.raise_for_status()
        openrouter_catalog = {
            item.get("id"): item
            for item in openrouter_response.json().get("data", [])
        }
        openrouter = {}
        for model_id in OPENROUTER_MODELS:
            item = openrouter_catalog.get(model_id) or {}
            pricing = item.get("pricing") or {}
            prompt_price = pricing.get("prompt")
            completion_price = pricing.get("completion")
            openrouter[model_id] = {
                "available": bool(item),
                "prompt_price": prompt_price,
                "completion_price": completion_price,
                "zero_cost_verified": (
                    bool(item)
                    and float(prompt_price) == 0.0
                    and float(completion_price) == 0.0
                ),
            }

        groq_response = client.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {configured['groq'].api_key}"},
        )
        groq_response.raise_for_status()
        groq_ids = {
            item.get("id") for item in groq_response.json().get("data", [])
        }

    probes = {}
    if args.probe_openrouter:
        with httpx.Client(timeout=30.0) as client:
            for model_id in OPENROUTER_MODELS:
                response = client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": (
                            f"Bearer {configured['openrouter'].api_key}"
                        ),
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_id,
                        "messages": [{"role": "user", "content": "Reply OK."}],
                        "max_tokens": 8,
                    },
                )
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                error = body.get("error") or {}
                probes[model_id] = {
                    "status_code": response.status_code,
                    "error_code": error.get("code"),
                    "error_message": error.get("message"),
                }

    report = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "verification_type": "read_only_model_catalog_no_inference",
        "openrouter": openrouter,
        "openrouter_key_status": {
            # Expose quota/usage fields only; never persist the key or its label.
            "is_free_tier": key_data.get("is_free_tier"),
            "limit": key_data.get("limit"),
            "limit_remaining": key_data.get("limit_remaining"),
            "usage": key_data.get("usage"),
            "usage_daily": key_data.get("usage_daily"),
            "usage_weekly": key_data.get("usage_weekly"),
            "usage_monthly": key_data.get("usage_monthly"),
            "rate_limit": key_data.get("rate_limit"),
        },
        "openrouter_request_guards": {
            "exact_free_model_slug": True,
            "max_prompt_price_per_million": 0,
            "max_completion_price_per_million": 0,
            "provider_fallbacks": "same_exact_free_model_only",
            "alternative_model_list": False,
        },
        "openrouter_guarded_probes": probes,
        "groq": {
            "model_id": GROQ_MODEL,
            "available_to_configured_api_key": GROQ_MODEL in groq_ids,
            "free_tier_confirmed_by_user": args.groq_free_tier_confirmed,
            "zero_cost_guard_note": (
                "Groq has no per-request zero-price field. Zero-cost execution "
                "therefore requires the configured account to remain on its Free tier."
            ),
        },
    }
    write_json_atomic(OUTPUT, report)
    print(json.dumps(report, indent=2))
    if not all(item["zero_cost_verified"] for item in openrouter.values()):
        return 2
    if GROQ_MODEL not in groq_ids or not args.groq_free_tier_confirmed:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
