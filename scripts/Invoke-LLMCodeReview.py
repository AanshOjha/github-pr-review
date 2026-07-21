#!/usr/bin/env python3
"""
Invoke-LLMCodeReview.py

Reads structured Git changes (changes.json) and system prompt instructions (prompts/Generic.codereviewprompt.md),
authenticates with Azure AI Foundry / Microsoft Foundry using DefaultAzureCredential, and invokes the chat model (`gpt-4.1`).
Outputs structured JSON review results to `review-results.json`.
"""

import os
import sys
import json
import re
from datetime import datetime
from pathlib import Path

# Optional: Load environment variables from .env if present (useful for local development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI, OpenAIError


def main():
    print("\n==========================================")
    print("    AI Review: Invoke-LLMCodeReview")
    print("==========================================")

    changes_path = Path(os.environ.get("CHANGES_PATH", "changes.json"))
    prompt_path = Path(os.environ.get("PROMPT_PATH", "prompts/Generic.codereviewprompt.md"))
    output_path = Path(os.environ.get("REVIEW_RESULTS_PATH", "review-results.json"))

    if not changes_path.exists():
        print(f"[ERROR] Changes file not found at {changes_path}. Run Get-CodeChanges.ps1 first.")
        sys.exit(1)

    if not prompt_path.exists():
        print(f"[ERROR] Prompt template not found at {prompt_path}.")
        sys.exit(1)

    with open(changes_path, "r", encoding="utf-8") as f:
        changes_data = json.load(f)

    if changes_data.get("totalFilesChanged", 0) == 0:
        print("[INFO] Zero changed files detected. Skipping LLM invocation.")
        empty_review = {
            "summary": "No code changes were detected that require AI review.",
            "reviews": [],
            "metadata": {
                "model": "skipped",
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(empty_review, f, indent=2)
        return

    with open(prompt_path, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    # Construct user prompt payload containing only the changed files and their valid line numbers
    user_prompt_payload = {
        "sourceBranch": changes_data.get("sourceBranch"),
        "targetBranch": changes_data.get("targetBranch"),
        "totalFilesChanged": changes_data.get("totalFilesChanged"),
        "filesToReview": changes_data.get("changedFiles", [])
    }
    user_prompt_text = (
        "Please review the following Pull Request code changes. "
        "Each file includes its diff patch and the exact list of `validLines` where comments can be posted.\n\n"
        + json.dumps(user_prompt_payload, indent=2)
    )

    # Azure AI Foundry Endpoint and Model setup
    endpoint = os.environ.get("FOUNDRY_ENDPOINT")
    if not endpoint:
        print("[ERROR] FOUNDRY_ENDPOINT environment variable/secret is not set.")
        sys.exit(1)

    deployment_name = os.environ.get("FOUNDRY_DEPLOYMENT", "gpt-4.1")
    cred_url = os.environ.get("AZURE_CRED_URL") or os.environ.get("DefaultAzureCredentialURL") or "https://ai.azure.com/.default"
    api_key_env = os.environ.get("API_FOUNDRY_KEY") or os.environ.get("FOUNDRY_API_KEY")

    print("Endpoint:   [HIDDEN / SECURE]")
    print(f"Deployment: {deployment_name}")

    try:
        if api_key_env:
            print("Authenticating via Foundry API Key (API_FOUNDRY_KEY)...")
            api_auth = api_key_env
        else:
            print(f"Authenticating via DefaultAzureCredential ({cred_url})...")
            api_auth = get_bearer_token_provider(DefaultAzureCredential(), cred_url)

        client = OpenAI(
            base_url=endpoint,
            api_key=api_auth
        )
    except Exception as auth_err:
        print(f"[ERROR] Failed to initialize Foundry / Azure credential: {auth_err}")
        print("Tip: Ensure API_FOUNDRY_KEY is set in repository secrets, or `az login` / DefaultAzureCredential is available.")
        sys.exit(1)

    print(f"Sending diff ({changes_data.get('totalFilesChanged')} files) to Microsoft Foundry model ({deployment_name})...")

    try:
        response = client.chat.completions.create(
            model=deployment_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt_text}
            ],
            response_format={"type": "json_object"},
            temperature=0.2
        )
        raw_content = response.choices[0].message.content.strip()
    except OpenAIError as api_err:
        print(f"[ERROR] OpenAI / Microsoft Foundry API call failed: {api_err}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Unexpected error during model invocation: {e}")
        sys.exit(1)

    # Clean markdown fences if the model wrapped the JSON (` ```json ... ``` `)
    clean_content = re.sub(r"^```json\s*|\s*```$", "", raw_content, flags=re.MULTILINE).strip()

    try:
        parsed_review = json.loads(clean_content)
    except json.JSONDecodeError as json_err:
        print(f"[WARNING] Failed to parse model response as pure JSON: {json_err}")
        print("Raw response from model:")
        print(raw_content)
        # Fallback wrapper so downstream scripts don't crash
        parsed_review = {
            "summary": "AI review completed, but the response formatting encountered a parsing error.",
            "reviews": [],
            "raw_text": raw_content
        }

    # Attach metadata
    parsed_review["metadata"] = {
        "model": deployment_name,
        "endpoint": endpoint,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(parsed_review, f, indent=2)

    num_findings = len(parsed_review.get("reviews", []))
    print(f"\nSuccessfully generated AI review -> Found {num_findings} issues -> Saved to {output_path}")
    print("==========================================\n")


if __name__ == "__main__":
    main()
