from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime, timezone
from html.parser import HTMLParser
import html
import re
from pathlib import Path
from typing import Any

from .client import APIError, Client, DEFAULT_API_URL, DEFAULT_USER_AGENT
from .parser import (
    clean_wikitext,
    extract_lists,
    extract_template_calls,
    parse_wikitext,
)


DEFAULT_EXTRACT_INCLUDE = (
    "revision",
    "categories",
    "links",
    "templates",
    "images",
    "external_links",
    "pageprops",
    "wikitext",
    "structure",
)


class Wiki:
    """Generic, data-oriented facade over the MediaWiki Action API.

    The class deliberately does not interpret domain meaning. It can fetch and
    structure pages, templates, sections, links, lists and revisions, but it
    never decides what a particular page, template or parameter *means*.
    """

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 30.0,
        maxlag: int | None = 5,
        retries: int = 4,
        min_interval: float = 0.5,
        cache_ttl: float = 300.0,
        cache_max_entries: int = 512,
        cache_path: str | Path | None = None,
        client: Client | None = None,
    ) -> None:
        self.client = client or Client(
            api_url,
            user_agent=user_agent,
            timeout=timeout,
            maxlag=maxlag,
            retries=retries,
            min_interval=min_interval,
            cache_ttl=cache_ttl,
            cache_max_entries=cache_max_entries,
            cache_path=cache_path,
        )

    @property
    def api_url(self) -> str:
        return self.client.api_url

    # ------------------------------------------------------------------
    # Raw API building blocks
    # ------------------------------------------------------------------
    def api(
        self,
        *,
        action: str = "query",
        use_cache: bool = True,
        **params: Any,
    ) -> Any:
        """Run one raw MediaWiki API request."""
        return self.client.request(use_cache=use_cache, action=action, **params)

    def iter_api(self, *, action: str = "query", **params: Any) -> Iterator[Any]:
        """Yield every continued API response for a request."""
        yield from self.client.iterate(action=action, **params)

    def query(self, **params: Any) -> dict[str, Any]:
        """Shortcut for one ``action=query`` request."""
        return self.api(action="query", **params)

    def parse(
        self,
        title: str,
        *,
        section: int | str | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        """Run ``action=parse`` for a page, optionally for one section."""
        params.setdefault("page", title)
        if section is not None:
            params.setdefault("section", section)
        data = self.api(action="parse", **params)
        return data.get("parse", {}) if isinstance(data, dict) else {}

    def iter_list(
        self,
        module: str,
        *,
        limit: int | None = None,
        **params: Any,
    ) -> Iterator[dict[str, Any]]:
        """Yield items from a MediaWiki ``list=...`` module lazily.

        Continuation is handled automatically. ``limit`` is the total number
        of items yielded, not the server-side chunk size. This mirrors the
        generator style used by mature MediaWiki clients while keeping the
        raw Action API shape visible.
        """
        if limit is not None and limit <= 0:
            return

        yielded = 0
        for batch in self.iter_api(action="query", list=module, **params):
            chunk = batch.get("query", {}).get(module, [])
            for item in chunk:
                yield item
                yielded += 1
                if limit is not None and yielded >= limit:
                    return

    def list(
        self,
        module: str,
        *,
        limit: int | None = None,
        **params: Any,
    ) -> list[dict[str, Any]]:
        """Collect a MediaWiki ``list=...`` module across continuation."""
        return list(self.iter_list(module, limit=limit, **params))

    def pages(self, **params: Any) -> list[dict[str, Any]]:
        """Collect and merge ``query.pages`` across continuation batches."""
        pages, _meta = self._pages_bundle(**params)
        return pages

    # ------------------------------------------------------------------
    # Site/API discovery
    # ------------------------------------------------------------------
    def siteinfo(
        self,
        props: Iterable[str] = ("general", "namespaces", "namespacealiases", "extensions"),
    ) -> dict[str, Any]:
        """Return MediaWiki site capabilities and metadata."""
        data = self.query(meta="siteinfo", siprop="|".join(dict.fromkeys(props)))
        return data.get("query", {})

    def paraminfo(self, modules: str | Iterable[str]) -> dict[str, Any]:
        """Ask MediaWiki which parameters/modules its API supports."""
        if isinstance(modules, str):
            value = modules
        else:
            value = "|".join(modules)
        data = self.api(action="paraminfo", modules=value)
        return data.get("paraminfo", {}) if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # Pages and search
    # ------------------------------------------------------------------
    def get(
        self,
        title: str,
        *,
        props: Iterable[str] = ("info", "categories", "links", "templates"),
    ) -> dict[str, Any] | None:
        """Get one page with selected MediaWiki properties."""
        values = self.get_many([title], props=props)
        return values[0] if values else None

    def get_many(
        self,
        titles: Iterable[str],
        *,
        props: Iterable[str] = ("info", "categories", "links", "templates"),
        batch_size: int = 50,
    ) -> list[dict[str, Any] | None]:
        """Fetch many titles efficiently while preserving requested order.

        MediaWiki accepts multiple titles in one query. This helper batches at
        50 titles by default, which is safe for ordinary clients.
        """
        requested = [str(title) for title in titles]
        if not requested:
            return []
        if not 1 <= batch_size <= 50:
            raise ValueError("batch_size must be between 1 and 50")

        prop_names = tuple(dict.fromkeys(props))
        result: list[dict[str, Any] | None] = []

        for batch_titles in self._chunks(requested, batch_size):
            params = self._property_query_params(
                prop_names,
                single_page=len(batch_titles) == 1,
            )
            params.update({"titles": "|".join(batch_titles), "redirects": 1})
            pages, meta = self._pages_bundle(**params)
            result.extend(
                None if page is not None and page.get("invalid") is True else page
                for page in self._map_pages_to_requested(batch_titles, pages, meta)
            )

        return result

    def search(
        self,
        text: str,
        *,
        limit: int | None = 20,
        namespace: int | None = None,
        **params: Any,
    ) -> list[dict[str, Any]]:
        """Search MediaWiki without pretending ranking resolves identity."""
        params.setdefault("srsearch", text)
        params.setdefault("srlimit", "max" if limit is None else max(1, min(int(limit), 500)))
        if namespace is not None:
            params.setdefault("srnamespace", namespace)
        return self.list("search", limit=limit, **params)

    # ------------------------------------------------------------------
    # Generic list/property helpers
    # ------------------------------------------------------------------
    def iter_members(
        self,
        category: str,
        *,
        limit: int | None = None,
        namespace: int | None = None,
        recurse: bool | int = False,
        **params: Any,
    ) -> Iterator[dict[str, Any]]:
        """Yield category members, optionally traversing subcategories.

        ``recurse=False`` preserves the direct MediaWiki ``categorymembers``
        behavior. ``recurse=True`` walks the complete reachable subcategory
        graph, while an integer limits traversal depth (``recurse=1`` includes
        members of immediate subcategories). Cycles are guarded and recursive
        output is de-duplicated.

        The helper remains purely structural: it understands MediaWiki
        categories and namespaces, not the meaning of page content.
        """
        if limit is not None and limit <= 0:
            return

        if isinstance(recurse, bool):
            max_depth: int | None = None if recurse else 0
        else:
            if recurse < 0:
                raise ValueError("recurse must be False, True, or an integer >= 0")
            max_depth = int(recurse)

        # Bare names use the canonical Category namespace. If the caller
        # already supplied a namespace prefix (including a localized one),
        # preserve it verbatim.
        if ":" not in category:
            category = f"Category:{category}"

        if max_depth == 0:
            direct = dict(params)
            direct.setdefault("cmtitle", category)
            direct.setdefault(
                "cmlimit",
                "max" if limit is None else max(1, min(int(limit), 500)),
            )
            if namespace is not None:
                direct.setdefault("cmnamespace", namespace)
            yield from self.iter_list("categorymembers", limit=limit, **direct)
            return

        requested_type = str(params.get("cmtype", "page|subcat|file"))
        requested_types = {value for value in requested_type.split("|") if value}
        traversal_types = set(requested_types)
        traversal_types.add("subcat")
        traversal_type_value = "|".join(
            value for value in ("page", "subcat", "file") if value in traversal_types
        )

        base = dict(params)
        base.pop("cmtitle", None)
        base.pop("cmnamespace", None)
        base.pop("cmcontinue", None)
        base["cmtype"] = traversal_type_value or "subcat"
        base.setdefault("cmlimit", "max")

        queue: deque[tuple[str, int]] = deque([(category, 0)])
        visited_categories: set[str] = set()
        emitted: set[tuple[Any, ...]] = set()
        yielded = 0

        while queue:
            current_category, depth = queue.popleft()
            category_key = self._title_key(current_category)
            if category_key in visited_categories:
                continue
            visited_categories.add(category_key)

            current_params = dict(base)
            current_params["cmtitle"] = current_category

            for member in self.iter_list("categorymembers", **current_params):
                member_type = self._category_member_type(member)

                if member_type == "subcat" and (max_depth is None or depth < max_depth):
                    subcategory = str(member.get("title", ""))
                    if subcategory:
                        queue.append((subcategory, depth + 1))

                if member_type not in requested_types:
                    continue
                if namespace is not None and member.get("ns") != namespace:
                    continue

                key = self._category_member_key(member)
                if key in emitted:
                    continue
                emitted.add(key)

                yield member
                yielded += 1
                if limit is not None and yielded >= limit:
                    return

    def members(
        self,
        category: str,
        *,
        limit: int | None = None,
        namespace: int | None = None,
        recurse: bool | int = False,
        **params: Any,
    ) -> list[dict[str, Any]]:
        """Collect category members, optionally traversing subcategories."""
        return list(
            self.iter_members(
                category,
                limit=limit,
                namespace=namespace,
                recurse=recurse,
                **params,
            )
        )

    def backlinks(
        self,
        title: str,
        *,
        limit: int | None = None,
        namespace: int | None = None,
        **params: Any,
    ) -> list[dict[str, Any]]:
        params.setdefault("bltitle", title)
        params.setdefault("bllimit", "max" if limit is None else max(1, min(int(limit), 500)))
        if namespace is not None:
            params.setdefault("blnamespace", namespace)
        return self.list("backlinks", limit=limit, **params)

    def embedded_in(
        self,
        title: str,
        *,
        limit: int | None = None,
        namespace: int | None = None,
        **params: Any,
    ) -> list[dict[str, Any]]:
        params.setdefault("eititle", title)
        params.setdefault("eilimit", "max" if limit is None else max(1, min(int(limit), 500)))
        if namespace is not None:
            params.setdefault("einamespace", namespace)
        return self.list("embeddedin", limit=limit, **params)

    def categories(self, title: str) -> list[str]:
        page = self.get(title, props=("categories",)) or {}
        return [x["title"] for x in page.get("categories", []) if "title" in x]

    def links(self, title: str) -> list[str]:
        page = self.get(title, props=("links",)) or {}
        return [x["title"] for x in page.get("links", []) if "title" in x]

    def templates(self, title: str) -> list[str]:
        page = self.get(title, props=("templates",)) or {}
        return [x["title"] for x in page.get("templates", []) if "title" in x]

    def images(self, title: str) -> list[str]:
        page = self.get(title, props=("images",)) or {}
        return [x["title"] for x in page.get("images", []) if "title" in x]

    def external_links(self, title: str) -> list[str]:
        page = self.get(title, props=("extlinks",)) or {}
        return self._external_link_values(page.get("extlinks", []))

    # ------------------------------------------------------------------
    # Page content and structure
    # ------------------------------------------------------------------
    def wikitext(self, title: str) -> str | None:
        """Fetch the latest main-slot wikitext with ``prop=revisions``."""
        params = self._property_query_params(("revisions",), include_content=True)
        params.update({"titles": title, "redirects": 1})
        pages = self.pages(**params)
        if not pages or pages[0].get("missing") is True:
            return None
        return self._revision_content(pages[0])

    def sections(self, title: str) -> list[dict[str, Any]]:
        """Return MediaWiki's rendered section table.

        ``tocdata`` is preferred; older servers fall back to ``sections``.
        """
        try:
            parsed = self.parse(title, prop="tocdata")
            tocdata = parsed.get("tocdata")
            if isinstance(tocdata, dict) and isinstance(tocdata.get("sections"), list):
                return list(tocdata["sections"])
        except APIError:
            pass

        parsed = self.parse(title, prop="sections")
        return list(parsed.get("sections", []))

    def section_index(self, title: str, heading: str) -> str | None:
        wanted = self._normalize_heading(heading)
        for section in self.sections(title):
            for candidate in (section.get("line"), section.get("anchor")):
                if candidate is not None and self._normalize_heading(str(candidate)) == wanted:
                    index = section.get("index")
                    return str(index) if index is not None else None
        return None

    def lead(self, title: str, *, prop: str = "wikitext") -> Any:
        parsed = self.parse(title, section=0, prop=prop)
        return self._parse_value(parsed.get(prop))

    def section(self, title: str, heading: str, *, prop: str = "wikitext") -> Any | None:
        index = self.section_index(title, heading)
        if index is None:
            return None
        parsed = self.parse(title, section=index, prop=prop)
        return self._parse_value(parsed.get(prop))

    def structure(self, title: str, *, section: str | int | None = None) -> dict[str, Any]:
        """Fetch wikitext and describe its syntax without interpreting meaning."""
        if section is None:
            source = self.wikitext(title)
        elif isinstance(section, int):
            parsed = self.parse(title, section=section, prop="wikitext")
            source = self._parse_value(parsed.get("wikitext"))
        else:
            source = self.section(title, section, prop="wikitext")

        if not isinstance(source, str):
            return {
                "sections": [],
                "templates": [],
                "wikilinks": [],
                "external_links": [],
                "lists": [],
                "tables": [],
            }
        return parse_wikitext(source)

    def template_calls(
        self,
        title: str,
        *,
        section: str | int | None = None,
    ) -> list[dict[str, Any]]:
        """Return arbitrary template calls and parameters from page source."""
        if section is None:
            source = self.wikitext(title)
        elif isinstance(section, int):
            parsed = self.parse(title, section=section, prop="wikitext")
            source = self._parse_value(parsed.get("wikitext"))
        else:
            source = self.section(title, section, prop="wikitext")
        return extract_template_calls(source) if isinstance(source, str) else []

    def section_items(self, title: str, heading: str, *, clean: bool = False) -> list[dict[str, Any]]:
        """Return list items in a named section; raw source is never lost."""
        source = self.section(title, heading, prop="wikitext")
        if not isinstance(source, str):
            return []

        result: list[dict[str, Any]] = []
        for item in extract_lists(source):
            raw_text = item["value"]["raw"]
            if clean:
                result.append(
                    {
                        "depth": item["depth"],
                        "marker": item["marker"],
                        "raw_text": raw_text,
                        "text": item["value"]["text"],
                    }
                )
            else:
                result.append(
                    {
                        "depth": item["depth"],
                        "marker": item["marker"],
                        "text": raw_text,
                    }
                )
        return result

    # ------------------------------------------------------------------
    # Generic snapshots / datasets
    # ------------------------------------------------------------------
    def extract(
        self,
        title: str,
        *,
        include: Iterable[str] = DEFAULT_EXTRACT_INCLUDE,
    ) -> dict[str, Any]:
        """Return one JSON-serializable page snapshot with provenance."""
        return self.extract_many([title], include=include)[0]

    def extract_many(
        self,
        titles: Iterable[str],
        *,
        include: Iterable[str] = DEFAULT_EXTRACT_INCLUDE,
        batch_size: int = 50,
    ) -> list[dict[str, Any]]:
        """Build generic page snapshots efficiently in batches.

        Supported ``include`` values are ``revision``, ``categories``,
        ``links``, ``templates``, ``images``, ``external_links``,
        ``pageprops``, ``wikitext`` and ``structure``. Page identity and source
        provenance are always included.
        """
        requested = [str(title) for title in titles]
        if not requested:
            return []
        if not 1 <= batch_size <= 50:
            raise ValueError("batch_size must be between 1 and 50")

        include_set = set(include)
        allowed = set(DEFAULT_EXTRACT_INCLUDE)
        unknown = include_set - allowed
        if unknown:
            raise ValueError(f"unknown include value(s): {', '.join(sorted(unknown))}")

        need_content = bool({"wikitext", "structure"} & include_set)
        props: list[str] = ["info"]
        if "revision" in include_set or need_content:
            props.append("revisions")
        if "categories" in include_set:
            props.append("categories")
        if "links" in include_set:
            props.append("links")
        if "templates" in include_set:
            props.append("templates")
        if "images" in include_set:
            props.append("images")
        if "external_links" in include_set:
            props.append("extlinks")
        if "pageprops" in include_set:
            props.append("pageprops")

        retrieved_at = datetime.now(timezone.utc).isoformat()
        snapshots: list[dict[str, Any]] = []

        for batch_titles in self._chunks(requested, batch_size):
            params = self._property_query_params(
                props,
                include_content=need_content,
                single_page=len(batch_titles) == 1,
            )
            params.update({"titles": "|".join(batch_titles), "redirects": 1})
            pages, meta = self._pages_bundle(**params)
            mapped = self._map_pages_to_requested(batch_titles, pages, meta)
            for requested_title, page in zip(batch_titles, mapped):
                snapshots.append(
                    self._snapshot(
                        requested_title,
                        page,
                        meta,
                        include_set,
                        retrieved_at,
                    )
                )

        return snapshots

    # ------------------------------------------------------------------
    # Compatibility helpers from 1.0 (implemented using generic machinery)
    # ------------------------------------------------------------------
    @staticmethod
    def clean_wikitext(text: str) -> str:
        return clean_wikitext(text)

    def templates_with_params(
        self,
        title: str,
        *,
        section: int | str = 0,
    ) -> list[dict[str, Any]]:
        calls = self.template_calls(title, section=section)
        return [
            {"name": item["name"], "params": dict(item["params"])}
            for item in calls
            if item.get("depth") == 1
        ]

    def template_params(
        self,
        title: str,
        template: str,
        *,
        section: int | str = 0,
    ) -> dict[str, str] | None:
        wanted = self._normalize_template_name(template)
        for item in self.templates_with_params(title, section=section):
            if self._normalize_template_name(item["name"]) == wanted:
                return dict(item["params"])
        return None

    def infobox(self, title: str, *, section: int | str = 0) -> dict[str, Any] | None:
        """Compatibility convenience; generic template parsing is preferred."""
        for item in self.templates_with_params(title, section=section):
            name = self._normalize_template_name(item["name"])
            if name == "infobox" or name.startswith("infobox "):
                return item
        return None

    def infobox_fields(self, title: str, *, section: int | str = 0) -> dict[str, str]:
        """Compatibility helper for rendered tables with class ``infobox``."""
        parsed = self.parse(title, section=section, prop="text")
        html_text = self._parse_value(parsed.get("text"))
        if not isinstance(html_text, str) or not html_text.strip():
            return {}
        parser = _InfoboxTableParser()
        parser.feed(html_text)
        parser.close()
        return parser.fields

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _pages_bundle(self, **params: Any) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        merged: dict[tuple[str, Any], dict[str, Any]] = {}
        order: list[tuple[str, Any]] = []
        meta: dict[str, list[dict[str, Any]]] = {
            "normalized": [],
            "redirects": [],
            "converted": [],
        }

        for batch in self.iter_api(action="query", **params):
            query = batch.get("query", {}) if isinstance(batch, dict) else {}
            for page in query.get("pages", []):
                key = self._page_key(page)
                if key not in merged:
                    merged[key] = {}
                    order.append(key)
                self._merge_dict(merged[key], page)

            for key in meta:
                for item in query.get(key, []) or []:
                    if item not in meta[key]:
                        meta[key].append(item)

        return [merged[key] for key in order], meta

    @staticmethod
    def _property_query_params(
        props: Iterable[str],
        *,
        include_content: bool = False,
        single_page: bool = True,
    ) -> dict[str, Any]:
        prop_names = tuple(dict.fromkeys(props))
        params: dict[str, Any] = {}
        if prop_names:
            params["prop"] = "|".join(prop_names)
        if "info" in prop_names:
            params["inprop"] = "url|displaytitle"
        if "categories" in prop_names:
            params["cllimit"] = "max"
        if "links" in prop_names:
            params["pllimit"] = "max"
        if "templates" in prop_names:
            params["tllimit"] = "max"
        if "images" in prop_names:
            params["imlimit"] = "max"
        if "extlinks" in prop_names:
            params["ellimit"] = "max"
        if "revisions" in prop_names:
            rvprop = ["ids", "timestamp", "sha1", "contentmodel", "contentformat"]
            if include_content:
                rvprop.append("content")
            params["rvprop"] = "|".join(rvprop)
            params["rvslots"] = "main"
            # MediaWiki's revisions module has two modes:
            # - multiple pages => latest revision of each page; rvlimit is invalid
            # - one page       => rvlimit may be used
            #
            # Omitting rvlimit for batched title queries is therefore required,
            # not just an optimization. See API:Revisions mode #1.
            if single_page:
                params["rvlimit"] = 1
        return params

    def _snapshot(
        self,
        requested_title: str,
        page: dict[str, Any] | None,
        meta: dict[str, list[dict[str, Any]]],
        include: set[str],
        retrieved_at: str,
    ) -> dict[str, Any]:
        if page is None:
            return {
                "schema_version": 1,
                "exists": False,
                "source": {
                    "api_url": self.api_url,
                    "requested_title": requested_title,
                    "retrieved_at": retrieved_at,
                },
                "page": None,
                "revision": None,
                "data": {},
                "content": None,
                "structure": None,
                "raw": None,
            }

        exists = page.get("missing") is not True and page.get("invalid") is not True
        revision = self._revision_metadata(page)
        content_raw = self._revision_content(page) if exists else None
        resolved_title = page.get("title")
        redirects = self._relevant_redirects(requested_title, resolved_title, meta)

        page_info = {
            key: page[key]
            for key in (
                "pageid",
                "ns",
                "title",
                "contentmodel",
                "pagelanguage",
                "pagelanguagehtmlcode",
                "pagelanguagedir",
                "touched",
                "lastrevid",
                "length",
                "fullurl",
                "editurl",
                "canonicalurl",
                "displaytitle",
            )
            if key in page
        }

        data: dict[str, Any] = {}
        if "categories" in include:
            data["categories"] = [x.get("title") for x in page.get("categories", []) if x.get("title")]
        if "links" in include:
            data["links"] = [x.get("title") for x in page.get("links", []) if x.get("title")]
        if "templates" in include:
            data["templates"] = [x.get("title") for x in page.get("templates", []) if x.get("title")]
        if "images" in include:
            data["images"] = [x.get("title") for x in page.get("images", []) if x.get("title")]
        if "external_links" in include:
            data["external_links"] = self._external_link_values(page.get("extlinks", []))
        if "pageprops" in include:
            data["pageprops"] = dict(page.get("pageprops", {}) or {})

        content: dict[str, Any] | None = None
        if "wikitext" in include and content_raw is not None:
            content = {
                "raw": content_raw,
                "text": clean_wikitext(content_raw),
            }

        structure = (
            parse_wikitext(content_raw)
            if "structure" in include and isinstance(content_raw, str)
            else None
        )

        return {
            "schema_version": 1,
            "exists": exists,
            "source": {
                "api_url": self.api_url,
                "requested_title": requested_title,
                "resolved_title": resolved_title,
                "pageid": page.get("pageid"),
                "revision_id": revision.get("revid") if revision else None,
                "revision_timestamp": revision.get("timestamp") if revision else None,
                "retrieved_at": retrieved_at,
                "redirects": redirects,
            },
            "page": page_info,
            "revision": revision if "revision" in include else None,
            "data": data,
            "content": content,
            "structure": structure,
            "raw": {
                "page": page,
                "normalized": meta.get("normalized", []),
                "redirects": meta.get("redirects", []),
                "converted": meta.get("converted", []),
            },
        }

    @staticmethod
    def _revision_metadata(page: dict[str, Any]) -> dict[str, Any] | None:
        revisions = page.get("revisions") or []
        if not revisions:
            return None
        revision = revisions[0]
        result = {
            key: revision[key]
            for key in ("revid", "parentid", "timestamp", "sha1")
            if key in revision
        }
        slot = revision.get("slots", {}).get("main", {}) if isinstance(revision.get("slots"), dict) else {}
        for key in ("contentmodel", "contentformat"):
            if key in slot:
                result[key] = slot[key]
            elif key in revision:
                result[key] = revision[key]
        return result

    @staticmethod
    def _revision_content(page: dict[str, Any]) -> str | None:
        revisions = page.get("revisions") or []
        if not revisions:
            return None
        revision = revisions[0]
        slots = revision.get("slots")
        if isinstance(slots, dict):
            main = slots.get("main") or {}
            if isinstance(main, dict):
                for key in ("content", "*"):
                    if isinstance(main.get(key), str):
                        return main[key]
        for key in ("content", "*"):
            if isinstance(revision.get(key), str):
                return revision[key]
        return None

    @staticmethod
    def _external_link_values(items: Any) -> list[str]:
        result: list[str] = []
        for item in items or []:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                value = item.get("url") or item.get("*")
                if value:
                    result.append(str(value))
        return result

    @classmethod
    def _map_pages_to_requested(
        cls,
        requested: Sequence[str],
        pages: list[dict[str, Any]],
        meta: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any] | None]:
        normalized = {
            cls._title_key(item.get("from", "")): str(item.get("to", ""))
            for item in meta.get("normalized", [])
            if item.get("from") is not None and item.get("to") is not None
        }
        converted = {
            cls._title_key(item.get("from", "")): str(item.get("to", ""))
            for item in meta.get("converted", [])
            if item.get("from") is not None and item.get("to") is not None
        }
        redirects = {
            cls._title_key(item.get("from", "")): str(item.get("to", ""))
            for item in meta.get("redirects", [])
            if item.get("from") is not None and item.get("to") is not None
        }
        by_title = {
            cls._title_key(page.get("title", "")): page
            for page in pages
            if page.get("title") is not None
        }

        result: list[dict[str, Any] | None] = []
        for original in requested:
            current = original.replace("_", " ").strip()

            # Normalization/conversion are single preprocessing steps. They can
            # change spelling/case without changing our comparison key, so do
            # not treat that as a redirect cycle.
            for mapping in (normalized, converted):
                next_value = mapping.get(cls._title_key(current))
                if next_value:
                    current = next_value

            # Redirects may chain. Follow them while guarding real cycles.
            seen: set[str] = set()
            while True:
                key = cls._title_key(current)
                if key in seen:
                    break
                seen.add(key)
                next_value = redirects.get(key)
                if not next_value:
                    break
                current = next_value

            page = by_title.get(cls._title_key(current))
            if page is None:
                page = by_title.get(cls._title_key(original))
            if page is not None and page.get("missing") is True:
                page = None
            result.append(page)
        return result

    @classmethod
    def _relevant_redirects(
        cls,
        requested: str,
        resolved: Any,
        meta: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        wanted = {cls._title_key(requested), cls._title_key(resolved or "")}
        result: list[dict[str, Any]] = []
        for item in meta.get("redirects", []):
            if cls._title_key(item.get("from", "")) in wanted or cls._title_key(item.get("to", "")) in wanted:
                result.append(dict(item))
                wanted.add(cls._title_key(item.get("from", "")))
                wanted.add(cls._title_key(item.get("to", "")))
        return result

    @staticmethod
    def _category_member_type(member: dict[str, Any]) -> str:
        value = member.get("type")
        if value in {"page", "subcat", "file"}:
            return str(value)
        namespace = member.get("ns")
        if namespace == 14:
            return "subcat"
        if namespace == 6:
            return "file"
        return "page"

    @classmethod
    def _category_member_key(cls, member: dict[str, Any]) -> tuple[Any, ...]:
        if member.get("pageid") is not None:
            return ("pageid", member.get("pageid"))
        return (
            "title",
            member.get("ns"),
            cls._title_key(member.get("title", "")),
        )

    @staticmethod
    def _title_key(value: Any) -> str:
        return " ".join(str(value).replace("_", " ").split())

    @staticmethod
    def _page_key(page: dict[str, Any]) -> tuple[str, Any]:
        if "pageid" in page:
            return ("pageid", page["pageid"])
        return ("title", page.get("title"))

    @classmethod
    def _merge_dict(cls, target: dict[str, Any], incoming: dict[str, Any]) -> None:
        for key, value in incoming.items():
            if key not in target:
                target[key] = value
                continue
            current = target[key]
            if isinstance(current, list) and isinstance(value, list):
                cls._extend_unique(current, value)
            elif isinstance(current, dict) and isinstance(value, dict):
                cls._merge_dict(current, value)
            elif current in (None, "", False) and value not in (None, "", False):
                target[key] = value

    @staticmethod
    def _extend_unique(target: list[Any], incoming: list[Any]) -> None:
        def record_key(item: Any) -> Any:
            # Preserve full equality, including metadata, for common flat API
            # records. Nested/unknown shapes retain the equality-scan fallback.
            if isinstance(item, dict) and all(
                type(value) in (str, int, float, bool, type(None))
                for value in item.values()
            ):
                return ("record", frozenset(item.items()))
            if type(item) in (str, int, float, bool, type(None)):
                return ("scalar", item)
            return None

        seen = set()
        fallback = []
        for item in target:
            key = record_key(item)
            if key is None:
                fallback.append(item)
            else:
                seen.add(key)
        for item in incoming:
            key = record_key(item)
            if key is None:
                if item not in target:
                    target.append(item)
                    fallback.append(item)
            elif key not in seen and item not in fallback:
                target.append(item)
                seen.add(key)

    @staticmethod
    def _parse_value(value: Any) -> Any:
        if isinstance(value, dict) and "*" in value:
            return value["*"]
        return value

    @staticmethod
    def _normalize_heading(value: str) -> str:
        value = html.unescape(value)
        value = re.sub(r"<[^>]+>", "", value)
        value = value.replace("_", " ")
        return " ".join(value.split()).casefold()

    @staticmethod
    def _normalize_template_name(value: str) -> str:
        value = value.strip().replace("_", " ")
        if value.casefold().startswith("template:"):
            value = value.split(":", 1)[1]
        return " ".join(value.split()).casefold()

    @staticmethod
    def _chunks(values: Sequence[str], size: int) -> Iterator[list[str]]:
        for start in range(0, len(values), size):
            yield list(values[start : start + size])


class _InfoboxTableParser(HTMLParser):
    """Compatibility parser for two-cell rows in an HTML infobox table."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, str] = {}
        self._table_depth = 0
        self._in_infobox = False
        self._done = False
        self._in_row = False
        self._cell_tag: str | None = None
        self._cell_parts: list[str] = []
        self._cells: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._done:
            return
        attrs_dict = dict(attrs)
        if tag == "table":
            if self._in_infobox:
                self._table_depth += 1
            elif "infobox" in (attrs_dict.get("class") or "").casefold():
                self._in_infobox = True
                self._table_depth = 1
            return
        if not self._in_infobox or self._table_depth != 1:
            return
        if tag == "tr":
            self._in_row = True
            self._cells = []
        elif self._in_row and tag in {"th", "td"} and self._cell_tag is None:
            self._cell_tag = tag
            self._cell_parts = []
        elif self._cell_tag is not None and tag == "br":
            self._cell_parts.append(" ")
        elif self._cell_tag is not None and tag == "img":
            alt = attrs_dict.get("alt")
            if alt:
                self._cell_parts.append(f" {alt} ")

    def handle_endtag(self, tag: str) -> None:
        if self._done or not self._in_infobox:
            return
        if tag == "table":
            self._table_depth -= 1
            if self._table_depth == 0:
                self._in_infobox = False
                self._done = True
            return
        if self._table_depth != 1:
            return
        if self._cell_tag == tag:
            value = " ".join("".join(self._cell_parts).split())
            self._cells.append((tag, value))
            self._cell_tag = None
            self._cell_parts = []
        elif tag == "tr" and self._in_row:
            self._finish_row()
            self._in_row = False

    def handle_data(self, data: str) -> None:
        if self._in_infobox and self._table_depth == 1 and self._cell_tag is not None:
            self._cell_parts.append(data)

    def _finish_row(self) -> None:
        nonempty = [(tag, text) for tag, text in self._cells if text]
        if len(nonempty) < 2 or nonempty[0][0] != "th":
            return
        label = nonempty[0][1]
        value = " ".join(text for _, text in nonempty[1:]).strip()
        if label and value:
            self.fields[label] = value
