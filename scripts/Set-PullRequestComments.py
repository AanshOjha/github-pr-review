#!/usr/bin/env python3
"""
Set-PullRequestComments.py

Reads AI review findings (`review-results.json`) and valid diff lines (`changes.json`).
Validates every line number against `ValidLines` to prevent GitHub API `422 Unprocessable Entity` errors,
checks for existing active threads to avoid duplicate spam, and submits a clean, unified Pull Request Review
via the GitHub REST API using `GITHUB_TOKEN`.
"""

import os
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def make_github_request(url, method="GET", payload=None, token=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Azure-AI-Foundry-PR-Review-Bot"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode("utf-8")
            return json.loads(res_body) if res_body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f"[GITHUB API ERROR] {method} {url} -> Status {e.code}: {err_body}")
        raise


def get_pr_number_and_sha(token, repo):
    # Check explicit env variable first
    pr_num = os.environ.get("PULL_REQUEST_NUMBER")
    head_sha = os.environ.get("PULL_REQUEST_HEAD_SHA")

    # Check GitHub Actions event payload
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).exists():
        try:
            with open(event_path, "r", encoding="utf-8") as f:
                event_data = json.load(f)
            if not pr_num and "pull_request" in event_data:
                pr_num = str(event_data["pull_request"]["number"])
            if not head_sha and "pull_request" in event_data and "head" in event_data["pull_request"]:
                head_sha = event_data["pull_request"]["head"]["sha"]
        except Exception as e:
            print(f"[WARNING] Could not parse GITHUB_EVENT_PATH: {e}")

    if not pr_num:
        print("[ERROR] PULL_REQUEST_NUMBER not found. Set PULL_REQUEST_NUMBER env var or run in GitHub Actions.")
        sys.exit(1)

    # If head_sha not found from event, query GitHub REST API
    if not head_sha:
        url = f"https://api.github.com/repos/{repo}/pulls/{pr_num}"
        pr_info = make_github_request(url, token=token)
        head_sha = pr_info.get("head", {}).get("sha")

    return int(pr_num), head_sha


def main():
    print("\n==========================================")
    print("  AI Review: Set-PullRequestComments")
    print("==========================================")

    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    if not token or not repo:
        print("[ERROR] GITHUB_TOKEN and GITHUB_REPOSITORY must be set.")
        sys.exit(1)

    pr_number, head_sha = get_pr_number_and_sha(token, repo)
    print(f"Target Repository: {repo}")
    print(f"Target PR Number:  #{pr_number}")
    print(f"Target Head SHA:   {head_sha}")

    review_path = Path(os.environ.get("REVIEW_RESULTS_PATH", "review-results.json"))
    changes_path = Path(os.environ.get("CHANGES_PATH", "changes.json"))

    if not review_path.exists() or not changes_path.exists():
        print("[ERROR] review-results.json or changes.json not found.")
        sys.exit(1)

    with open(review_path, "r", encoding="utf-8") as f:
        review_data = json.load(f)

    with open(changes_path, "r", encoding="utf-8") as f:
        changes_data = json.load(f)

    # Build lookup table of valid right-side line numbers per file: { "src/file.py": set([41, 42, 43]) }
    valid_lines_map = {}
    for item in changes_data.get("changedFiles", []):
        f_path = item.get("filePath")
        valid_lines_map[f_path] = set(item.get("validLines", []))

    # Fetch existing inline review comments to prevent duplicate noise across subsequent pushes
    print("Checking existing review comments for deduplication...")
    existing_comments_url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments"
    existing_comments = make_github_request(existing_comments_url, token=token)
    
    existing_markers = set()
    for comm in existing_comments:
        body = comm.get("body", "")
        # Look for our embedded marker `<!-- ai-review: {path}:{line} -->`
        if "<!-- ai-review:" in body:
            existing_markers.add((comm.get("path"), comm.get("line")))

    # Process findings and partition into valid inline vs out-of-bounds general feedback
    inline_comments = []
    general_notes = []
    summary = review_data.get("summary", "AI code review completed via Microsoft Foundry.")
    model_name = review_data.get("metadata", {}).get("model", "gpt-4.1")

    for finding in review_data.get("reviews", []):
        f_path = finding.get("fileName")
        l_num = finding.get("lineNumber")
        sev = finding.get("severity", "Medium")
        comm_text = finding.get("comment", "")

        if not f_path or not l_num:
            continue

        try:
            l_num = int(l_num)
        except ValueError:
            general_notes.append(f"- **[{sev}] (`{f_path}`)**: {comm_text}")
            continue

        # Deduplication check
        if (f_path, l_num) in existing_markers:
            print(f"  [SKIP DUP] Already commented on {f_path}:{l_num}")
            continue

        # Line validation against diff hunks
        valid_set = valid_lines_map.get(f_path)
        if valid_set and l_num in valid_set:
            # Valid inline comment!
            marker = f"<!-- ai-review: {f_path}:{l_num} -->"
            formatted_body = f"**[{sev} Severity]**\n\n{comm_text}\n\n{marker}"
            inline_comments.append({
                "path": f_path,
                "line": l_num,
                "side": "RIGHT",
                "body": formatted_body
            })
            print(f"  [INLINE] {f_path}:{l_num} ({sev})")
        else:
            # Out-of-bounds or file not found in patch — include in general review body
            general_notes.append(f"- **[{sev}] (`{f_path}:{l_num}`)**: {comm_text}")
            print(f"  [FALLBACK TO SUMMARY] {f_path}:{l_num} not in diff hunk valid lines.")

    # Construct the summary review body
    review_body = f"### 🤖 Azure AI Foundry (`{model_name}`) Code Review\n\n**Summary:** {summary}\n"
    
    if general_notes:
        review_body += "\n#### Additional Findings & General Notes\n" + "\n".join(general_notes) + "\n"

    review_body += "\n---\n*Automated review generated by [Microsoft Foundry / Azure OpenAI Pipeline](https://johnlokerse.dev/2026/01/06/automated-code-reviews-in-azure-devops-using-openai-models-powered-by-microsoft-foundry/) adapted for GitHub Actions.*"

    # Submit the PR review
    submit_url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/reviews"
    review_payload = {
        "commit_id": head_sha,
        "body": review_body,
        "event": "COMMENT",
        "comments": inline_comments
    }

    print(f"Submitting Pull Request Review ({len(inline_comments)} inline comments, {len(general_notes)} summary notes)...")
    try:
        response = make_github_request(submit_url, method="POST", payload=review_payload, token=token)
        print(f"\nSuccessfully submitted PR Review! Review ID: {response.get('id')}")
        print(f"Review URL: {response.get('html_url')}")
    except Exception as e:
        print(f"[ERROR] Failed to submit PR review: {e}")
        sys.exit(1)

    print("==========================================\n")


if __name__ == "__main__":
    main()
