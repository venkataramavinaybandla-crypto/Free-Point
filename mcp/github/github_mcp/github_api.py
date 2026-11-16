"""Minimal GitHub REST client used by the GitHub MCP server.

The client never logs request headers, so the user's bearer token cannot leak
through error paths.
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

import httpx

MAX_LIST_ENTRIES = 100
MAX_FILE_BYTES = 512_000
MAX_FILE_CHARS = 120_000


class GitHubAPIError(RuntimeError):
    """A GitHub call failed. Messages never include credentials."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"GitHub API error {status_code}: {message}")
        self.status_code = status_code


class GitHubAPI:
    def __init__(self, token: str, base_url: str = "https://api.github.com", timeout: float = 15.0) -> None:
        if not token:
            raise GitHubAPIError(401, "A GitHub token is required.")
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "free-point-github-mcp",
            },
            follow_redirects=False,
        )

    async def __aenter__(self) -> "GitHubAPI":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code == 404:
            raise GitHubAPIError(404, "Not found.")
        if response.status_code >= 400:
            try:
                message = response.json().get("message", "request failed")
            except ValueError:
                message = "request failed"
            raise GitHubAPIError(response.status_code, str(message)[:200])
        if response.status_code == 204:
            return None
        return response.json()

    async def list_files(self, repo: str, path: str = "", ref: str = "") -> dict[str, Any]:
        params = {"ref": ref} if ref else {}
        data = await self._request("GET", f"/repos/{repo}/contents/{quote(path, safe='/')}", params=params)
        if isinstance(data, dict):
            data = [data]
        entries = [
            {
                "name": item.get("name", ""),
                "path": item.get("path", ""),
                "type": item.get("type", ""),
                "size": item.get("size", 0),
                "sha": item.get("sha", ""),
            }
            for item in data[:MAX_LIST_ENTRIES]
        ]
        return {"entries": entries, "truncated": len(data) > MAX_LIST_ENTRIES}

    async def read_file(self, repo: str, path: str, ref: str = "") -> dict[str, Any]:
        params = {"ref": ref} if ref else {}
        data = await self._request("GET", f"/repos/{repo}/contents/{quote(path, safe='/')}", params=params)
        if not isinstance(data, dict) or data.get("type") != "file":
            raise GitHubAPIError(400, "Path is not a file.")
        size = int(data.get("size", 0))
        if size > MAX_FILE_BYTES:
            raise GitHubAPIError(413, f"File exceeds the {MAX_FILE_BYTES} byte read cap.")
        content = data.get("content", "")
        if data.get("encoding") == "base64":
            try:
                raw = base64.b64decode(content)
            except ValueError as err:
                raise GitHubAPIError(422, "File content could not be decoded.") from err
        else:
            raw = str(content).encode("utf-8")
        text = raw.decode("utf-8", errors="replace")
        truncated = len(text) > MAX_FILE_CHARS
        return {
            "path": data.get("path", path),
            "sha": data.get("sha", ""),
            "size": size,
            "text": text[:MAX_FILE_CHARS],
            "truncated": truncated,
        }

    async def get_ref_sha(self, repo: str, branch: str) -> str | None:
        try:
            data = await self._request("GET", f"/repos/{repo}/git/ref/heads/{quote(branch, safe='/')}")
        except GitHubAPIError as err:
            if err.status_code == 404:
                return None
            raise
        return data["object"]["sha"]

    async def get_commit_tree(self, repo: str, sha: str) -> str:
        data = await self._request("GET", f"/repos/{repo}/git/commits/{quote(sha, safe='')}")
        return data["tree"]["sha"]

    async def create_blob(self, repo: str, content: str) -> str:
        data = await self._request("POST", f"/repos/{repo}/git/blobs", json={"content": content, "encoding": "utf-8"})
        return data["sha"]

    async def create_tree(self, repo: str, base_tree: str, path: str, blob_sha: str) -> str:
        data = await self._request(
            "POST",
            f"/repos/{repo}/git/trees",
            json={
                "base_tree": base_tree,
                "tree": [{"path": path, "mode": "100644", "type": "blob", "sha": blob_sha}],
            },
        )
        return data["sha"]

    async def create_commit(self, repo: str, message: str, tree: str, parents: list[str]) -> str:
        data = await self._request(
            "POST",
            f"/repos/{repo}/git/commits",
            json={"message": message[:200], "tree": tree, "parents": parents},
        )
        return data["sha"]

    async def create_ref(self, repo: str, branch: str, sha: str) -> None:
        await self._request("POST", f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})

    async def find_pull(self, repo: str, head: str) -> dict[str, Any] | None:
        owner = repo.split("/", 1)[0]
        data = await self._request(
            "GET",
            f"/repos/{repo}/pulls",
            params={"head": f"{owner}:{head}", "state": "all", "per_page": 10},
        )
        for pull in data:
            if pull.get("head", {}).get("ref") == head:
                return {
                    "number": pull.get("number"),
                    "url": pull.get("html_url", ""),
                    "draft": bool(pull.get("draft")),
                    "state": pull.get("state", ""),
                }
        return None

    async def create_pull(self, repo: str, head: str, base: str, title: str, body: str) -> dict[str, Any]:
        data = await self._request(
            "POST",
            f"/repos/{repo}/pulls",
            json={"head": head, "base": base, "title": title[:200], "body": body[:20_000], "draft": True},
        )
        return {
            "number": data.get("number"),
            "url": data.get("html_url", ""),
            "draft": bool(data.get("draft")),
            "state": data.get("state", "open"),
        }
