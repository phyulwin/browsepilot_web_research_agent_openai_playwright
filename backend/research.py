# backend/research.py — bounded research memory and deterministic action selection helpers.
import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from backend.config import settings
from backend.models import Action
from backend.safety import validate_url


SEARCH_URLS = {
    "bing": "https://www.bing.com/search?q=",
    "duckduckgo": "https://html.duckduckgo.com/html/?q=",
    "google": "https://www.google.com/search?q=",
}


def domain(url):
    # Treat www aliases as one failure domain without conflating unrelated sites.
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


def is_search(url):
    # Search pages are discovery aids and must never become quoted source evidence.
    host = domain(url)
    return (host.startswith("google.") or ".google." in host
            or any(host == base or host.endswith("." + base) for base in ("bing.com", "duckduckgo.com")))


def url_key(url):
    # Ignore fragments, common tracking parameters, query ordering, and HTTP upgrades.
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"gclid", "fbclid", "msclkid"}]
    return urlunsplit(("https", domain(url), parts.path.rstrip("/") or "/", urlencode(sorted(query)), ""))


def prompt_urls(prompt):
    # Scan explicit HTTP(S) links without rewriting or translating multilingual text.
    urls, cursor = [], 0
    lowered = "".join(char.lower() if char.isascii() else char for char in prompt)
    while cursor < len(prompt):
        starts = [position for scheme in ("https://", "http://")
                  if (position := lowered.find(scheme, cursor)) >= 0]
        if not starts:
            break
        start = min(starts)
        end = start
        while end < len(prompt) and not prompt[end].isspace() and prompt[end] not in '<>"\'`「」『』。':
            end += 1
        candidate = prompt[start:end].rstrip(".,;:!?、，；：！？]}")
        while candidate.endswith(")") and candidate.count(")") > candidate.count("("):
            candidate = candidate[:-1]
        try:
            validate_url(candidate)
            if url_key(candidate) not in {url_key(url) for url in urls}:
                urls.append(candidate)
        except ValueError:
            # Unsafe or malformed user links never reach navigation or action logs.
            pass
        cursor = max(end, start + 1)
    return urls


def fingerprint(observation):
    # Compare normalized content across navigation, tabs, and read/scroll operations.
    text = " ".join(observation.get("text", "").split())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def automatic_action(command, reason, session, **arguments):
    # Deterministic actions use exactly the same validated vocabulary and audit trail.
    step = next((item["number"] for item in session["steps"] if item["status"] != "completed"), 1)
    return Action(command=command, reason=reason, step=step, updates=[],
                  **{**dict.fromkeys(("url", "query", "link_id", "tab_id", "direction", "seconds")), **arguments})


class ResearchMemory:
    """Keep per-run loop prevention independent of model instructions and browser state."""

    def __init__(self, prompt):
        # All sets are bounded by the existing session action limit.
        self.direct_urls = prompt_urls(prompt)
        self.attempted_urls = set()
        self.visited = set()
        self.failed_domains = {}
        self.blocked_domains = set()
        self.searches = {}
        self.successful_searches = set()
        self.actions = set()
        self.read = set()
        self.extracted = set()
        self.observations = set()
        self.aliases = {}
        self.pending_search = None
        self.rejections = 0
        self.no_progress = 0

    def blocked(self, url):
        # A challenge on a parent host also prevents retrying its subdomains.
        host = domain(url)
        return any(host == item or host.endswith("." + item) for item in self.blocked_domains)

    def query_key(self, query):
        # Normalize case, punctuation, whitespace, and word order; retain Japanese letters.
        words = frozenset("".join(char if char.isalnum() else " " for char in query.casefold()).split())
        for previous in self.searches:
            if words == previous or (words and previous and len(words & previous) / len(words | previous) >= 0.85):
                return previous
        return words

    def provider(self, query):
        # A normalized query may try each configured provider once, never a blocked host.
        key = self.query_key(query)
        if key in self.successful_searches:
            return None
        used = self.searches.get(key, set())
        return next((name for name in settings.search_providers.split(",")
                     if name not in used and not self.blocked(SEARCH_URLS[name])), None)

    def next_action(self, session, observation):
        # Always inspect and extract readable source content before another navigation.
        digest = fingerprint(observation)
        readable = (observation.get("usable", True) and not observation.get("access_issue")
                    and not is_search(observation["url"]) and len(observation["text"].strip()) >= 40)
        if readable and (url_key(observation["url"]), digest) not in self.extracted:
            command = "EXTRACT" if digest in self.read else "READ_PAGE"
            return automatic_action(command, "Inspect source content before further browsing" if command == "READ_PAGE"
                                    else "Collect verified passages and assess answer coverage", session)
        pending = next((url for url in self.direct_urls if url_key(url) not in self.attempted_urls
                        and url_key(url) not in self.visited and not self.blocked(url)), None)
        if pending:
            return automatic_action("NAVIGATE", "Open a URL supplied in the approved request", session, url=pending)
        if (session.get("goal_satisfied") and not session.get("coverage_gaps")
                and all(source["verified"] for source in session.get("requested_sources", []))):
            return automatic_action("DONE", "Verified evidence covers the requested information", session)
        if self.pending_search and self.provider(self.pending_search):
            return automatic_action("SEARCH", "Try the next permitted search provider after an access failure", session,
                                    query=self.pending_search)
        self.pending_search = None
        return None

    def prepare(self, action, observation):
        # Reject equivalent operations before any browser request or additional paid extraction.
        target, provider = action.url, None
        if action.command == "CLICK":
            target = next((link["url"] for link in observation["links"] if link["id"] == action.link_id), None)
            if not target:
                raise ValueError("The link was not in the current observation")
        if action.command in {"NAVIGATE", "CLICK", "NEW_TAB"} and target:
            validate_url(target)
            if is_search(target):
                raise ValueError("Use SEARCH so provider limits and fallbacks apply")
            if self.blocked(target):
                raise ValueError("This domain is blocked for this run; choose another source")
            if url_key(target) in self.attempted_urls or url_key(target) in self.visited:
                raise ValueError("This source URL was already attempted; choose an unvisited source")
        if action.command == "SEARCH":
            provider = self.provider(action.query)
            if not provider:
                raise ValueError("Equivalent search already returned links or exhausted providers; use an original source or finish")
            self.searches.setdefault(self.query_key(action.query), set()).add(provider)
        digest = fingerprint(observation)
        if action.command in {"READ_PAGE", "EXTRACT"}:
            seen = self.read if action.command == "READ_PAGE" else self.extracted
            key = digest if action.command == "READ_PAGE" else (url_key(observation["url"]), digest)
            if key in seen:
                raise ValueError("This page content was already processed")
        signature = (action.command, url_key(target) if target else None, provider,
                     tuple(sorted(self.query_key(action.query))) if action.query else None,
                     action.tab_id, action.direction, observation["url"], digest)
        if signature in self.actions:
            raise ValueError("Repeated operation on unchanged content was prevented")
        self.actions.add(signature)
        if target:
            self.attempted_urls.add(url_key(target))
        return target, provider

    def failure(self, url, restricted=False):
        # One denied request blocks a host; two other navigation failures exhaust that host.
        if not url:
            return
        self.attempted_urls.add(url_key(url))
        host = domain(url)
        self.failed_domains[host] = self.failed_domains.get(host, 0) + 1
        if restricted or self.failed_domains[host] >= 2:
            self.blocked_domains.add(host)

    def compact_observation(self, observation):
        # Action selection gets a short preview once; extraction gets the full bounded window.
        digest = fingerprint(observation)
        repeated = digest in self.observations
        self.observations.add(digest)
        links = [link for link in observation["links"]
                 if not self.blocked(link["url"]) and url_key(link["url"]) not in self.attempted_urls]
        return {"url": observation["url"], "title": observation["title"],
                "text": "Unchanged content; see previous action outcome and evidence." if repeated else observation["text"][:1400],
                "links": links[:20], "tabs": observation["tabs"], "access_issue": observation.get("access_issue"),
                "has_more_text": observation.get("has_more_text", False)}

    def summary(self):
        # Expose only compact progress and constraints to the next model decision.
        return {"visited_urls": sorted(self.visited)[-12:], "attempted_urls": sorted(self.attempted_urls)[-12:],
                "blocked_domains": sorted(self.blocked_domains),
                "searches": [{"query": " ".join(sorted(query)), "providers": sorted(providers)}
                             for query, providers in self.searches.items()],
                "failed_domains": self.failed_domains, "processed_pages": len(self.extracted)}
