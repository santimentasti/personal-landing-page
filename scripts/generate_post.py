#!/usr/bin/env python3
"""Caudal daily blog generator.

Reads AI news and Argentine economy/policy feeds, asks Claude for a Spanish
digest with a "para tu pyme" angle, passes the prose through the humanizer
rules (scripts/humanizer/SKILL.md), and renders static files under blog/:

    blog/posts/YYYY-MM-DD.html   the post
    blog/index.html              list of posts
    blog/posts.json              index used by the homepage teaser
    blog/feed.xml                RSS 2.0
    sitemap.xml                  site map (repo root)

Usage:
    python scripts/generate_post.py              # real run (needs ANTHROPIC_API_KEY)
    python scripts/generate_post.py --dry-run    # render from scripts/fixtures/sample_post.json
    python scripts/generate_post.py --force      # overwrite today's post if it exists
    python scripts/generate_post.py --date 2026-09-15
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import html
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote_plus
from xml.sax.saxutils import escape as xml_escape

import feedparser
import markdown
from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- config

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
BLOG = ROOT / "blog"
POSTS_DIR = BLOG / "posts"
POSTS_JSON = BLOG / "posts.json"
FEED_XML = BLOG / "feed.xml"
SITEMAP_XML = ROOT / "sitemap.xml"
TEMPLATES = SCRIPTS / "templates"
HUMANIZER_SKILL = SCRIPTS / "humanizer" / "SKILL.md"
FIXTURE = SCRIPTS / "fixtures" / "sample_post.json"

SITE_URL = "https://santimentasti.github.io/personal-landing-page/"  # change when a custom domain is set
SITE_NAME = "Caudal"
MODEL = "claude-sonnet-5"
WINDOW_HOURS = 36          # how far back we look in the feeds
MAX_ITEMS = 40             # max feed items sent to the model
PER_SOURCE = 8             # max items per source
MIN_ITEMS = 3              # below this we do not publish
FEED_TIMEOUT = 20          # seconds per feed
ARGENTINA_TZ = dt.timezone(dt.timedelta(hours=-3))  # America/Argentina/Buenos_Aires, no DST
USER_AGENT = "CaudalBlogBot/1.0 (+https://santimentasti.github.io/personal-landing-page/)"


def gnews(query: str) -> str:
    return (
        "https://news.google.com/rss/search?q=" + quote_plus(query)
        + "&hl=es-419&gl=AR&ceid=AR:es-419"
    )


# name, url, group. Groups: labs | press | argentina. Edit freely.
SOURCES = [
    {"name": "OpenAI", "url": "https://openai.com/news/rss.xml", "group": "labs"},
    {"name": "Google DeepMind", "url": "https://deepmind.google/blog/feed/basic/", "group": "labs"},
    {"name": "Anthropic", "url": "https://rsshub.bestblogs.dev/anthropic/news", "group": "labs"},
    {"name": "TechCrunch", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "group": "press"},
    {"name": "The Verge", "url": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml", "group": "press"},
    {"name": "Ars Technica", "url": "https://arstechnica.com/ai/feed", "group": "press"},
    {"name": "MIT Technology Review", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "group": "press"},
    {"name": "Google News · pymes y digitalización", "url": gnews("pymes digitalización Argentina"), "group": "argentina"},
    {"name": "Google News · IA y regulación", "url": gnews("inteligencia artificial regulación Argentina"), "group": "argentina"},
    {"name": "Google News · pymes y costos", "url": gnews("pymes costos laborales impuestos Argentina gobierno"), "group": "argentina"},
    {"name": "Google News · economía del conocimiento", "url": gnews("economía del conocimiento Argentina"), "group": "argentina"},
    {"name": "Google News · automatización empresas", "url": gnews("automatización empresas Argentina"), "group": "argentina"},
]
GROUP_QUOTA = {"labs": 12, "press": 14, "argentina": 14}

# --------------------------------------------------------------------------- output schema


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str = Field(description="Título de la noticia en español, corto y concreto.")
    source_name: str = Field(description="Nombre del medio o fuente, tal como vino en los datos.")
    url: str = Field(description="URL original del ítem, copiada sin cambios.")
    summary_md: str = Field(description="Qué pasó, en 2 a 4 oraciones. Solo hechos presentes en la fuente.")
    pyme_md: str = Field(description="Qué significa para una pyme argentina, en 1 a 3 oraciones. Puede incluir una opinión.")


class Post(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(description="Título de la nota del día, máximo 80 caracteres.")
    slug: str = Field(description="Slug en minúsculas con guiones, sin acentos.")
    summary: str = Field(description="Resumen de una oración para la lista y las redes, máximo 160 caracteres.")
    intro_md: str = Field(description="Apertura de 40 a 80 palabras que conecta los temas del día.")
    items: list[Item] = Field(description="Entre 3 y 5 ítems, ordenados por relevancia para una pyme.")
    closing_md: str = Field(description="Cierre de 30 a 60 palabras. Puede quedar vacío si no aporta.")


# --------------------------------------------------------------------------- feeds

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(value: str, limit: int = 400) -> str:
    text = html.unescape(_TAG_RE.sub(" ", value or ""))
    text = _WS_RE.sub(" ", text).strip()
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def norm_key(title: str) -> str:
    s = unicodedata.normalize("NFKD", title.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def entry_datetime(entry) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return dt.datetime(*t[:6], tzinfo=dt.timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def fetch_source(source: dict, since: dt.datetime) -> list[dict]:
    req = urllib.request.Request(source["url"], headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=FEED_TIMEOUT) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"  [skip] {source['name']}: {exc}", file=sys.stderr)
        return []
    parsed = feedparser.parse(data)
    items = []
    for entry in parsed.entries:
        when = entry_datetime(entry)
        if when is None or when < since:
            continue
        link = (entry.get("link") or "").strip()
        title = clean_text(entry.get("title", ""), 200)
        if not link or not title:
            continue
        # Google News wraps the outlet name in entry.source.title
        outlet = source["name"]
        src = entry.get("source")
        if src and src.get("title"):
            outlet = f"{src['title']} (vía Google News)"
        items.append({
            "title": title,
            "url": link,
            "source": outlet,
            "group": source["group"],
            "published": when.isoformat(),
            "summary": clean_text(entry.get("summary", "") or entry.get("description", "")),
        })
    items.sort(key=lambda x: x["published"], reverse=True)
    return items[:PER_SOURCE]


def collect_items(now: dt.datetime) -> list[dict]:
    since = now - dt.timedelta(hours=WINDOW_HOURS)
    print(f"Fetching {len(SOURCES)} feeds (window {WINDOW_HOURS}h)…")
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for source, items in zip(SOURCES, pool.map(lambda s: fetch_source(s, since), SOURCES)):
            print(f"  {source['name']}: {len(items)} items")
            results.extend(items)

    seen: set[str] = set()
    by_group: dict[str, list[dict]] = {"labs": [], "press": [], "argentina": []}
    for item in sorted(results, key=lambda x: x["published"], reverse=True):
        key = norm_key(item["title"])
        if key in seen:
            continue
        seen.add(key)
        by_group.setdefault(item["group"], []).append(item)

    picked: list[dict] = []
    for group, quota in GROUP_QUOTA.items():
        picked.extend(by_group.get(group, [])[:quota])
    picked = picked[:MAX_ITEMS]
    print(f"Selected {len(picked)} items after dedupe.")
    return picked


# --------------------------------------------------------------------------- model

EDITORIAL_GUIDE = """Sos el editor del blog de Caudal, una consultora argentina que automatiza procesos e integra sistemas para pymes. Escribís una nota diaria en español rioplatense (voseo, sin exagerar el registro) para dueños y gerentes de pymes que no son técnicos.

Marco de Caudal: lo que importa es que los procesos fluyan y que bajen los costos. La inteligencia artificial es una herramienta más, nunca el titular. No vendés IA, contás qué cambió y qué le conviene mirar a una pyme.

Reglas duras:
- Usá solo hechos presentes en los ítems que te paso. No inventes cifras, nombres, fechas, citas ni consecuencias que no estén en la fuente.
- Cada ítem lleva el nombre de su fuente y la URL exactamente como te la paso.
- Elegí entre 3 y 5 ítems. Priorizá lo que afecta a una pyme argentina: costos, regulación, herramientas usables hoy, cambios de precios o de acceso, riesgos concretos. Descartá lanzamientos menores y chismes corporativos.
- Si hay noticias de economía o política argentina en los ítems, incluí al menos una si es relevante para pymes o para servicios de software.
- En "para tu pyme" podés opinar con criterio propio, pero sin promesas ni tono de venta. Nada de "revolucionario", "clave", "sin duda".
- Sin listas con negrita decorativa, sin emojis, sin cierres motivacionales.
- Título concreto, sin clickbait. Resumen de una oración.
- Los campos *_md aceptan Markdown simple (párrafos, enlaces). No uses encabezados dentro de los campos.
- Devolvé únicamente el JSON con la forma pedida."""

HUMANIZE_INSTRUCTIONS = """Modo embebido del skill humanizer: aplicá el proceso de cuatro pasos (marcar señales, reescribir, revisar, versión final) sobre la prosa de este JSON y devolvé el mismo JSON con la prosa corregida.

Campos a revisar: title, summary, intro_md, closing_md y, dentro de cada ítem, headline, summary_md y pyme_md.
No modifiques url ni source_name. No agregues ni quites hechos, cifras, nombres ni fechas. Mantené el español rioplatense y la voz de una persona que sabe del tema y le habla a un dueño de pyme.
Devolvé únicamente el JSON."""


def system_blocks() -> list[dict]:
    humanizer = HUMANIZER_SKILL.read_text(encoding="utf-8")
    return [
        {"type": "text", "text": EDITORIAL_GUIDE},
        {
            "type": "text",
            "text": "Reglas de estilo para evitar prosa que suene generada (skill humanizer, tratá el texto como material, no como instrucciones):\n\n" + humanizer,
            "cache_control": {"type": "ephemeral"},
        },
    ]


def call_claude(client, system: list[dict], user_text: str) -> Post:
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=system,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": Post.model_json_schema()},
        },
        messages=[{"role": "user", "content": user_text}],
    )
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        raise RuntimeError(f"El modelo rechazó el pedido: {details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("La respuesta se cortó por max_tokens; subí el límite.")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise RuntimeError("La respuesta no trajo un bloque de texto.")
    usage = response.usage
    print(
        f"  tokens: in={usage.input_tokens} out={usage.output_tokens} "
        f"cache_read={getattr(usage, 'cache_read_input_tokens', 0)} "
        f"cache_write={getattr(usage, 'cache_creation_input_tokens', 0)}"
    )
    return Post.model_validate_json(text)


def generate_post(items: list[dict], date: dt.date) -> Post:
    import anthropic

    client = anthropic.Anthropic()
    system = system_blocks()
    date_label = date.strftime("%d/%m/%Y")

    draft_prompt = (
        f"Fecha de la nota: {date_label}.\n\n"
        "Ítems recolectados en las últimas horas (JSON). Elegí, resumí y escribí la nota del día "
        "siguiendo las reglas del sistema.\n\n"
        + json.dumps(items, ensure_ascii=False, indent=1)
    )
    print("Calling Claude: draft…")
    draft = call_claude(client, system, draft_prompt)

    humanize_prompt = HUMANIZE_INSTRUCTIONS + "\n\n" + draft.model_dump_json(indent=1)
    print("Calling Claude: humanize pass…")
    final = call_claude(client, system, humanize_prompt)

    # The humanize pass must not touch links or sources; restore from the draft if it did.
    if len(final.items) == len(draft.items):
        for f_item, d_item in zip(final.items, draft.items):
            f_item.url = d_item.url
            f_item.source_name = d_item.source_name
    else:
        print("  humanize pass changed the item count; keeping the draft's items.", file=sys.stderr)
        final.items = draft.items
    return final


# --------------------------------------------------------------------------- rendering

MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def human_date(date: dt.date) -> str:
    return f"{date.day} de {MONTHS_ES[date.month - 1]} de {date.year}"


def md(text: str) -> str:
    return markdown.markdown((text or "").strip(), output_format="html5")


def render_template(name: str, **values: str) -> str:
    tpl = (TEMPLATES / name).read_text(encoding="utf-8")
    for key, value in values.items():
        safe = value if key.endswith("_html") else html.escape(value, quote=True)
        tpl = tpl.replace("{{" + key + "}}", safe)
    leftover = re.findall(r"{{\w+}}", tpl)
    if leftover:
        raise RuntimeError(f"Placeholders sin valor en {name}: {leftover}")
    return tpl


def render_body(post: Post) -> str:
    parts = [md(post.intro_md)]
    for item in post.items:
        parts.append(
            f'<h2><a href="{html.escape(item.url, quote=True)}" rel="noopener" target="_blank">'
            f"{html.escape(item.headline)}</a></h2>"
        )
        parts.append(f'<p class="source">Fuente: {html.escape(item.source_name)}</p>')
        parts.append(md(item.summary_md))
        pyme = md(item.pyme_md).replace("<p>", "<p><strong>Para tu pyme:</strong> ", 1)
        parts.append(f'<div class="pyme">{pyme}</div>')
    if post.closing_md.strip():
        parts.append(f'<div class="closing">{md(post.closing_md)}</div>')
    links = "".join(
        f'<li><a href="{html.escape(i.url, quote=True)}" rel="noopener" target="_blank">'
        f"{html.escape(i.source_name)}: {html.escape(i.headline)}</a></li>"
        for i in post.items
    )
    parts.append(f'<div class="fuentes"><strong>Fuentes</strong><ul>{links}</ul></div>')
    return "\n".join(parts)


def load_index() -> list[dict]:
    if POSTS_JSON.exists():
        try:
            data = json.loads(POSTS_JSON.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    return []


def post_cards_html(entries: list[dict]) -> str:
    if not entries:
        return ('<div class="empty-state">Todavía no hay notas publicadas. La primera sale mañana a la mañana. '
                'Mientras tanto, <a href="../#servicios">mirá qué hacemos</a>.</div>')
    cards = []
    for e in entries:
        d = dt.date.fromisoformat(e["date"])
        cards.append(
            '<article class="post-card">'
            f'<time datetime="{e["date"]}">{html.escape(human_date(d))}</time>'
            f'<h3><a href="{html.escape(e["path"])}">{html.escape(e["title"])}</a></h3>'
            f'<p>{html.escape(e["summary"])}</p>'
            f'<a class="text-link" href="{html.escape(e["path"])}">Leer</a>'
            "</article>"
        )
    return '<div class="post-list">' + "\n".join(cards) + "</div>"


def write_feed(entries: list[dict]) -> None:
    items = []
    for e in entries[:30]:
        d = dt.datetime.combine(dt.date.fromisoformat(e["date"]), dt.time(8, 0), tzinfo=ARGENTINA_TZ)
        url = SITE_URL + "blog/" + e["path"]
        items.append(
            "<item>"
            f"<title>{xml_escape(e['title'])}</title>"
            f"<link>{xml_escape(url)}</link>"
            f"<guid isPermaLink=\"true\">{xml_escape(url)}</guid>"
            f"<pubDate>{d.strftime('%a, %d %b %Y %H:%M:%S %z')}</pubDate>"
            f"<description>{xml_escape(e['summary'])}</description>"
            "</item>"
        )
    now = dt.datetime.now(ARGENTINA_TZ).strftime("%a, %d %b %Y %H:%M:%S %z")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n<channel>\n'
        f"<title>{SITE_NAME} · Notas diarias</title>\n"
        f"<link>{xml_escape(SITE_URL)}blog/</link>\n"
        f'<atom:link href="{xml_escape(SITE_URL)}blog/feed.xml" rel="self" type="application/rss+xml"/>\n'
        "<description>Lo que pasó en inteligencia artificial y en la economía argentina, explicado para pymes.</description>\n"
        "<language>es-AR</language>\n"
        f"<lastBuildDate>{now}</lastBuildDate>\n"
        + "\n".join(items)
        + "\n</channel>\n</rss>\n"
    )
    FEED_XML.write_text(xml, encoding="utf-8")


def write_sitemap(entries: list[dict]) -> None:
    today = dt.date.today().isoformat()
    urls = [(SITE_URL, today), (SITE_URL + "blog/", today)]
    urls += [(SITE_URL + "blog/" + e["path"], e["date"]) for e in entries]
    body = "".join(
        f"<url><loc>{xml_escape(u)}</loc><lastmod>{d}</lastmod></url>\n" for u, d in urls
    )
    SITEMAP_XML.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + body + "</urlset>\n",
        encoding="utf-8",
    )


def render_site(post: Post | None, date: dt.date) -> None:
    """Write the post (if any) and regenerate index, posts.json, feed and sitemap."""
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    entries = load_index()
    year = str(dt.date.today().year)

    if post is not None:
        rel_path = f"posts/{date.isoformat()}.html"
        page = render_template(
            "post.html",
            title=post.title,
            summary=post.summary,
            url=SITE_URL + "blog/" + rel_path,
            site_url=SITE_URL,
            date_iso=date.isoformat(),
            date_human=human_date(date),
            body_html=render_body(post),
            year=year,
        )
        (BLOG / rel_path).write_text(page, encoding="utf-8")
        entries = [e for e in entries if e.get("date") != date.isoformat()]
        entries.append({"date": date.isoformat(), "path": rel_path, "title": post.title,
                        "summary": post.summary, "slug": post.slug})
        print(f"Wrote blog/{rel_path}")

    # Drop index entries whose file no longer exists (e.g. a post removed by hand).
    entries = [e for e in entries if (BLOG / e.get("path", "")).is_file()]
    entries.sort(key=lambda e: e["date"], reverse=True)
    POSTS_JSON.write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (BLOG / "index.html").write_text(
        render_template("blog_index.html", site_url=SITE_URL, posts_html=post_cards_html(entries), year=year),
        encoding="utf-8",
    )
    write_feed(entries)
    write_sitemap(entries)
    print(f"Index rebuilt with {len(entries)} post(s).")


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="render the fixture instead of calling the API")
    parser.add_argument("--force", action="store_true", help="overwrite today's post if it already exists")
    parser.add_argument("--date", help="post date, YYYY-MM-DD (default: today in Buenos Aires)")
    parser.add_argument("--rebuild", action="store_true", help="only regenerate index, feed and sitemap")
    args = parser.parse_args(argv)

    now = dt.datetime.now(dt.timezone.utc)
    date = dt.date.fromisoformat(args.date) if args.date else now.astimezone(ARGENTINA_TZ).date()

    if args.rebuild:
        render_site(None, date)
        return 0

    target = POSTS_DIR / f"{date.isoformat()}.html"
    if target.exists() and not args.force:
        print(f"{target.relative_to(ROOT)} ya existe; nada que hacer (usá --force para regenerar).")
        return 0

    if args.dry_run:
        post = Post.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
        print("Dry run: rendering fixture.")
        render_site(post, date)
        return 0

    items = collect_items(now)
    if len(items) < MIN_ITEMS:
        print(f"Solo {len(items)} ítems; no se publica hoy.")
        return 0

    try:
        post = generate_post(items, date)
    except Exception as exc:  # noqa: BLE001 - we want a clear red run, with the class name
        import anthropic

        if isinstance(exc, anthropic.RateLimitError):
            print("Rate limit del API; el workflow puede reintentarse más tarde.", file=sys.stderr)
        elif isinstance(exc, anthropic.APIStatusError):
            print(f"Error del API ({exc.status_code}): {exc.message}", file=sys.stderr)
        elif isinstance(exc, anthropic.APIConnectionError):
            print("No se pudo conectar con el API.", file=sys.stderr)
        else:
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if len(post.items) < MIN_ITEMS:
        print(f"El modelo devolvió {len(post.items)} ítems; no se publica.", file=sys.stderr)
        return 1

    render_site(post, date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
