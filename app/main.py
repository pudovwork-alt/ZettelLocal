from __future__ import annotations

import io
import json
import os
import re
import sys
import zipfile
from collections import defaultdict
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ai_engine import PersonalTextModel, extract_keywords, tokenize
from .database import Database
from .smart_tools import (
    WIKILINK_RE, SOURCE_RE, NUMBER_RE,
    article_analysis,
    atomize_note,
    dashboard,
    detect_contradictions,
    knowledge_gaps,
    lexical_similarity,
    note_quality,
    reflective_questions,
    suggest_links,
    unfinished_notes, auto_topics, fact_index, interest_timeline, note_statuses, rediscover_note, possible_fact_conflicts,
)

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
    STATIC_DIR = BUNDLE_DIR / "app" / "static"
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    STATIC_DIR = Path(__file__).resolve().parent / "static"
DATA_DIR = Path(os.environ.get("ZETTEL_DATA_DIR", BASE_DIR / "data"))
DB = Database(DATA_DIR / "zettel.db")
MODEL = PersonalTextModel(DATA_DIR / "model")
TAG_RE = re.compile(r"(?<!\w)#([\wА-Яа-яЁё\-]{2,})", re.UNICODE)

app = FastAPI(title="ZettelLocal", version="2.12.1")


class NoteCreate(BaseModel):
    title: str = "Без названия"
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    category_id: int | None = None


class NoteUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    favorite: bool | None = None
    category_id: int | None = None


class CategoryCreate(BaseModel):
    title: str
    parent_id: int | None = None


class CategoryUpdate(BaseModel):
    title: str | None = None
    parent_id: int | None = None


class SearchBody(BaseModel):
    query: str
    limit: int = 10


class TrainBody(BaseModel):
    epochs: int = 4
    dimensions: int = 64


class ChatBody(BaseModel):
    question: str
    note_id: int | None = None


class SettingsBody(BaseModel):
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    use_ollama: bool = False


class RelationCreate(BaseModel):
    source_id: int
    target_id: int
    relation_type: str = "связано с"


class EntityRelationDecision(BaseModel):
    source: str
    target: str
    status: str = "confirmed"


class FeedbackBody(BaseModel):
    kind: str = "related"
    rating: int
    query: str = ""
    source_note_id: int | None = None
    target_note_id: int | None = None


class AnswerCreate(BaseModel):
    question: str
    answer: str
    source_note_ids: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class AtomizeCreateBody(BaseModel):
    items: list[dict[str, str]] = Field(default_factory=list)
    link_from_source: bool = True


class PlainTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self.parts)


def normalize_tags(tags: list[str] | None, content: str = "") -> list[str]:
    result = {str(t).strip().lstrip("#").lower() for t in (tags or []) if str(t).strip()}
    result.update(tag.lower() for tag in TAG_RE.findall(content or ""))
    return sorted(result)


def note_excerpt(note: dict[str, Any], max_len: int = 260) -> str:
    text = re.sub(r"[#>*_`\[\]]", " ", note.get("content", ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


def backlinks_for(title: str, notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    title_lower = title.strip().lower()
    found = []
    for note in notes:
        links = [x.strip().lower() for x in WIKILINK_RE.findall(note.get("content", ""))]
        if title_lower in links:
            found.append(note)
    return found


def category_payload() -> dict[str, Any]:
    categories = DB.list_categories()
    by_parent: dict[int | None, list[dict[str, Any]]] = {}
    for category in categories:
        by_parent.setdefault(category.get("parent_id"), []).append(category)

    def build(parent_id: int | None) -> tuple[list[dict[str, Any]], int]:
        nodes: list[dict[str, Any]] = []
        total = 0
        for category in by_parent.get(parent_id, []):
            children, children_count = build(int(category["id"]))
            node = dict(category)
            node["children"] = children
            node["total_count"] = int(node.get("direct_count", 0)) + children_count
            nodes.append(node)
            total += node["total_count"]
        return nodes, total

    tree, _ = build(None)
    unfiled = len(DB.list_notes(category_id=0))
    return {"tree": tree, "flat": categories, "unfiled": unfiled}


def graph_payload(notes: list[dict[str, Any]]) -> dict[str, Any]:
    by_title = {n["title"].strip().lower(): n for n in notes}
    links: list[dict[str, Any]] = []
    for note in notes:
        for target_title in WIKILINK_RE.findall(note.get("content", "")):
            target = by_title.get(target_title.strip().lower())
            if target and target["id"] != note["id"]:
                links.append({"source": note["id"], "target": target["id"], "type": "wiki"})
    for relation in DB.list_relations():
        links.append({
            "source": relation["source_id"],
            "target": relation["target_id"],
            "type": relation["relation_type"],
        })
    unique = {(x["source"], x["target"], x["type"]): x for x in links}
    return {
        "nodes": [
            {
                "id": n["id"],
                "title": n["title"],
                "favorite": n["favorite"],
                "tags": n["tags"],
                "category_id": n.get("category_id"),
            }
            for n in notes
        ],
        "links": list(unique.values()),
    }


def hybrid_search(query: str, notes: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []
    semantic_results = MODEL.semantic_search(query, notes, max(limit * 4, 30)) if MODEL.ready else []
    semantic_scores = {int(n["id"]): float(n.get("score") or 0.0) for n in semantic_results}
    qtokens = set(tokenize(query))
    scored: list[tuple[float, dict[str, Any]]] = []
    for note in notes:
        title = note.get("title", "")
        content = note.get("content", "")
        ntext = f"{title} {content} {' '.join(note.get('tags') or [])}"
        ntokens = set(tokenize(ntext))
        token_match = len(qtokens & ntokens) / max(len(qtokens), 1)
        phrase = 1.0 if query.lower() in ntext.lower() else 0.0
        title_bonus = 0.22 if query.lower() in title.lower() else 0.0
        lexical = lexical_similarity(query, ntext)
        semantic = semantic_scores.get(int(note["id"]), 0.0)
        feedback = DB.feedback_bonus(query, int(note["id"]))
        score = semantic * 0.55 + token_match * 0.18 + lexical * 0.12 + phrase * 0.1 + title_bonus + feedback
        if score > 0:
            scored.append((score, note))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{**note, "score": round(score, 4), "excerpt": note_excerpt(note)} for score, note in scored[:limit]]


def best_verified_answer(question: str) -> tuple[dict[str, Any] | None, float]:
    best: dict[str, Any] | None = None
    best_score = 0.0
    for answer in DB.list_answers():
        score = lexical_similarity(question, answer.get("question", ""))
        if question.lower().strip() == answer.get("question", "").lower().strip():
            score = 1.0
        if score > best_score:
            best, best_score = answer, score
    return best, best_score


def read_uploaded_text(filename: str, raw: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".json", ".rtf"}:
        return raw.decode("utf-8", errors="replace")
    if suffix in {".html", ".htm"}:
        parser = PlainTextHTMLParser()
        parser.feed(raw.decode("utf-8", errors="replace"))
        return parser.text()
    if suffix == ".docx":
        try:
            from docx import Document
            document = Document(io.BytesIO(raw))
            return "\n\n".join(p.text for p in document.paragraphs if p.text.strip())
        except ImportError as exc:
            raise ValueError("Для DOCX установите зависимости из обновлённого requirements.txt") from exc
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
        except ImportError as exc:
            raise ValueError("Для PDF установите зависимости из обновлённого requirements.txt") from exc
    raise ValueError(f"Формат {suffix or filename} пока не поддерживается")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": "2.7.0", "notes": len(DB.list_notes()), "model": MODEL.status()}


# Notes ---------------------------------------------------------------
@app.get("/api/notes")
def list_notes(
    q: str = "",
    favorite: bool | None = None,
    category_id: int | None = None,
    include_children: bool = True,
) -> list[dict[str, Any]]:
    notes = DB.list_notes(q, favorite, category_id, include_children)
    for note in notes:
        note["excerpt"] = note_excerpt(note)
    return notes


@app.post("/api/notes")
def create_note(body: NoteCreate) -> dict[str, Any]:
    return DB.create_note(
        body.title,
        body.content,
        normalize_tags(body.tags, body.content),
        body.category_id,
    )


@app.post("/api/notes/open-link")
def open_or_create_link(body: NoteCreate) -> dict[str, Any]:
    existing = DB.find_by_title(body.title)
    return existing or DB.create_note(body.title, "", [], body.category_id)


@app.get("/api/notes/{note_id}")
def get_note(note_id: int) -> dict[str, Any]:
    note = DB.get_note(note_id)
    if not note:
        raise HTTPException(404, "Заметка не найдена")
    notes = DB.list_notes()
    note["backlinks"] = [
        {"id": x["id"], "title": x["title"], "excerpt": note_excerpt(x, 150)}
        for x in backlinks_for(note["title"], notes)
        if x["id"] != note_id
    ]
    outgoing = []
    for title in dict.fromkeys(WIKILINK_RE.findall(note.get("content", ""))):
        target = DB.find_by_title(title)
        outgoing.append({"title": title, "id": target.get("id") if target else None, "exists": bool(target)})
    note["outgoing"] = outgoing
    note["relations"] = DB.relations_for_note(note_id)
    return note


@app.put("/api/notes/{note_id}")
def update_note(note_id: int, body: NoteUpdate) -> dict[str, Any]:
    current = DB.get_note(note_id)
    if not current:
        raise HTTPException(404, "Заметка не найдена")
    fields = body.model_dump(exclude_unset=True)
    if "title" in fields and fields["title"] is None:
        fields.pop("title")
    if "content" in fields and fields["content"] is None:
        fields.pop("content")
    content = fields.get("content", current["content"])
    fields["tags"] = normalize_tags(fields.get("tags", current["tags"]), content)
    if fields.get("category_id") == 0:
        fields["category_id"] = None
    updated = DB.update_note(note_id, fields)
    if not updated:
        raise HTTPException(404, "Заметка не найдена")
    return updated


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int) -> dict[str, bool]:
    if not DB.delete_note(note_id):
        raise HTTPException(404, "Заметка не найдена")
    return {"ok": True}


@app.post("/api/daily")
def daily_note() -> dict[str, Any]:
    title = date.today().isoformat()
    existing = DB.find_by_title(title)
    if existing:
        return existing
    return DB.create_note(title, f"# {title}\n\n## Главное\n\n## Задачи\n- [ ] \n\n## Мысли\n", ["дневник"])


@app.get("/api/tags")
def tags() -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for note in DB.list_notes():
        for tag in note.get("tags", []):
            counts[tag] = counts.get(tag, 0) + 1
    return [{"tag": tag, "count": count} for tag, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]


# Categories ----------------------------------------------------------
@app.get("/api/categories")
def categories() -> dict[str, Any]:
    return category_payload()


@app.post("/api/categories")
def create_category(body: CategoryCreate) -> dict[str, Any]:
    return DB.create_category(body.title, body.parent_id)


@app.put("/api/categories/{category_id}")
def update_category(category_id: int, body: CategoryUpdate) -> dict[str, Any]:
    try:
        data = body.model_dump(exclude_unset=True)
        updated = DB.update_category(
            category_id,
            title=data.get("title"),
            parent_id=data.get("parent_id"),
            set_parent="parent_id" in data,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not updated:
        raise HTTPException(404, "Категория не найдена")
    return updated


@app.delete("/api/categories/{category_id}")
def delete_category(category_id: int) -> dict[str, bool]:
    if not DB.delete_category(category_id):
        raise HTTPException(404, "Категория не найдена")
    return {"ok": True}


# Relations and graph -------------------------------------------------
@app.get("/api/graph")
def graph() -> dict[str, Any]:
    return graph_payload(DB.list_notes())


@app.post("/api/relations")
def add_relation(body: RelationCreate) -> dict[str, Any]:
    try:
        return DB.add_relation(body.source_id, body.target_id, body.relation_type)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/relations/{relation_id}")
def delete_relation(relation_id: int) -> dict[str, bool]:
    if not DB.delete_relation(relation_id):
        raise HTTPException(404, "Связь не найдена")
    return {"ok": True}


# Search and AI -------------------------------------------------------
@app.post("/api/search/semantic")
def semantic_search(body: SearchBody) -> dict[str, Any]:
    results = hybrid_search(body.query, DB.list_notes(), max(1, min(body.limit, 50)))
    return {"results": results, "model": MODEL.status(), "mode": "hybrid"}


@app.get("/api/ai/status")
def ai_status() -> dict[str, Any]:
    settings = DB.all_settings()
    return {
        "personal_model": MODEL.status(),
        "ollama": {
            "enabled": settings.get("use_ollama", "false") == "true",
            "url": settings.get("ollama_url", "http://127.0.0.1:11434"),
            "model": settings.get("ollama_model", ""),
        },
        "verified_answers": len(DB.list_answers()),
    }


@app.post("/api/ai/train")
def train_model(body: TrainBody) -> dict[str, Any]:
    try:
        return MODEL.train(
            DB.list_notes(),
            dimensions=max(24, min(body.dimensions, 192)),
            epochs=max(1, min(body.epochs, 12)),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/ai/related/{note_id}")
def related_notes(note_id: int, limit: int = Query(8, ge=1, le=30)) -> dict[str, Any]:
    note = DB.get_note(note_id)
    if not note:
        raise HTTPException(404, "Заметка не найдена")
    results = suggest_links(note, DB.list_notes(), MODEL, limit)
    enriched = []
    for item in results:
        target = DB.get_note(int(item["id"])) or {}
        enriched.append({**target, **item, "excerpt": note_excerpt(target)})
    return {"results": enriched, "model": MODEL.status()}


@app.get("/api/ai/smart/{note_id}")
def smart_note(note_id: int) -> dict[str, Any]:
    note = DB.get_note(note_id)
    if not note:
        raise HTTPException(404, "Заметка не найдена")
    notes = DB.list_notes()
    return {
        "quality": note_quality(note),
        "questions": reflective_questions(note),
        "links": suggest_links(note, notes, MODEL, 8),
        "contradictions": detect_contradictions(note, notes, MODEL, 6),
        "article": article_analysis(note),
        "atoms": atomize_note(note, 8),
    }


@app.get("/api/ai/dashboard")
def ai_dashboard() -> dict[str, Any]:
    notes = DB.list_notes()
    data = dashboard(notes)
    data.update({
        "topics": auto_topics(notes, 12),
        "unfinished": unfinished_notes(notes, 16),
        "facts": fact_index(notes),
        "interest_timeline": interest_timeline(notes, 8),
        "statuses": note_statuses(notes),
        "rediscover": rediscover_note(notes),
        "fact_conflicts": possible_fact_conflicts(notes, 8),
    })
    return data


@app.get("/api/ai/gaps")
def ai_gaps() -> dict[str, Any]:
    return {"gaps": knowledge_gaps(DB.list_notes(), 20)}


@app.get("/api/ai/summarize/{note_id}")
def summarize_note(note_id: int) -> dict[str, str]:
    note = DB.get_note(note_id)
    if not note:
        raise HTTPException(404, "Заметка не найдена")
    return {"summary": MODEL.summarize(note.get("content", ""), 3)}


@app.get("/api/ai/tags/{note_id}")
def suggest_tags(note_id: int) -> dict[str, list[str]]:
    note = DB.get_note(note_id)
    if not note:
        raise HTTPException(404, "Заметка не найдена")
    existing = set(note.get("tags", []))
    suggested = [x for x in extract_keywords(f"{note['title']} {note['content']}", 14) if x not in existing]
    return {"tags": suggested[:10]}


@app.post("/api/ai/atomize/{note_id}/create")
def create_atoms(note_id: int, body: AtomizeCreateBody) -> dict[str, Any]:
    source = DB.get_note(note_id)
    if not source:
        raise HTTPException(404, "Заметка не найдена")
    items = body.items or atomize_note(source, 8)
    created = []
    links = []
    for item in items[:20]:
        title = str(item.get("title") or "Новая атомарная заметка").strip()[:150]
        content = str(item.get("content") or "").strip()
        existing = DB.find_by_title(title)
        note = existing or DB.create_note(title, content, source.get("tags") or [], source.get("category_id"))
        created.append({"id": note["id"], "title": note["title"]})
        links.append(f"[[{note['title']}]]")
    if body.link_from_source and links:
        addition = "\n\n## Атомарные заметки\n" + "\n".join(f"- {x}" for x in links)
        if "## Атомарные заметки" not in source.get("content", ""):
            DB.update_note(note_id, {"content": source.get("content", "") + addition})
    return {"created": created}


@app.post("/api/ai/feedback")
def save_feedback(body: FeedbackBody) -> dict[str, Any]:
    return DB.add_feedback(body.kind, body.rating, body.query, body.source_note_id, body.target_note_id)


@app.get("/api/answers")
def list_answers() -> list[dict[str, Any]]:
    return DB.list_answers()


@app.post("/api/answers")
def create_answer(body: AnswerCreate) -> dict[str, Any]:
    if not body.question.strip() or not body.answer.strip():
        raise HTTPException(400, "Заполните вопрос и эталонный ответ")
    return DB.create_answer(body.question, body.answer, body.source_note_ids, normalize_tags(body.tags))


@app.delete("/api/answers/{answer_id}")
def delete_answer(answer_id: int) -> dict[str, bool]:
    if not DB.delete_answer(answer_id):
        raise HTTPException(404, "Ответ не найден")
    return {"ok": True}


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    all_values = DB.all_settings()
    return {
        "ollama_url": all_values.get("ollama_url", "http://127.0.0.1:11434"),
        "ollama_model": all_values.get("ollama_model", ""),
        "use_ollama": all_values.get("use_ollama", "false") == "true",
    }


@app.put("/api/settings")
def save_settings(body: SettingsBody) -> dict[str, Any]:
    DB.set_setting("ollama_url", body.ollama_url.rstrip("/"))
    DB.set_setting("ollama_model", body.ollama_model.strip())
    DB.set_setting("use_ollama", "true" if body.use_ollama else "false")
    return body.model_dump()


@app.post("/api/ai/chat")
async def ai_chat(body: ChatBody) -> dict[str, Any]:
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Введите вопрос")

    verified, verified_score = best_verified_answer(question)
    if verified and verified_score >= 0.72:
        source_notes = [DB.get_note(int(note_id)) for note_id in verified.get("source_note_ids", [])]
        sources = [
            {"id": n["id"], "title": n["title"], "excerpt": note_excerpt(n, 220)}
            for n in source_notes if n
        ]
        return {
            "answer": verified["answer"],
            "sources": sources,
            "mode": "verified",
            "confidence": round(verified_score, 2),
            "verified_answer_id": verified["id"],
        }

    notes = DB.list_notes()
    context_notes = hybrid_search(question, notes, 7)
    if body.note_id:
        active = DB.get_note(body.note_id)
        if active and all(n["id"] != active["id"] for n in context_notes):
            context_notes.insert(0, active)
    sources = [{"id": n["id"], "title": n["title"], "excerpt": note_excerpt(n, 240)} for n in context_notes]

    settings = get_settings()
    if settings["use_ollama"] and settings["ollama_model"]:
        context = "\n\n".join(
            f"[ИСТОЧНИК {i + 1}] {n['title']}\n{n.get('content', '')[:5000]}"
            for i, n in enumerate(context_notes[:7])
        )
        prompt = (
            "Ты локальный помощник по личной базе знаний. Отвечай только на основании контекста. "
            "После каждого существенного утверждения указывай источник в формате [1], [2]. "
            "Если данных недостаточно или источники противоречат друг другу, прямо скажи об этом. "
            "Не выдумывай факты. Пиши по-русски и ясно.\n\n"
            f"КОНТЕКСТ:\n{context}\n\nВОПРОС: {question}\n\nОТВЕТ:"
        )
        try:
            async with httpx.AsyncClient(timeout=240) as client:
                response = await client.post(
                    f"{settings['ollama_url']}/api/generate",
                    json={"model": settings["ollama_model"], "prompt": prompt, "stream": False},
                )
                response.raise_for_status()
                answer = response.json().get("response", "").strip()
            confidence = round(sum(float(n.get("score") or 0) for n in context_notes[:3]) / max(min(len(context_notes), 3), 1), 2)
            return {"answer": answer or "Локальная модель не вернула текст.", "sources": sources, "mode": "ollama", "confidence": confidence}
        except Exception as exc:
            fallback = "Ollama недоступна. Найдены связанные заметки:\n\n" + "\n".join(
                f"• {s['title']}: {s['excerpt']}" for s in sources
            )
            return {"answer": fallback, "sources": sources, "mode": "fallback", "warning": str(exc), "confidence": 0.0}

    if sources:
        answer = "Нашёл наиболее близкие материалы в вашей базе:\n\n" + "\n\n".join(
            f"**{i + 1}. {s['title']}**\n{s['excerpt']}" for i, s in enumerate(sources)
        )
        confidence = round(sum(float(n.get("score") or 0) for n in context_notes[:3]) / max(min(len(context_notes), 3), 1), 2)
    else:
        answer = "В заметках пока нет подходящего материала. Добавьте больше текста или обучите персональную модель."
        confidence = 0.0
    return {"answer": answer, "sources": sources, "mode": "local-retrieval", "confidence": confidence}


# Import/export -------------------------------------------------------
@app.get("/api/export")
def export_notes() -> StreamingResponse:
    memory = io.BytesIO()
    notes = DB.list_notes()
    categories_data = DB.list_categories()
    relations_data = DB.list_relations()
    answers_data = DB.list_answers()
    with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = []
        for note in notes:
            safe = re.sub(r"[^\wА-Яа-яЁё\- ]+", "", note["title"]).strip()[:80] or f"note-{note['id']}"
            filename = f"notes/{note['id']:04d} - {safe}.md"
            metadata = "---\n" + json.dumps(
                {
                    "id": note["id"],
                    "title": note["title"],
                    "tags": note["tags"],
                    "favorite": note["favorite"],
                    "category_id": note.get("category_id"),
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n---\n\n"
            archive.writestr(filename, metadata + note["content"])
            manifest.append({k: note.get(k) for k in ("id", "title", "tags", "favorite", "category_id", "created_at", "updated_at")})
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("categories.json", json.dumps(categories_data, ensure_ascii=False, indent=2))
        archive.writestr("relations.json", json.dumps(relations_data, ensure_ascii=False, indent=2))
        archive.writestr("verified_answers.json", json.dumps(answers_data, ensure_ascii=False, indent=2))
    memory.seek(0)
    return StreamingResponse(
        memory,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="ZettelLocal-backup.zip"'},
    )


@app.post("/api/import")
async def import_notes(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    imported = 0
    categories_map: dict[int, int] = {}
    notes_map: dict[int, int] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if "categories.json" in archive.namelist():
                raw_categories = json.loads(archive.read("categories.json").decode("utf-8"))
                pending = list(raw_categories)
                for _ in range(len(pending) + 2):
                    remaining = []
                    for category in pending:
                        old_parent = category.get("parent_id")
                        if old_parent and int(old_parent) not in categories_map:
                            remaining.append(category)
                            continue
                        created = DB.create_category(str(category.get("title") or "Категория"), categories_map.get(int(old_parent)) if old_parent else None)
                        categories_map[int(category["id"])] = int(created["id"])
                    if len(remaining) == len(pending):
                        for category in remaining:
                            created = DB.create_category(str(category.get("title") or "Категория"), None)
                            categories_map[int(category["id"])] = int(created["id"])
                        break
                    pending = remaining
                    if not pending:
                        break

            for name in archive.namelist():
                if not name.lower().endswith(".md") or name.startswith("__"):
                    continue
                text = archive.read(name).decode("utf-8", errors="replace")
                title = re.sub(r"^\d+\s*-\s*", "", Path(name).stem).strip() or "Импортированная заметка"
                content = text
                tags_list: list[str] = []
                favorite = False
                old_id: int | None = None
                category_id: int | None = None
                if text.startswith("---\n"):
                    end = text.find("\n---\n", 4)
                    if end > 0:
                        try:
                            meta = json.loads(text[4:end])
                            title = str(meta.get("title") or title)
                            tags_list = list(meta.get("tags") or [])
                            favorite = bool(meta.get("favorite"))
                            old_id = int(meta["id"]) if meta.get("id") is not None else None
                            old_category = meta.get("category_id")
                            category_id = categories_map.get(int(old_category)) if old_category else None
                            content = text[end + 5:].lstrip("\n")
                        except Exception:
                            pass
                note = DB.create_note(title, content, normalize_tags(tags_list, content), category_id)
                if favorite:
                    DB.update_note(note["id"], {"favorite": True})
                if old_id is not None:
                    notes_map[old_id] = int(note["id"])
                imported += 1

            if "relations.json" in archive.namelist():
                for relation in json.loads(archive.read("relations.json").decode("utf-8")):
                    source_id = notes_map.get(int(relation.get("source_id", 0)))
                    target_id = notes_map.get(int(relation.get("target_id", 0)))
                    if source_id and target_id:
                        try:
                            DB.add_relation(source_id, target_id, str(relation.get("relation_type") or "связано с"))
                        except ValueError:
                            pass
            if "verified_answers.json" in archive.namelist():
                for answer in json.loads(archive.read("verified_answers.json").decode("utf-8")):
                    source_ids = [notes_map[x] for x in answer.get("source_note_ids", []) if x in notes_map]
                    DB.create_answer(str(answer.get("question") or ""), str(answer.get("answer") or ""), source_ids, answer.get("tags") or [])
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, "Нужен ZIP-архив, созданный экспортом ZettelLocal") from exc
    return {"ok": True, "imported": imported, "categories": len(categories_map)}


@app.post("/api/import/files")
async def import_files(files: list[UploadFile] = File(...), category_id: int | None = None) -> dict[str, Any]:
    created = []
    errors = []
    for upload in files[:100]:
        filename = upload.filename or "Документ"
        try:
            raw = await upload.read()
            content = read_uploaded_text(filename, raw)
            if not content.strip():
                raise ValueError("В документе не найден текст")
            title = Path(filename).stem.strip() or "Импортированный документ"
            note = DB.create_note(title, content, normalize_tags([], content), category_id)
            created.append({"id": note["id"], "title": note["title"]})
        except Exception as exc:
            errors.append({"file": filename, "error": str(exc)})
    return {"created": created, "errors": errors}



# Smart workbench 2.7 ------------------------------------------------
ENTITY_STOP = {"который","которая","которые","этого","этой","после","перед","через","также","можно","нужно","будет","были","есть","если","для","при","или","как","что","это"}

def extract_entities_local(text: str, notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text = text or ""
    found: dict[str, dict[str, Any]] = {}
    patterns = [
        ("DOI", r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I),
        ("PMID", r"\bPMID\s*:?\s*(\d{5,9})\b", re.I),
        ("Дата", r"\b(?:19|20)\d{2}\b", 0),
        ("Организация", r"\b(?:ФГБУ|ФГБОУ|АНО|ООО|ПАО|АО|НИИ|Минздрав(?:а)?|университет)\s+[А-ЯA-ZЁ][^\n,.;:]{2,60}", re.I),
    ]
    for kind, pat, flags in patterns:
        for m in re.finditer(pat, text, flags):
            value=(m.group(1) if kind=="PMID" and m.groups() else m.group(0)).strip()
            key=f"{kind}:{value.lower()}"; found[key]={"type":kind,"value":value,"mentions":1}
    # Capitalized multiword names and recurring domain terms from note titles.
    for m in re.finditer(r"(?<![.!?]\s)\b[А-ЯЁA-Z][а-яёa-z-]+(?:\s+[А-ЯЁA-Z][а-яёa-z-]+){1,2}\b", text):
        value=m.group(0).strip()
        if value.lower() not in ENTITY_STOP: found.setdefault("Имя:"+value.lower(), {"type":"Имя/понятие","value":value,"mentions":1})
    low=text.lower()
    for n in notes:
        title=str(n.get("title") or "").strip()
        if len(title)>=3 and title.lower() in low:
            item=found.setdefault("Понятие:"+title.lower(), {"type":"Понятие","value":title,"mentions":0,"note_id":n.get("id")})
            item["mentions"] += low.count(title.lower())
    return list(found.values())[:80]

def next_action_local(note: dict[str, Any], notes: list[dict[str, Any]]) -> dict[str, str]:
    content=note.get("content",""); links=WIKILINK_RE.findall(content); words=len(tokenize(content))
    if re.search(r"\b(?:TODO|дописать|проверить|уточнить|сделать|позвонить|написать)\b", content, re.I): return {"kind":"task","title":"Разобрать действия","detail":"В заметке есть незавершённые действия."}
    if words < 45: return {"kind":"develop","title":"Развить заметку","detail":"Сейчас это короткая мысль — добавьте контекст, вывод или источник."}
    if not links: return {"kind":"link","title":"Связать с базой","detail":"В заметке пока нет [[связей]]. Посмотрите предлагаемые связи."}
    if not note.get("tags"): return {"kind":"tag","title":"Добавить теги","detail":"Теги помогут находить эту мысль в тематических срезах."}
    return {"kind":"done","title":"Заметка в хорошем состоянии","detail":"Явных срочных действий не найдено."}

def tasks_from_text(text: str) -> list[dict[str,str]]:
    out=[]
    for line in (text or "").splitlines():
        clean=line.strip()
        if re.match(r"^- \[ \]", clean): out.append({"text":re.sub(r"^- \[ \]\s*","",clean),"source":"checkbox"})
        elif re.search(r"\b(?:TODO|дописать|проверить|уточнить|сделать|позвонить|написать|подготовить|отправить)\b", clean, re.I): out.append({"text":re.sub(r"^[-*]\s*","",clean),"source":"phrase"})
    return out[:30]

@app.get("/api/smart/workbench/{note_id}")
def smart_workbench(note_id: int) -> dict[str, Any]:
    note=DB.get_note(note_id)
    if not note: raise HTTPException(404,"Заметка не найдена")
    notes=DB.list_notes()
    return {"entities":extract_entities_local(f"{note.get('title','')}\n{note.get('content','')}",notes),"tasks":tasks_from_text(note.get('content','')),"next_action":next_action_local(note,notes)}

@app.get("/api/smart/concept")
def concept_page(q: str = Query(..., min_length=2)) -> dict[str, Any]:
    notes=DB.list_notes(); results=hybrid_search(q,notes,20)
    exact=DB.find_by_title(q)
    mentions=[]
    for n in notes:
        count=(str(n.get('title',''))+' '+str(n.get('content',''))).lower().count(q.lower())
        if count: mentions.append({"id":n['id'],"title":n['title'],"count":count,"excerpt":note_excerpt(n,180)})
    mentions.sort(key=lambda x:-x['count'])
    related=[]
    for r in results[:8]:
        if not exact or r['id']!=exact['id']: related.append({"id":r['id'],"title":r['title'],"score":r.get('score',0),"excerpt":r.get('excerpt','')})
    return {"concept":q,"exact_note":exact,"mentions":mentions[:20],"related":related}



def entity_type_normalized(kind: str, value: str) -> str:
    k=(kind or '').lower(); v=(value or '').lower()
    if 'doi' in k or 'pmid' in k: return 'Публикации'
    if 'организа' in k: return 'Организации'
    if 'дата' in k: return 'Даты'
    if any(x in v for x in ['приказ','федеральный закон','постановление','№']): return 'Документы'
    if 'имя' in k: return 'Люди/понятия'
    return 'Понятия'

def entity_alias_key(value: str) -> str:
    v=(value or '').lower().replace('ё','е')
    v=re.sub(r'[^a-zа-я0-9]+',' ',v).strip()
    for x in ['российской федерации','россии','рф']:
        v=v.replace(x,'').strip()
    return v

@app.get('/api/smart/entities')
def smart_entities() -> dict[str, Any]:
    notes=DB.list_notes(); bucket={}
    for n in notes:
        text=f"{n.get('title','')}\n{n.get('content','')}"
        for e in extract_entities_local(text,notes):
            value=e.get('value','').strip()
            if len(value)<2: continue
            key=entity_alias_key(value) or value.lower()
            item=bucket.setdefault(key,{'name':value,'type':entity_type_normalized(e.get('type',''),value),'aliases':set(),'note_ids':set(),'mentions':0,'sources':[]})
            item['aliases'].add(value); item['note_ids'].add(n['id']); item['mentions']+=max(1,int(e.get('mentions',1) or 1))
            if len(item['sources'])<5: item['sources'].append({'id':n['id'],'title':n['title'],'excerpt':note_excerpt(n,120)})
    out=[]
    for item in bucket.values():
        aliases=sorted(item['aliases'],key=len)
        if aliases: item['name']=aliases[0] if len(aliases[0])>2 else item['name']
        out.append({'name':item['name'],'type':item['type'],'aliases':aliases,'notes':len(item['note_ids']),'mentions':item['mentions'],'sources':item['sources'],'importance':min(100, item['mentions']*4+len(item['note_ids'])*8)})
    out.sort(key=lambda x:(-x['importance'],-x['mentions'],x['name'].lower()))
    types={}
    for x in out: types[x['type']]=types.get(x['type'],0)+1
    return {'items':out[:500],'types':types,'total':len(out)}

@app.get("/api/smart/duplicate")
def duplicate_check(title: str = Query(..., min_length=1)) -> dict[str, Any]:
    notes=DB.list_notes(); scored=[]
    for n in notes:
        score=lexical_similarity(title,n.get('title',''))
        if title.lower() in n.get('title','').lower() or n.get('title','').lower() in title.lower(): score=max(score,.72)
        if score>=.28: scored.append({"id":n['id'],"title":n['title'],"score":round(score,3),"excerpt":note_excerpt(n,120)})
    scored.sort(key=lambda x:-x['score'])
    return {"results":scored[:6]}

@app.get("/api/smart/inbox")
def smart_inbox() -> dict[str, Any]:
    notes=DB.list_notes(); items=[]
    for n in notes:
        content=n.get('content',''); score=0; reasons=[]
        if not n.get('category_id'): score+=2; reasons.append('без папки')
        if not n.get('tags'): score+=1; reasons.append('без тегов')
        if not WIKILINK_RE.findall(content): score+=1; reasons.append('без связей')
        if len(tokenize(content))<45: score+=1; reasons.append('короткая мысль')
        if re.search(r"\b(?:TODO|дописать|проверить|уточнить)\b",content,re.I): score+=2; reasons.append('есть действие')
        if score>=3: items.append({"id":n['id'],"title":n['title'],"score":score,"reasons":reasons,"suggested_tags":extract_keywords(f"{n['title']} {content}",5),"next_action":next_action_local(n,notes)})
    items.sort(key=lambda x:-x['score'])
    return {"items":items[:40]}



def _entity_noise(value: str, kind: str = "") -> bool:
    v=(value or '').strip()
    k=(kind or '').strip()
    if not v: return True
    if k == 'Даты': return True
    # Years, dates, percentages and bare numeric fragments are facts, not topic nodes.
    if re.fullmatch(r'(?:19|20)\d{2}', v): return True
    if re.fullmatch(r'\d+(?:[.,]\d+)?\s*%?', v): return True
    if re.fullmatch(r'\d{1,2}[./-]\d{1,2}[./-]\d{2,4}', v): return True
    if len(v) < 2: return True
    return False


def _entity_relation_decisions() -> dict[str, str]:
    try:
        raw=DB.get_setting('entity_relation_decisions','{}')
        data=json.loads(raw or '{}')
        return data if isinstance(data,dict) else {}
    except Exception:
        return {}


def _entity_relation_key(a: str, b: str) -> str:
    left=(a or '').strip().lower(); right=(b or '').strip().lower()
    return '||'.join(sorted([left,right]))


def _entity_rows_for_topic(q: str, notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ql=q.lower().strip(); rows=[]
    for n in notes:
        text=f"{n.get('title','')}\n{n.get('content','')}"
        low=text.lower()
        if ql not in low: continue
        ents=extract_entities_local(text,notes)
        seen=set()
        for e in ents:
            v=(e.get('value') or '').strip()
            typ=entity_type_normalized(e.get('type',''),v)
            if len(v)<2 or v.lower()==ql or _entity_noise(v,typ): continue
            key=v.lower()
            if key in seen: continue
            seen.add(key)
            rows.append({'name':v,'type':typ,'note_id':n['id'],'note_title':n['title']})
    return rows


def _confidence_for_topic(q: str, notes: list[dict[str, Any]]) -> dict[str, Any]:
    ql=q.lower(); mentions=[]; sources=0; numeric=[]
    for n in notes:
        text=str(n.get('content') or '')
        if ql not in (str(n.get('title') or '')+' '+text).lower(): continue
        mentions.append(n)
        if SOURCE_RE.search(text): sources += 1
        numeric.extend(NUMBER_RE.findall(text))
    distinct_nums=len(set(x.lower() for x in numeric if x.strip()))
    score=min(100, 18 + len(mentions)*7 + sources*12)
    conflicts=max(0, distinct_nums-6)
    score=max(0, score-min(conflicts*4,24))
    level='Высокая' if score>=75 else 'Средняя' if score>=48 else 'Низкая'
    return {'score':score,'level':level,'reasons':[f'{len(mentions)} заметок с упоминанием',f'{sources} заметок с источниками'], 'possible_conflicts':conflicts}

@app.get('/api/smart/topic')
def smart_topic(q: str = Query(..., min_length=2)) -> dict[str, Any]:
    notes=DB.list_notes(); ql=q.lower().strip()
    topic_notes=[n for n in notes if ql in (str(n.get('title') or '')+' '+str(n.get('content') or '')).lower()]
    rows=_entity_rows_for_topic(q,notes)
    co={}
    for r in rows:
        k=(r['type'],r['name'].lower())
        item=co.setdefault(k,{'name':r['name'],'type':r['type'],'notes':set(),'count':0})
        item['notes'].add(r['note_id']); item['count']+=1
    related=[]
    for item in co.values():
        strength=min(100, len(item['notes'])*14 + item['count']*4)
        related.append({'name':item['name'],'type':item['type'],'notes':len(item['notes']),'mentions':item['count'],'strength':strength,'relation':'часто встречается вместе'})
    related.sort(key=lambda x:(-x['strength'],-x['notes'],x['name'].lower()))
    # simple automatic subtopics from repeated keywords
    kw={}
    for n in topic_notes:
        for term in extract_keywords(f"{n.get('title','')} {n.get('content','')}",14):
            tl=term.lower()
            if tl==ql or ql in tl or tl in ql or _entity_noise(term,'Понятия'): continue
            item=kw.setdefault(tl,{'name':term,'notes':set(),'mentions':0})
            item['notes'].add(n['id']); item['mentions']+=1
    subtopics=[{'name':v['name'],'notes':len(v['notes']),'mentions':v['mentions']} for v in kw.values() if len(v['notes'])>=2]
    subtopics.sort(key=lambda x:(-x['notes'],-x['mentions']))
    questions=[]; actions=[]; sources=[]
    for n in topic_notes:
        for line in str(n.get('content') or '').splitlines():
            t=line.strip()
            if not t: continue
            if '?' in t or re.search(r'\b(?:уточнить|проверить|неясно|вопрос)\b',t,re.I): questions.append({'note_id':n['id'],'note_title':n['title'],'text':t[:260]})
            if re.search(r'\b(?:TODO|дописать|сделать|подготовить|отправить|позвонить|написать)\b',t,re.I) or re.match(r'^- \[ \]',t): actions.append({'note_id':n['id'],'note_title':n['title'],'text':t[:260]})
        if SOURCE_RE.search(str(n.get('content') or '')): sources.append({'id':n['id'],'title':n['title'],'excerpt':note_excerpt(n,150)})
    # Bridges: entities that occur inside this topic and also connect to notes outside it.
    topic_ids={int(n['id']) for n in topic_notes}; bridges=[]
    for r in related[:30]:
        name_low=r['name'].lower(); outside=0
        for n in notes:
            if int(n['id']) in topic_ids: continue
            if name_low in (str(n.get('title') or '')+' '+str(n.get('content') or '')).lower(): outside += 1
        if outside:
            bridges.append({'name':r['name'],'type':r['type'],'inside_notes':r['notes'],'outside_notes':outside,'bridge_score':min(100,r['strength']+outside*5)})
    bridges.sort(key=lambda x:(-x['bridge_score'],-x['outside_notes']))
    mature=min(100, len(topic_notes)*4 + len(related[:15])*3 + len(sources)*5)
    gaps=[]
    if len(sources)<max(2,len(topic_notes)//4): gaps.append('Мало заметок с явными источниками')
    if questions: gaps.append(f'Открытых вопросов: {len(questions)}')
    if len(related)<4: gaps.append('Тема слабо связана с другими сущностями')
    return {'topic':q,'notes':len(topic_notes),'related':related[:30],'subtopics':subtopics[:18],'questions':questions[:20],'actions':actions[:20],'sources':sources[:20],'confidence':_confidence_for_topic(q,notes),'maturity':mature,'gaps':gaps,'bridges':bridges[:12]}


@app.get('/api/smart/entity-graph')
def entity_graph() -> dict[str, Any]:
    notes=DB.list_notes()
    raw=smart_entities()
    items=[x for x in raw.get('items',[]) if not _entity_noise(x.get('name',''),x.get('type','')) and x.get('type')!='Даты']
    # Keep the graph readable: the most meaningful entities only.
    items=items[:70]
    by_name={x['name'].strip().lower():x for x in items}
    nodes=[{'id':i,'name':x['name'],'title':x['name'],'type':x['type'],'importance':x['importance']} for i,x in enumerate(items)]
    id_by_name={n['name'].strip().lower():n['id'] for n in nodes}
    note_entities=[]
    for n in notes:
        found=[]; seen=set()
        for e in extract_entities_local(f"{n.get('title','')}\n{n.get('content','')}",notes):
            v=(e.get('value') or '').strip(); typ=entity_type_normalized(e.get('type',''),v); vl=v.lower()
            if vl in id_by_name and vl not in seen and not _entity_noise(v,typ):
                seen.add(vl); found.append(vl)
        note_entities.append((n,found))
    pair_notes=defaultdict(set)
    for n,ents in note_entities:
        capped=ents[:18]
        for i,a in enumerate(capped):
            for b in capped[i+1:]:
                if a!=b: pair_notes[_entity_relation_key(a,b)].add(int(n['id']))
    decisions=_entity_relation_decisions()
    links=[]; direct_pairs=set()
    # Explicit wiki links between notes whose titles are represented as entities => confirmed.
    title_map={n['title'].strip().lower():n for n in notes}
    for n in notes:
        a=n['title'].strip().lower()
        if a not in id_by_name: continue
        for target in WIKILINK_RE.findall(n.get('content') or ''):
            b=target.strip().lower()
            if b in id_by_name and b!=a:
                key=_entity_relation_key(a,b); direct_pairs.add(key)
                links.append({'source':id_by_name[a],'target':id_by_name[b],'kind':'confirmed','strength':100,'notes':1,'reason':'Подтверждено явной [[ссылкой]] между заметками','key':key})
    # Co-occurrence links; user decisions override automatic status.
    for key,nids in sorted(pair_notes.items(), key=lambda kv:len(kv[1]), reverse=True):
        if len(nids)<2: continue
        a,b=key.split('||',1)
        if a not in id_by_name or b not in id_by_name: continue
        status=decisions.get(key,'suggested')
        if status=='rejected': continue
        if key in direct_pairs: continue
        strength=min(96, 28+len(nids)*11)
        kind='confirmed' if status=='confirmed' else 'suggested'
        links.append({'source':id_by_name[a],'target':id_by_name[b],'kind':kind,'strength':strength,'notes':len(nids),'reason':f'Встречаются вместе в {len(nids)} заметках','key':key})
        direct_pairs.add(key)
        if len(links)>=135: break
    # Indirect second-level links are visual hints only and never actionable.
    adjacency=defaultdict(set)
    for l in links:
        if l['kind'] not in ('confirmed','suggested'): continue
        adjacency[l['source']].add(l['target']); adjacency[l['target']].add(l['source'])
    indirect=[]
    ids=list(adjacency.keys())
    for i,a in enumerate(ids):
        for b in ids[i+1:]:
            key_ids=(min(a,b),max(a,b))
            if any((min(l['source'],l['target']),max(l['source'],l['target']))==key_ids for l in links): continue
            common=adjacency[a] & adjacency[b]
            if len(common)>=2:
                indirect.append({'source':a,'target':b,'kind':'indirect','strength':min(55,20+len(common)*9),'notes':0,'reason':f'Косвенно связаны через {len(common)} общих сущности','key':''})
    indirect.sort(key=lambda x:-x['strength'])
    links.extend(indirect[:28])
    return {'kind':'entities','nodes':nodes,'links':links,'legend':{'confirmed':'подтверждено','suggested':'предполагается','indirect':'косвенная связь'}}


@app.post('/api/smart/entity-relation')
def entity_relation_decision(body: EntityRelationDecision) -> dict[str, Any]:
    status=(body.status or '').strip().lower()
    if status not in {'confirmed','rejected'}: raise HTTPException(400,'Неизвестный статус')
    key=_entity_relation_key(body.source,body.target)
    data=_entity_relation_decisions(); data[key]=status
    DB.set_setting('entity_relation_decisions',json.dumps(data,ensure_ascii=False))
    return {'ok':True,'key':key,'status':status}

# ZettelLocal 2.11 — local Auto-Capture ---------------------------------------
class AutoCaptureBody(BaseModel):
    text: str
    sensitivity: str = "normal"
    active_note_id: int | None = None


def _autocapture_category_id() -> int | None:
    for c in DB.list_categories():
        if (c.get("title") or "").strip().lower() == "auto-capture":
            return int(c["id"])
    try:
        c = DB.create_category("Auto-Capture", None)
        return int(c["id"])
    except Exception:
        return None


def _capture_kind(sentence: str) -> tuple[str, int]:
    low = sentence.lower()
    rules = [
        ("решение", 5, ("решили", "решено", "договорились", "оставляем вариант", "оставляем", "выбрали", "принято решение", "будем делать", "будем использовать", "используем")),
        ("инсайт", 5, ("важно", "ключевой", "получается", "суть в том", "проблема в том", "я понял", "я считаю", "считаю, что", "идея в том", "лучше", "вывод", "наблюдение")),
        ("гипотеза", 4, ("мне кажется", "возможно", "предполагаю", "вероятно", "гипотеза", "может быть связано")),
        ("задача", 4, ("нужно сделать", "надо сделать", "нужно", "надо", "следует", "проверить", "уточнить", "подготовить", "дописать", "отправить", "позвонить")),
        ("вопрос", 4, ("нужно понять", "надо понять", "неясно", "вопрос", "нужно выяснить", "?")),
        ("факт", 3, ("составляет", "равно", "показал", "выявлено", "согласно", "приказ", "doi", "pmid")),
    ]
    for kind, score, markers in rules:
        if any(m in low for m in markers):
            return kind, score
    # A concrete declarative sentence can still be useful even without explicit
    # marker words. This is important for natural conversation: people often state
    # an insight directly instead of saying "важно" or "я понял" first.
    entities = extract_entities_local(sentence, DB.list_notes())
    if len(sentence) >= 80 and len(entities) >= 1:
        return "наблюдение", 3
    if len(sentence) >= 55 and len(entities) >= 2:
        return "наблюдение", 3
    # In normal mode the length bonus lifts a substantial declarative thought to
    # the capture threshold; careful mode still ignores it unless stronger cues exist.
    if len(sentence) >= 85:
        return "наблюдение", 3
    return "", 0


def _capture_candidates(text: str, sensitivity: str) -> list[dict[str, Any]]:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) < 18:
        return []
    sentences = [x.strip(" \t\n-•") for x in re.split(r"(?<=[.!?])\s+|\n+", cleaned) if len(x.strip()) >= 18]
    threshold = {"careful": 5, "normal": 4, "active": 3}.get(sensitivity, 4)
    result = []
    for sentence in sentences[:16]:
        kind, score = _capture_kind(sentence)
        if not kind:
            continue
        if 45 <= len(sentence) <= 700:
            score += 1
        if re.search(r"\b\d+(?:[.,]\d+)?%?\b", sentence):
            score += 1
        if len(extract_entities_local(sentence, DB.list_notes())) >= 2:
            score += 1
        if score >= threshold:
            result.append({"kind": kind, "score": score, "text": sentence})
    # Keep the conversation invisible: at most three atomic captures from one turn.
    result.sort(key=lambda x: (-x["score"], -len(x["text"])))
    seen = set(); out = []
    for item in result:
        key = re.sub(r"\W+", " ", item["text"].lower()).strip()[:120]
        if key in seen:
            continue
        seen.add(key); out.append(item)
        if len(out) >= 3:
            break
    return out


def _capture_title(text: str, kind: str) -> str:
    keys = extract_keywords(text, 4)
    title = " · ".join(keys).strip()
    if not title:
        title = re.sub(r"^[\-–—\s]+", "", text).strip()[:82]
    title = title[:92].strip(" .,:;—-") or "Автоматически захваченное знание"
    prefixes = {"решение":"Решение", "вопрос":"Вопрос", "гипотеза":"Гипотеза", "задача":"Действие"}
    if kind in prefixes and not title.lower().startswith(prefixes[kind].lower()):
        title = f"{prefixes[kind]}: {title}"
    return title[:110]


@app.post('/api/smart/autocapture')
def auto_capture(body: AutoCaptureBody) -> dict[str, Any]:
    if not (body.text or '').strip():
        return {'captured': [], 'skipped': [], 'count': 0}
    notes = DB.list_notes()
    candidates = _capture_candidates(body.text, body.sensitivity)
    created, skipped = [], []
    category_id = _autocapture_category_id()
    for item in candidates:
        sentence = item['text']
        # Duplicate guard: compare against existing titles and content.
        best = None; best_score = 0.0
        for n in notes:
            sc = lexical_similarity(sentence, f"{n.get('title','')} {n.get('content','')}")
            if sc > best_score:
                best_score, best = sc, n
        if best and best_score >= 0.66:
            skipped.append({'reason':'Похожее знание уже есть', 'note_id':best['id'], 'title':best['title'], 'score':round(best_score,2)})
            continue
        title = _capture_title(sentence, item['kind'])
        keywords = [k for k in extract_keywords(sentence, 7) if len(k) > 2]
        tags = list(dict.fromkeys(['auto-capture', item['kind']] + [re.sub(r'\s+','-',k.lower()) for k in keywords[:5]]))[:7]
        temp = {'id': -1, 'title': title, 'content': sentence, 'tags': tags}
        links = suggest_links(temp, notes, MODEL, 4)
        link_text = ' · '.join(f"[[{x['title']}]]" for x in links[:3])
        entities = [e.get('value','') for e in extract_entities_local(sentence, notes) if e.get('value')][:8]
        content = sentence
        if link_text:
            content += f"\n\nСвязи: {link_text}"
        if entities:
            content += "\n\nСущности: " + " · ".join(entities)
        content += f"\n\n---\nAuto-Capture · тип: {item['kind']} · локальный анализ разговора"
        note = DB.create_note(title, content, tags, category_id)
        notes.append(note)
        created.append({'id':note['id'],'title':note['title'],'kind':item['kind'],'score':item['score'],'links':[x['title'] for x in links[:3]],'tags':tags})
    history = []
    try:
        history = json.loads(DB.get_setting('autocapture_history','[]') or '[]')
    except Exception:
        history = []
    for x in created:
        history.insert(0, {'date': DB.now(), **x})
    history = history[:100]
    DB.set_setting('autocapture_history', json.dumps(history, ensure_ascii=False))
    return {'captured': created, 'skipped': skipped, 'count': len(created), 'history_count': len(history)}


@app.get('/api/smart/autocapture/history')
def auto_capture_history() -> dict[str, Any]:
    try:
        history = json.loads(DB.get_setting('autocapture_history','[]') or '[]')
    except Exception:
        history = []
    return {'items': history[:100], 'count': len(history)}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store, max-age=0"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

