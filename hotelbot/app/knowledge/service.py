"""Knowledge retrieval.

`KnowledgeRetriever` is the replaceable interface. The MVP implementation,
`LexicalRetriever`, is an in-memory IDF-weighted term-coverage scorer over
the hotel's knowledge documents (content + curated keywords, all languages,
diacritic-folded and crudely stemmed).

Why coverage rather than raw BM25: the score must double as a *grounding*
signal ("is there enough evidence to answer?"). Coverage = fraction of the
query's informative weight that a document explains, so it is bounded in
[0, 1] and a question about something absent from the pack (e.g. "sauna")
scores near zero instead of being matched to the least-bad document.

A pgvector/embedding retriever can implement the same Protocol later.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import KnowledgeDocument
from app.knowledge.schemas import RetrievalResult, RetrievedItem
from app.text import fold, stem, tokens

# Function words that carry no retrieval signal (folded, both languages).
STOPWORDS = frozenset(
    """
    a an the is are was were be been am do does did can could would should will may might
    i me my we our us you your he she it they them their this that these those there here
    what when where which who whom why how much many any some please thanks thank hi hello
    to of in on at for from with by about as and or but if so not no yes ok okay also just
    have has had get got need want like know tell let bring time
    send more some give another again
    ja ti on ona ono mi vi oni one ona je su sam si smo ste bi bih bismo biste da li ne ni
    i a ali ili pa te jer kako sta sto koji koja koje kad kada gdje gde ima imate imamo
    u na za od do sa s iz kod po o pri prema mogu moze mozemo mozete molim hvala zdravo
    dobar dan vec jos samo li se me te nas vas mene tebe vama nama ovo to taj ta ovaj ova
    zelim zelimo hocu treba trebam trebamo recite kazite znate interesuje koliko
    posaljite donesite dajte vise
    vrijeme sati doci dodjem jedan jedna dva dvije dvoje tri troje cetiri cetvoro pet
    one two three four five
    """.split()
)
# Russian function words, folded with the same transliteration as queries.
STOPWORDS |= frozenset(
    fold(w) for w in """
    что как когда где сколько какой какая какие есть ли у вас вам нас нам мне меня мы вы я он она
    это в во на с со и а но или не нет да можно пожалуйста спасибо здравствуйте привет ли бы
    для по от до из за о об при хочу хотим нужно нужен нужна будет время
    ещё еще пришлите принесите дайте
    """.split()
)


class KnowledgeRetriever(Protocol):
    def search(self, query: str, language: str | None = None, k: int = 3) -> RetrievalResult: ...


class LexicalRetriever:
    KEYWORD_WEIGHT = 2.0

    def __init__(self, documents: list[RetrievedItem], min_score: float) -> None:
        self.documents = documents
        self.min_score = min_score
        self._doc_terms: list[Counter[str]] = []
        df: Counter[str] = Counter()
        for doc in documents:
            terms: Counter[str] = Counter()
            for text in doc.content.values():
                terms.update(_informative(text))
            for words in doc.metadata.get("_keywords", {}).values():
                for word in words:
                    for t in _informative(word):
                        terms[t] += self.KEYWORD_WEIGHT
            terms.update(_informative(doc.category.replace("_", " ")))
            self._doc_terms.append(terms)
            df.update(terms.keys())
        n = max(len(documents), 1)
        self._idf = {t: math.log(1 + n / c) for t, c in df.items()}
        self._unknown_idf = math.log(1 + n)

    def search(self, query: str, language: str | None = None, k: int = 3) -> RetrievalResult:
        q_terms = list(dict.fromkeys(_informative(query)))  # dedupe, keep order
        if not q_terms or not self.documents:
            return RetrievalResult(query=query, items=[], min_score=self.min_score)
        weights = {t: self._idf.get(t, self._unknown_idf) for t in q_terms}
        total = sum(weights.values())
        scored: list[RetrievedItem] = []
        for doc, terms in zip(self.documents, self._doc_terms):
            matched = sum(w for t, w in weights.items() if t in terms)
            if matched <= 0:
                continue
            score = matched / total
            scored.append(doc.model_copy(update={"score": round(score, 4)}))
        scored.sort(key=lambda d: d.score, reverse=True)
        return RetrievalResult(query=query, items=scored[:k], min_score=self.min_score)


def _informative(text: str) -> list[str]:
    # Stopwords are removed as whole tokens *before* stemming: filtering on
    # stems made unrelated words collide (stopword "interesuje" -> "inter"
    # removed "internet"). Digits (dates, times, room numbers) say little
    # about the topic and would dilute coverage, so they are not terms either.
    return [stem(t) for t in tokens(text)
            if t not in STOPWORDS and len(t) > 1 and not any(c.isdigit() for c in t)]


class KnowledgeService:
    """Facade used by the agent: loads a hotel's documents and searches them."""

    def __init__(self, retriever: KnowledgeRetriever) -> None:
        self.retriever = retriever

    @property
    def topics(self) -> frozenset[str]:
        """Knowledge capabilities: topics this property's pack covers."""
        docs = getattr(self.retriever, "documents", [])
        return frozenset(d.topic for d in docs)

    @classmethod
    def from_db(cls, session: Session, property_id: str, min_score: float) -> KnowledgeService:
        docs = session.scalars(
            select(KnowledgeDocument).where(KnowledgeDocument.property_id == property_id)
        )
        items = [
            RetrievedItem(
                key=d.item_key,
                category=d.category,
                source=d.source,
                content=d.content,
                metadata={**d.extra, "_keywords": d.keywords},
                score=0.0,
            )
            for d in docs
        ]
        return cls(LexicalRetriever(items, min_score=min_score))

    def search(self, query: str, language: str | None = None, k: int = 3) -> RetrievalResult:
        return self.retriever.search(query, language=language, k=k)
