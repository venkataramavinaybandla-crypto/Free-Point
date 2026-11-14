"""CLI script to generate single-use Free Point invite codes."""

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Ensure repository root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.app.auth.tokens import generate_invite_code, hash_sha256
from harness.app.store.factory import get_invite_store


async def main() -> None:
    parser = argparse.ArgumentParser(description="Create single-use Free Point invite code")
    parser.add_argument("--days", type=int, default=7, help="Expiry in days (default: 7)")
    args = parser.parse_args()

    code = generate_invite_code()
    code_hash = hash_sha256(code)
    expires_at = int(time.time()) + (args.days * 86400)

    store = get_invite_store()
    await store.create_invite(code_hash=code_hash, expires_at=expires_at)
    print(code)


if __name__ == "__main__":
    asyncio.run(main())
