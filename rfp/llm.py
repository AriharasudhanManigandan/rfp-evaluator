"""LLM provider adapters. A provider only needs ``complete_json(system, user, repair_of=None) -> str``.

* anthropic  - Claude via the Anthropic SDK
* openai     - OpenAI or any OpenAI-compatible endpoint (Groq, Together, Ollama, Gemini's OpenAI endpoint ...)
* mock       - OFFLINE keyword scorer for demos and tests. It is NOT an LLM.
* FaultInjectingClient - wraps any provider and corrupts its reply (validation demo)

``repair_of`` carries the previous unparseable reply so a provider can ask the model to fix its own output.
"""
from __future__ import annotations

import json
import os
import re

from .config import DEFAULT_MODELS, LLM_TEMPERATURE

REPAIR_PROMPT = ("Your previous reply could not be parsed as a JSON object. Reply again with ONLY the JSON "
                 "object in the required schema - no markdown, no text before or after it.")


class LLMConfigError(RuntimeError):
    """Provider is not configured correctly (missing key / package)."""


def _conversation(user: str, repair_of: str | None) -> list[dict]:
    messages = [{"role": "user", "content": user}]
    if repair_of is not None:
        messages += [{"role": "assistant", "content": repair_of or "(empty reply)"},
                     {"role": "user", "content": REPAIR_PROMPT}]
    return messages


def _call_dropping_unsupported(call, extras: dict):
    """Call with optional parameters (temperature, response_format); drop any the server rejects."""
    extras = dict(extras)
    while True:
        try:
            return call(**extras)
        except Exception as exc:
            message = str(exc).lower()
            rejected = [name for name in extras if name in message]
            if not rejected:
                raise
            for name in rejected:
                extras.pop(name)


class AnthropicClient:
    def __init__(self, api_key: str, model: str | None = None):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMConfigError("Install the 'anthropic' package.") from exc
        self.provider, self.model = "anthropic", model or DEFAULT_MODELS["anthropic"]
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete_json(self, system: str, user: str, repair_of: str | None = None) -> str:
        def call(**extra):
            return self._client.messages.create(model=self.model, max_tokens=4096, system=system,
                                                messages=_conversation(user, repair_of), **extra)
        message = _call_dropping_unsupported(call, {"temperature": LLM_TEMPERATURE})
        return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")


class OpenAIClient:
    def __init__(self, api_key: str, model: str | None = None, base_url: str | None = None):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMConfigError("Install the 'openai' package.") from exc
        self.provider, self.model = "openai", model or DEFAULT_MODELS["openai"]
        self.base_url = base_url or None
        self._client = OpenAI(api_key=api_key, base_url=self.base_url)

    def complete_json(self, system: str, user: str, repair_of: str | None = None) -> str:
        messages = [{"role": "system", "content": system}, *_conversation(user, repair_of)]

        def call(**extra):
            return self._client.chat.completions.create(model=self.model, messages=messages, **extra)
        response = _call_dropping_unsupported(
            call, {"temperature": LLM_TEMPERATURE, "response_format": {"type": "json_object"}})
        return response.choices[0].message.content or ""


# --------------------------------------------------------------------------- offline demo scorer
KEYWORD_BANK = {
    "technical": ["architecture", "microservice", "api", "integration", "scalab", "load test",
                  "high availability", "multi-region", "kubernetes", "saml", "event-driven", "kafka",
                  "rest api", "graphql", "failover", "autoscal", "container", "sandbox", "legacy", "connector"],
    "implementation": ["milestone", "phase", "sprint", "week", "timeline", "project manager", "staffing",
                       "team", "risk register", "mitigation", "uat", "go-live", "hypercare", "training",
                       "cutover", "gantt", "raci", "governance", "dependenc", "contingency"],
    "commercial": ["price", "pricing", "total cost", "usd", "fixed price", "fixed-price", "licen",
                   "assumption", "payment", "tco", "discount", "invoice", "rate card", "exclusion",
                   "subscription", "warranty", "breakdown", "optional", "itemi", "excludes"],
    "security": ["iso 27001", "soc 2", "encrypt", "aes-256", "penetration", "mfa", "multi-factor", "rbac",
                 "audit", "gdpr", "dpia", "privacy", "vulnerab", "incident response", "certif", "tls",
                 "data residency", "immutable", "least privilege", "compliance"],
    "support": ["sla", "24x7", "24/7", "reference", "case stud", "similar project", "customer success",
                "csat", "service desk", "escalation", "uptime", "response time", "years", "clients",
                "helpdesk", "support", "onboarding", "account manager", "quarterly review", "nps"],
}
BANK_HINTS = {"technical": "technical", "implementation": "implementation", "delivery": "implementation",
              "commercial": "commercial", "price": "commercial", "cost": "commercial",
              "security": "security", "compliance": "security", "support": "support",
              "experience": "support"}


class MockClient:
    """Deterministic keyword-based scorer so the whole pipeline can be demoed without an API key."""
    provider, model = "offline-demo", "keyword-scorer-v1"

    def complete_json(self, system: str, user: str, repair_of: str | None = None) -> str:
        criteria = json.loads(re.search(r"<criteria>\s*(.*?)\s*</criteria>", user, re.S).group(1))
        name = re.search(r"<supplier_name>(.*?)</supplier_name>", user, re.S).group(1)
        document = re.search(r"<document>\n(.*)\n</document>", user, re.S).group(1)
        lower = document.lower()
        sentences = re.split(r"(?<=[.!?])\s+|\n+", document)

        results, risks = [], []
        for crit in criteria:
            inspect = crit.get("what_to_inspect") or crit.get("description") or ""
            label = f"{crit['name']} {inspect}".lower()
            bank_key = next((v for k, v in BANK_HINTS.items() if k in crit["name"].lower()), None) \
                or next((v for k, v in BANK_HINTS.items() if k in label), None)
            bank = KEYWORD_BANK.get(bank_key) or sorted({w for w in re.findall(r"[a-z]{4,}", label)})
            hits = [kw for kw in bank if re.search(r"\b" + re.escape(kw), lower)]
            ratio = min(1.0, len(hits) / max(1.0, 0.6 * len(bank)))
            score = round(crit["max_score"] * (0.1 + 0.9 * ratio), 1) if hits else 0.0
            best = next((s.strip() for s in sentences
                         if any(re.search(r"\b" + re.escape(kw), s.lower()) for kw in hits)), "")
            evidence = " ".join(best.split()[:40]) if best else ""
            if ratio < 0.4:
                risks.append(f"Limited detail for '{crit['name']}'.")
            results.append({
                "criterion_id": crit["criterion_id"], "score": score, "max_score": crit["max_score"],
                "justification": f"Offline demo scorer: {len(hits)} of {len(bank)} indicator terms found "
                                 f"({', '.join(hits[:4]) or 'none'}).",
                "evidence": evidence})
        return json.dumps({"supplier_name": name, "criteria": results, "risks": risks,
                           "overall_summary": "Generated by the offline keyword scorer, not an LLM. "
                                              "Use a real provider for production evaluations."})


class FaultInjectingClient:
    """Wraps a provider and damages its reply for chosen suppliers, to demonstrate the Validation Tool.

    Injected faults: score above the maximum, a non-numeric score, a missing evidence quote, a dropped
    criterion, an invented criterion id, a renamed supplier and markdown fences around the JSON.
    """

    def __init__(self, inner, target_suppliers):
        self.inner, self.targets = inner, set(target_suppliers)

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

    def complete_json(self, system: str, user: str, repair_of: str | None = None) -> str:
        reply = self.inner.complete_json(system, user, repair_of)
        match = re.search(r"<supplier_name>(.*?)</supplier_name>", user, re.S)
        if repair_of is not None or not match or match.group(1) not in self.targets:
            return reply
        data = json.loads(reply)
        items = data["criteria"]
        if len(items) >= 1:
            items[0]["score"] = 15                              # above max  -> V7 clipped
        if len(items) >= 2:
            items[1]["score"] = "eight"                         # non-numeric -> V5
        if len(items) >= 3:
            items[2].pop("evidence", None)                      # no quote   -> V9
        if len(items) >= 4:
            items.pop()                                         # dropped    -> V8 imputed
        items.append({"criterion_id": 999, "score": 7, "max_score": 10,
                      "justification": "invented criterion", "evidence": ""})   # unknown -> V3
        data["supplier_name"] = data["supplier_name"].upper() + " INC"          # renamed -> V2
        return "Sure! Here is the evaluation:\n```json\n" + json.dumps(data) + "\n```"


# --------------------------------------------------------------------------- selection helpers
def _get(name: str, secrets: dict | None) -> str | None:
    return os.environ.get(name) or (secrets or {}).get(name)


def provider_available(provider: str, secrets: dict | None = None) -> bool:
    return provider == "mock" or bool(_get(f"{provider.upper()}_API_KEY", secrets))


def default_provider(secrets: dict | None = None) -> str:
    for provider in ("anthropic", "openai"):
        if provider_available(provider, secrets):
            return provider
    return "mock"


def build_client(provider: str | None = None, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None, secrets: dict | None = None):
    provider = (provider or _get("LLM_PROVIDER", secrets) or default_provider(secrets)).lower()
    if provider == "mock":
        return MockClient()
    model = model or _get(f"{provider.upper()}_MODEL", secrets) or _get("LLM_MODEL", secrets) \
        or DEFAULT_MODELS.get(provider)
    key = api_key or _get(f"{provider.upper()}_API_KEY", secrets)
    if not key:
        raise LLMConfigError(f"No API key for '{provider}'. Set {provider.upper()}_API_KEY, add it to "
                             "Streamlit secrets, or paste it in the sidebar.")
    if provider == "anthropic":
        return AnthropicClient(key, model)
    if provider == "openai":
        return OpenAIClient(key, model, base_url or _get("OPENAI_BASE_URL", secrets))
    raise LLMConfigError(f"Unknown LLM provider '{provider}'.")
