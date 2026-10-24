"""Paperless-ngx client for the lecture poller (PLAN §4.1). Reads originals, notes, and tags. Writes the pipeline
tags, uploads from the Shortcut, and, when a new copy of a lecture replaces an old one, its title, notes, and tags
(the old copy goes to the trash)."""

from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import httpx

DONE, ERROR, SKIP, OPT_IN = "mnemosyne-done", "mnemosyne-error", "mnemosyne-skip", "mnemosyne"


@dataclass(frozen=True)
class Doc:
    id: int
    title: str
    tags: frozenset[int]
    document_type: int | None
    added: str


class Paperless:
    def __init__(self, url: str, token: str, public_url: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.public_url = (public_url or url).rstrip("/")
        self._headers = {"Authorization": f"Token {token}", "Accept": "application/json"}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.url, headers=self._headers, timeout=120)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._client() as http:
            r = await http.get(path, params=params)
            r.raise_for_status()
            return dict(r.json())

    async def _id_by_name(self, kind: str, name: str) -> int | None:
        found = await self._get(f"/api/{kind}/", {"name__iexact": name})
        results: list[dict[str, Any]] = found.get("results") or []
        return int(results[0]["id"]) if results else None

    async def tag_id(self, name: str, create: bool = False) -> int | None:
        tid = await self._id_by_name("tags", name)
        if tid is None and create:
            async with self._client() as http:
                r = await http.post("/api/tags/", json={"name": name, "matching_algorithm": 0})
                r.raise_for_status()
                tid = int(r.json()["id"])
        return tid

    async def document_type_id(self, name: str) -> int | None:
        return await self._id_by_name("document_types", name)

    async def documents(self, **query: Any) -> list[Doc]:
        params: dict[str, Any] = {"page_size": 100, "ordering": "added"}
        for k, v in query.items():
            params[k] = ",".join(str(x) for x in cast(list[Any], v)) if isinstance(v, list) else v
        page = await self._get("/api/documents/", params)
        results: list[dict[str, Any]] = page.get("results") or []
        return [self._doc(d) for d in results]

    @staticmethod
    def _doc(d: dict[str, Any]) -> Doc:
        tags: list[Any] = d.get("tags") or []
        return Doc(int(d["id"]), str(d["title"]), frozenset(int(t) for t in tags), d.get("document_type"),
                   str(d.get("added", "")))

    async def document(self, doc_id: int) -> Doc:
        return self._doc(await self._get(f"/api/documents/{doc_id}/"))

    async def candidates(self, semester: int, course_tags: list[int], default_types: list[int], excluded: list[int],
                         go_live: date, opt_in: int | None) -> list[Doc]:
        """Lectures and returned exams added since go-live (in by default), plus anything tagged `mnemosyne`, minus
        done/error/skip."""
        lectures = [d for t in default_types for d in await self.documents(
            tags__id__all=[semester], tags__id__in=course_tags, document_type__id=t, tags__id__none=excluded,
            added__date__gte=go_live.isoformat())]
        opted = await self.documents(tags__id__all=[opt_in], tags__id__none=excluded) if opt_in else []
        seen: dict[int, Doc] = {d.id: d for d in [*lectures, *opted]}
        return sorted(seen.values(), key=lambda d: d.added)

    async def notes(self, doc_id: int) -> list[str]:
        async with self._client() as http:
            r = await http.get(f"/api/documents/{doc_id}/notes/")
            r.raise_for_status()
            body: Any = r.json()
        items = cast(list[dict[str, Any]], body if isinstance(body, list) else body.get("results") or [])
        return [str(n.get("note", "")).strip() for n in items if str(n.get("note", "")).strip()]

    async def download(self, doc_id: int) -> bytes:
        async with self._client() as http:
            r = await http.get(f"/api/documents/{doc_id}/download/", params={"original": "true"})
            r.raise_for_status()
            return r.content

    async def add_tag(self, doc_id: int, tag: int) -> None:
        async with self._client() as http:
            r = await http.post("/api/documents/bulk_edit/",
                                json={"documents": [doc_id], "method": "add_tag", "parameters": {"tag": tag}})
            r.raise_for_status()

    async def upload(self, pdf: bytes, filename: str, title: str, tags: list[int], document_type: int | None) -> str:
        """Hand a file to Paperless's consumer. Returns the consumption task id; the document appears later."""
        data: dict[str, Any] = {"title": title, "tags": [str(t) for t in tags]}
        if document_type is not None:
            data["document_type"] = str(document_type)
        async with self._client() as http:
            r = await http.post("/api/documents/post_document/", data=data,
                                files={"document": (filename, pdf, "application/pdf")})
            r.raise_for_status()
            return str(r.json())

    async def task(self, task_id: str) -> tuple[str, int | None, str]:
        """(status, new document id, error text) for a consumption task; status is pending until it finishes."""
        page = await self._get("/api/tasks/", {"task_id": task_id})
        results: list[dict[str, Any]] = page.get("results") or []
        if not results:
            return "pending", None, ""
        t = results[0]
        data: dict[str, Any] = t.get("result_data") or {}
        doc = data.get("document_id")
        return str(t.get("status", "pending")).lower(), (int(doc) if doc else None), str(data)[:2000]

    async def add_note(self, doc_id: int, note: str) -> None:
        async with self._client() as http:
            r = await http.post(f"/api/documents/{doc_id}/notes/", json={"note": note})
            r.raise_for_status()

    async def set_title(self, doc_id: int, title: str) -> None:
        async with self._client() as http:
            r = await http.patch(f"/api/documents/{doc_id}/", json={"title": title})
            r.raise_for_status()

    async def trash(self, doc_id: int) -> None:
        """DELETE moves a document to Paperless's trash, where it stays recoverable (30 days by default)."""
        async with self._client() as http:
            r = await http.delete(f"/api/documents/{doc_id}/")
            r.raise_for_status()

    async def remove_tag(self, doc_id: int, tag: int) -> None:
        async with self._client() as http:
            r = await http.post("/api/documents/bulk_edit/",
                                json={"documents": [doc_id], "method": "remove_tag", "parameters": {"tag": tag}})
            r.raise_for_status()

    def link(self, doc_id: int) -> str:
        return f"{self.public_url}/documents/{doc_id}/details"
