from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from .ai_engine import STOPWORDS, extract_keywords, tokenize

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]")
SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
NEGATIONS = {"не", "нет", "никогда", "нельзя", "отсутствует", "без", "not", "no", "never"}
SOURCE_RE = re.compile(r"https?://|doi\s*:|\bdoi\.org\b|источник|литератур|references|библиограф", re.I)
NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:%|лет|года|год|месяц|дней|дБ|гц|Гц|человек|участник\w*)?", re.I)


def clean_text(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    text = re.sub(r"[#>*_`\[\]]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def meaningful_sentences(text: str, min_len: int = 35) -> list[str]:
    return [clean_text(x) for x in SENTENCE_RE.split(text or "") if len(clean_text(x)) >= min_len]


def lexical_similarity(a: str, b: str) -> float:
    at = {x for x in tokenize(a) if x not in STOPWORDS and len(x) > 2}
    bt = {x for x in tokenize(b) if x not in STOPWORDS and len(x) > 2}
    if not at or not bt:
        return 0.0
    jaccard = len(at & bt) / max(len(at | bt), 1)
    sequence = SequenceMatcher(None, clean_text(a).lower()[:1500], clean_text(b).lower()[:1500]).ratio()
    return jaccard * 0.78 + sequence * 0.22


def note_quality(note: dict[str, Any]) -> dict[str, Any]:
    title = (note.get("title") or "").strip()
    content = note.get("content") or ""
    words = tokenize(content)
    links = WIKILINK_RE.findall(content)
    tags = note.get("tags") or []
    checks: list[dict[str, Any]] = []
    score = 100

    def add(ok: bool, penalty: int, label: str, advice: str) -> None:
        nonlocal score
        if not ok:
            score -= penalty
        checks.append({"ok": ok, "label": label, "advice": "" if ok else advice})

    add(len(title) >= 5 and title.lower() not in {"новая заметка", "без названия"}, 12, "Понятный заголовок", "Сформулируйте заголовок как конкретную идею или вопрос.")
    add(len(words) >= 35, 18, "Достаточно содержания", "Добавьте контекст, вывод или практическое значение.")
    add(len(words) <= 900, 8, "Атомарность", "Заметка длинная: рассмотрите разбиение на несколько идей.")
    add(bool(links), 12, "Связи", "Добавьте хотя бы одну [[связанную заметку]].")
    add(bool(tags), 8, "Теги", "Добавьте 1–3 точных тега.")
    add(bool(SOURCE_RE.search(content)), 14, "Источник", "Добавьте DOI, ссылку или библиографическое описание, если это фактическое утверждение.")
    add(any(x in content.lower() for x in ("вывод", "итог", "значение", "поэтому", "мой комментарий")), 8, "Собственный вывод", "Запишите, что эта информация означает именно для вашей работы.")
    add("?" in content or any(x in content.lower() for x in ("проверить", "неясно", "вопрос")), 5, "Открытый вопрос", "Добавьте вопрос для дальнейшего исследования.")
    return {"score": max(score, 0), "checks": checks, "words": len(words), "links": len(links), "tags": len(tags)}


def reflective_questions(note: dict[str, Any]) -> list[str]:
    content = (note.get("content") or "").lower()
    questions: list[str] = []
    if not SOURCE_RE.search(content):
        questions.append("На каком источнике основаны ключевые утверждения?")
    if "однако" not in content and "огранич" not in content and "исключ" not in content:
        questions.append("Какие ограничения или исключения есть у этой идеи?")
    if not WIKILINK_RE.search(content):
        questions.append("С какими уже существующими заметками это связано?")
    if not any(x in content for x in ("практи", "примен", "использ")):
        questions.append("Как это можно применить на практике?")
    if not any(x in content for x in ("провер", "измер", "эксперимент", "данн")):
        questions.append("Как можно проверить эту мысль на данных?")
    questions.append("Что изменится, если ключевое предположение окажется неверным?")
    return questions[:5]


def article_analysis(note: dict[str, Any]) -> dict[str, Any]:
    text = note.get("content") or ""
    sentences = meaningful_sentences(text, 28)

    def select(markers: tuple[str, ...], fallback: str = "Не найдено автоматически") -> str:
        for sentence in sentences:
            low = sentence.lower()
            if any(marker in low for marker in markers):
                return sentence[:700]
        return fallback

    sample_candidates = [s for s in sentences if NUMBER_RE.search(s) and any(x in s.lower() for x in ("участ", "пациент", "ребен", "детей", "взросл", "выборк", "n="))]
    return {
        "purpose": select(("цель", "задачей исследования", "aim", "objective", "исследовали")),
        "methods": select(("метод", "дизайн", "оценивали", "измеряли", "применяли", "method")),
        "sample": sample_candidates[0][:700] if sample_candidates else "Не найдено автоматически",
        "results": select(("результат", "показал", "выявлен", "обнаружен", "result", "достовер")),
        "limitations": select(("огранич", "недостат", "limitation", "следует учитывать")),
        "practice": select(("практичес", "клиничес", "может использовать", "рекоменду", "значение")),
        "keywords": extract_keywords(f"{note.get('title', '')} {text}", 12),
    }


def atomize_note(note: dict[str, Any], limit: int = 8) -> list[dict[str, str]]:
    content = note.get("content") or ""
    blocks = [x.strip() for x in re.split(r"\n(?=#{1,3}\s)|\n{2,}", content) if len(clean_text(x)) >= 45]
    if len(blocks) <= 1:
        blocks = meaningful_sentences(content, 55)
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for block in blocks:
        heading = re.match(r"^#{1,3}\s+(.+)$", block.splitlines()[0]) if block.splitlines() else None
        if heading:
            title = clean_text(heading.group(1))[:90]
        else:
            keys = extract_keywords(block, 3)
            title = " · ".join(keys).capitalize() if keys else clean_text(block)[:70]
        title = title or "Новая атомарная заметка"
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append({"title": title, "content": block})
        if len(result) >= limit:
            break
    return result


def suggest_links(note: dict[str, Any], notes: list[dict[str, Any]], model: Any, limit: int = 8) -> list[dict[str, Any]]:
    current_links = {x.strip().lower() for x in WIKILINK_RE.findall(note.get("content") or "")}
    semantic = {int(x["id"]): float(x.get("score") or 0) for x in model.related(note, notes, 30)} if model.ready else {}
    scored: list[tuple[float, dict[str, Any]]] = []
    source_text = f"{note.get('title', '')} {note.get('content', '')}"
    for other in notes:
        if other.get("id") == note.get("id") or other.get("title", "").strip().lower() in current_links:
            continue
        lexical = lexical_similarity(source_text, f"{other.get('title', '')} {other.get('content', '')}")
        score = semantic.get(int(other["id"]), 0.0) * 0.72 + lexical * 0.28 if semantic else lexical
        if score >= 0.08:
            scored.append((score, other))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "id": other["id"],
            "title": other["title"],
            "score": round(score, 3),
            "reason": "совпадают понятия и контекст" if score > 0.25 else "есть общие термины",
        }
        for score, other in scored[:limit]
    ]


def detect_contradictions(note: dict[str, Any], notes: list[dict[str, Any]], model: Any, limit: int = 6) -> list[dict[str, Any]]:
    source_sentences = meaningful_sentences(note.get("content") or "")[:80]
    candidates = model.related(note, notes, 12) if model.ready else sorted(
        (x for x in notes if x.get("id") != note.get("id")),
        key=lambda x: lexical_similarity(note.get("content") or "", x.get("content") or ""),
        reverse=True,
    )[:12]
    results: list[tuple[float, dict[str, Any]]] = []
    for other in candidates:
        for left in source_sentences:
            lt = set(tokenize(left)) - STOPWORDS
            if len(lt) < 4:
                continue
            left_neg = bool(set(tokenize(left)) & NEGATIONS)
            left_numbers = set(NUMBER_RE.findall(left))
            for right in meaningful_sentences(other.get("content") or "")[:80]:
                rt = set(tokenize(right)) - STOPWORDS
                overlap = len(lt & rt) / max(min(len(lt), len(rt)), 1)
                if overlap < 0.45:
                    continue
                right_neg = bool(set(tokenize(right)) & NEGATIONS)
                right_numbers = set(NUMBER_RE.findall(right))
                neg_conflict = left_neg != right_neg
                number_conflict = bool(left_numbers and right_numbers and left_numbers != right_numbers)
                if neg_conflict or number_conflict:
                    confidence = overlap + (0.18 if neg_conflict else 0) + (0.12 if number_conflict else 0)
                    results.append((confidence, {
                        "note_id": other["id"],
                        "note_title": other["title"],
                        "left": left[:420],
                        "right": right[:420],
                        "reason": "разная формулировка отрицания" if neg_conflict else "различаются числовые значения",
                        "confidence": round(min(confidence, 0.99), 2),
                    }))
    results.sort(key=lambda x: x[0], reverse=True)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for _, item in results:
        key = (int(item["note_id"]), item["reason"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def knowledge_gaps(notes: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    mentions: Counter[str] = Counter()
    note_sets: defaultdict[str, set[int]] = defaultdict(set)
    titles = {clean_text(n.get("title", "")).lower() for n in notes}
    for note in notes:
        words = set(extract_keywords(f"{note.get('title', '')} {note.get('content', '')}", 20))
        for word in words:
            mentions[word] += 1
            note_sets[word].add(int(note["id"]))
    gaps = []
    for word, count in mentions.most_common():
        if count < 3 or word.lower() in titles or any(word.lower() == t.split(" — ")[0] for t in titles):
            continue
        gaps.append({"term": word, "mentions": count, "note_ids": sorted(note_sets[word])[:12]})
        if len(gaps) >= limit:
            break
    return gaps


def duplicate_groups(notes: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    results: list[tuple[float, dict[str, Any]]] = []
    capped = notes[:350]
    for i, left in enumerate(capped):
        for right in capped[i + 1:]:
            title_sim = SequenceMatcher(None, left.get("title", "").lower(), right.get("title", "").lower()).ratio()
            if title_sim < 0.62:
                continue
            content_sim = lexical_similarity(left.get("content", ""), right.get("content", ""))
            score = title_sim * 0.55 + content_sim * 0.45
            if score >= 0.62:
                results.append((score, {"left": {"id": left["id"], "title": left["title"]}, "right": {"id": right["id"], "title": right["title"]}, "score": round(score, 2)}))
    results.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in results[:limit]]


def dashboard(notes: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    title_map = {n.get("title", "").strip().lower(): n for n in notes}
    outgoing_count = 0
    linked_ids: set[int] = set()
    missing_targets: Counter[str] = Counter()
    degree: Counter[int] = Counter()
    incoming: Counter[int] = Counter()
    outgoing: Counter[int] = Counter()

    for note in notes:
        note_id = int(note["id"])
        links = [x.strip() for x in WIKILINK_RE.findall(note.get("content") or "") if x.strip()]
        outgoing_count += len(links)
        outgoing[note_id] += len(links)
        if links:
            linked_ids.add(note_id)
        for title in links:
            target = title_map.get(title.lower())
            if target:
                target_id = int(target["id"])
                linked_ids.add(target_id)
                degree[note_id] += 1
                degree[target_id] += 1
                incoming[target_id] += 1
            else:
                missing_targets[title] += 1

    orphan = [n for n in notes if int(n["id"]) not in linked_ids]
    without_sources = [n for n in notes if len(tokenize(n.get("content") or "")) > 80 and not SOURCE_RE.search(n.get("content") or "")]
    stale = []
    fresh_30 = 0
    for note in notes:
        try:
            updated = datetime.fromisoformat(str(note.get("updated_at", "")).replace("Z", "+00:00"))
            age = (now - updated).days
            if age >= 90:
                stale.append(note)
            if age <= 30:
                fresh_30 += 1
        except Exception:
            pass

    recent = sorted(notes, key=lambda n: n.get("updated_at", ""), reverse=True)[:8]
    weak = [n for n in notes if 0 < len(tokenize(n.get("content") or "")) < 55]
    emptyish = [n for n in notes if len(tokenize(n.get("content") or "")) == 0]
    untagged = [n for n in notes if not (n.get("tags") or [])]
    uncategorized = [n for n in notes if not n.get("category_id")]

    hubs = sorted(notes, key=lambda n: (degree[int(n["id"])], incoming[int(n["id"])]), reverse=True)
    hubs = [n for n in hubs if degree[int(n["id"])] > 0][:10]

    total = max(1, len(notes))
    connected_ratio = 1 - len(orphan) / total
    tagged_ratio = 1 - len(untagged) / total
    categorized_ratio = 1 - len(uncategorized) / total
    developed_ratio = 1 - (len(weak) + len(emptyish)) / total
    missing_penalty = min(1.0, sum(missing_targets.values()) / max(1, outgoing_count)) if outgoing_count else 0.0
    freshness_ratio = fresh_30 / total
    health = round(max(0, min(100,
        connected_ratio * 35 +
        tagged_ratio * 15 +
        categorized_ratio * 15 +
        developed_ratio * 20 +
        freshness_ratio * 10 +
        (1 - missing_penalty) * 5
    )))

    recommendations: list[dict[str, Any]] = []
    if orphan:
        recommendations.append({"kind": "connect", "priority": "high", "title": f"Связать {len(orphan)} изолированных заметок", "detail": "Добавьте хотя бы одну осмысленную [[ссылку]] из или на эти заметки.", "note_id": orphan[0]["id"]})
    if missing_targets:
        top_missing, count = missing_targets.most_common(1)[0]
        recommendations.append({"kind": "missing", "priority": "high", "title": f"Создать узел «{top_missing}»", "detail": f"На него уже ссылаются {count} раз(а), но заметки ещё нет.", "term": top_missing})
    if weak:
        recommendations.append({"kind": "develop", "priority": "medium", "title": f"Развить {len(weak)} коротких заметок", "detail": "Добавьте определение, аргументы, источник или связанные идеи.", "note_id": weak[0]["id"]})
    if untagged:
        recommendations.append({"kind": "tag", "priority": "medium", "title": f"Разметить {len(untagged)} заметок без тегов", "detail": "Теги улучшают поиск и помогают видеть повторяющиеся темы.", "note_id": untagged[0]["id"]})
    if uncategorized:
        recommendations.append({"kind": "category", "priority": "low", "title": f"Разложить {len(uncategorized)} заметок по папкам", "detail": "Структура каталога станет понятнее, особенно при росте базы.", "note_id": uncategorized[0]["id"]})
    if stale:
        recommendations.append({"kind": "stale", "priority": "low", "title": f"Проверить {len(stale)} давно не обновлявшихся заметок", "detail": "Часть из них можно уточнить, объединить или архивировать.", "note_id": stale[0]["id"]})

    return {
        "stats": {
            "notes": len(notes),
            "words": sum(len(tokenize(n.get("content") or "")) for n in notes),
            "links": outgoing_count,
            "categories": len({n.get("category_id") for n in notes if n.get("category_id")}),
            "health": health,
            "connected_pct": round(connected_ratio * 100),
            "fresh_30": fresh_30,
        },
        "orphans": [{"id": n["id"], "title": n["title"]} for n in orphan[:12]],
        "without_sources": [{"id": n["id"], "title": n["title"]} for n in without_sources[:12]],
        "stale": [{"id": n["id"], "title": n["title"], "updated_at": n.get("updated_at")} for n in stale[:12]],
        "missing_links": [{"title": title, "mentions": count} for title, count in missing_targets.most_common(10)],
        "gaps": knowledge_gaps(notes, 10),
        "duplicates": duplicate_groups(notes, 6),
        "recent": [{"id": n["id"], "title": n["title"], "updated_at": n.get("updated_at")} for n in recent],
        "hubs": [{"id": n["id"], "title": n["title"], "degree": degree[int(n["id"])], "incoming": incoming[int(n["id"])], "outgoing": outgoing[int(n["id"])]} for n in hubs],
        "weak": [{"id": n["id"], "title": n["title"], "words": len(tokenize(n.get("content") or ""))} for n in weak[:12]],
        "untagged": [{"id": n["id"], "title": n["title"]} for n in untagged[:12]],
        "uncategorized": [{"id": n["id"], "title": n["title"]} for n in uncategorized[:12]],
        "recommendations": recommendations[:6],
    }

# ZettelLocal 2.6 — lightweight local intelligence (no external API)
TODO_RE = re.compile(r"\b(?:todo|fixme|дописать|уточнить|проверить|разобраться|не забыть|вопрос)\b|\?{2,}", re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
PMID_RE = re.compile(r"\bPMID\s*:?\s*(\d{5,9})\b", re.I)
DATE_RE = re.compile(r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|(?:19|20)\d{2})\b")
PERCENT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*%")
URL_RE = re.compile(r"https?://[^\s)\]>]+", re.I)


def unfinished_notes(notes: list[dict[str, Any]], limit: int = 16) -> list[dict[str, Any]]:
    out = []
    for n in notes:
        text = n.get('content') or ''
        issues = []
        if TODO_RE.search(text): issues.append('есть TODO / вопрос')
        if len(tokenize(text)) < 24: issues.append('очень короткая')
        missing = [x for x in WIKILINK_RE.findall(text) if x.strip()]
        if text.rstrip().endswith(('-', ':', '—')): issues.append('мысль обрывается')
        if issues:
            out.append({'id': n['id'], 'title': n['title'], 'issues': issues, 'words': len(tokenize(text)), 'links': len(missing)})
    return out[:limit]


def auto_topics(notes: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    # A transparent local topic detector: recurring keywords + note membership.
    membership: defaultdict[str, list[int]] = defaultdict(list)
    for n in notes:
        kws = extract_keywords(f"{n.get('title','')} {n.get('content','')}", 10)
        for kw in set(kws):
            if len(kw) >= 4:
                membership[kw.lower()].append(int(n['id']))
    ranked = sorted(membership.items(), key=lambda x: (-len(x[1]), x[0]))
    topics = []
    used: list[set[int]] = []
    for term, ids in ranked:
        if len(ids) < 2: continue
        idset = set(ids)
        if any(len(idset & prev) / max(1, min(len(idset), len(prev))) > .82 for prev in used):
            continue
        used.append(idset)
        topics.append({'term': term, 'count': len(ids), 'note_ids': ids[:30]})
        if len(topics) >= limit: break
    return topics


def fact_index(notes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {'dates': [], 'percentages': [], 'doi': [], 'pmid': [], 'urls': []}
    seen = {k: set() for k in groups}
    patterns = {'dates': DATE_RE, 'percentages': PERCENT_RE, 'doi': DOI_RE, 'pmid': PMID_RE, 'urls': URL_RE}
    for n in notes:
        text = n.get('content') or ''
        for kind, pattern in patterns.items():
            for m in pattern.findall(text):
                value = m if isinstance(m, str) else str(m)
                key = (str(value).lower(), int(n['id']))
                if key in seen[kind]: continue
                seen[kind].add(key)
                groups[kind].append({'value': value, 'note_id': n['id'], 'title': n['title']})
                if len(groups[kind]) >= 40: break
    return groups


def interest_timeline(notes: list[dict[str, Any]], months: int = 8) -> list[dict[str, Any]]:
    buckets: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for n in notes:
        stamp = str(n.get('created_at') or n.get('updated_at') or '')[:7]
        if len(stamp) != 7: continue
        for kw in extract_keywords(f"{n.get('title','')} {n.get('content','')}", 7):
            if len(kw) >= 4: buckets[stamp][kw.lower()] += 1
    result = []
    for month in sorted(buckets.keys(), reverse=True)[:months]:
        result.append({'month': month, 'topics': [{'term': k, 'count': v} for k, v in buckets[month].most_common(5)]})
    return result


def note_statuses(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    title_map = {n.get('title','').strip().lower(): n for n in notes}
    incoming = Counter()
    for n in notes:
        for title in WIKILINK_RE.findall(n.get('content') or ''):
            target = title_map.get(title.strip().lower())
            if target: incoming[int(target['id'])] += 1
    result=[]
    for n in notes:
        words=len(tokenize(n.get('content') or '')); out=len(WIKILINK_RE.findall(n.get('content') or '')); inc=incoming[int(n['id'])]
        if inc >= 5 or (inc + out >= 8 and words >= 80): status='Опорная'
        elif words >= 55 and (inc + out) >= 2: status='Связанная'
        elif words >= 25: status='Развивается'
        else: status='Черновик'
        result.append({'id':n['id'],'title':n['title'],'status':status,'incoming':inc,'outgoing':out,'words':words})
    return result


def rediscover_note(notes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not notes: return None
    now = datetime.now(timezone.utc)
    recent = sorted(notes, key=lambda n: n.get('updated_at',''), reverse=True)[:5]
    recent_text = ' '.join(f"{n.get('title','')} {n.get('content','')[:700]}" for n in recent)
    candidates=[]
    recent_ids={int(n['id']) for n in recent}
    for n in notes:
        if int(n['id']) in recent_ids: continue
        try:
            updated=datetime.fromisoformat(str(n.get('updated_at','')).replace('Z','+00:00')); age=max(0,(now-updated).days)
        except Exception: age=0
        if age < 30: continue
        sim=lexical_similarity(recent_text, f"{n.get('title','')} {n.get('content','')}")
        score=sim * .75 + min(age/365,1) * .25
        candidates.append((score,n,age,sim))
    if not candidates: return None
    score,n,age,sim=max(candidates,key=lambda x:x[0])
    return {'id':n['id'],'title':n['title'],'age_days':age,'relevance':round(sim,2),'reason':'старая заметка, связанная с недавними темами' if sim>.05 else 'давно не открывалась'}


def possible_fact_conflicts(notes: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    # Conservative heuristic: similar sentences with different percentages/numbers.
    claims=[]
    for n in notes[:250]:
        for s in meaningful_sentences(n.get('content') or '', 28)[:50]:
            nums=NUMBER_RE.findall(s)
            if nums: claims.append((n,s,nums))
    out=[]
    for i,(a,sa,na) in enumerate(claims[:700]):
        for b,sb,nb in claims[i+1:i+80]:
            if a['id']==b['id'] or set(na)==set(nb): continue
            sim=lexical_similarity(sa,sb)
            if sim >= .23:
                out.append({'left_id':a['id'],'left_title':a['title'],'right_id':b['id'],'right_title':b['title'],'left':sa[:220],'right':sb[:220],'score':round(sim,2)})
                if len(out)>=limit: return out
    return out
