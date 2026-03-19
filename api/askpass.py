#!/usr/bin/env python3
"""
Git askpass script for password prompts.

This script is called by git when it needs a password. It communicates
with the DOP API to request a password from the UI and wait for the answer.

Usage:
    GIT_ASKPASS=/path/to/askpass.py GIT_ASKPASS_PROMPT=echo git clone ...

Environment variables:
    DOP_ASKPASS_JOB_ID: The job ID (required)
    DOP_ASKPASS_API_URL: The API URL (default: http://localhost:10818)
"""

import json
import os
import sys
import urllib.request
import urllib.error
import time
from pathlib import Path


ASKPASS_CANCELLED_MARKER = "__DOP_ASKPASS_CANCELLED__"


def request_password(job_id: str, prompt: str, api_url: str) -> str | None:
    """
    Request a password from the API and wait for the answer.

    Args:
        job_id: The job ID
        prompt: The prompt to display to the user
        api_url: The API base URL

    Returns:
        The password, or None if failed
    """
    # Step 1: Create the askpass request
    try:
        request_data = json.dumps({
            "job_id": job_id,
            "prompt": prompt
        }).encode('utf-8')

        req = urllib.request.Request(
            f"{api_url}/api/askpass/request",
            data=request_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode('utf-8'))
            request_id = result.get("request_id")

            if not request_id:
                return None
    except (urllib.error.URLError, json.JSONDecodeError, ValueError) as e:
        print(f"Failed to create askpass request: {e}", file=sys.stderr)
        return None

    # Step 2: Poll for the answer
    max_wait = 300  # 5 minutes timeout
    start = time.time()

    while time.time() - start < max_wait:
        try:
            req = urllib.request.Request(
                f"{api_url}/api/askpass/answer/{request_id}",
                method="GET",
                headers={"Content-Type": "application/json"}
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
                answer = result.get("answer")

                if answer is not None:
                    if answer == ASKPASS_CANCELLED_MARKER:
                        print("Askpass request cancelled by user", file=sys.stderr)
                        return None
                    return answer
        except (urllib.error.URLError, json.JSONDecodeError, ValueError):
            pass

        # Wait a bit before polling again
        time.sleep(0.5)

    return None


def main():
    """Main entry point for the askpass script."""
    job_id = os.environ.get("DOP_ASKPASS_JOB_ID")
    api_url = os.environ.get("DOP_ASKPASS_API_URL", "http://localhost:10818")

    if not job_id:
        print("Error: DOP_ASKPASS_JOB_ID environment variable not set", file=sys.stderr)
        sys.exit(1)

    # Git passes the prompt as the first argument
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Password: "

    # Request the password from the API
    password = request_password(job_id, prompt, api_url)

    if password is None:
        print("Error: Failed to get password", file=sys.stderr)
        sys.exit(1)

    # Print the password to stdout (git will read it)
    print(password)
    sys.exit(0)


if __name__ == "__main__":
    main()
