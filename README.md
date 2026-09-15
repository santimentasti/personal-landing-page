# Caudal · landing page y blog diario

Sitio estático de **Caudal**, la consultora de automatización de procesos e integración de sistemas para pymes de Santiago Mentasti. Incluye:

- **Landing page** (`index.html`): servicios, cómo trabajamos, resultados, FAQ y llamada a la acción (diagnóstico gratuito por Calendly).
- **Blog diario** (`blog/`): cada mañana un job de GitHub Actions lee fuentes de noticias de inteligencia artificial y de economía/política argentina, escribe una nota en español con un ángulo "para tu pyme" usando Claude, la pasa por las reglas del skill [humanizer](https://github.com/blader/humanizer) y la publica.

Sin frameworks ni build: HTML, CSS y un poco de JavaScript. Se hospeda en GitHub Pages.

## Estructura

```
index.html                 landing
css/styles.css             estilos base
css/blog.css               tipografía del blog
js/main.js                 menú móvil, reveal, teaser del blog
assets/                    favicon y og-image (SVG)
blog/index.html            lista de notas (generado)
blog/posts/YYYY-MM-DD.html notas (generadas)
blog/posts.json            índice que usa la home (generado)
blog/feed.xml              RSS del blog (generado)
sitemap.xml                (generado)
scripts/generate_post.py   generador diario
scripts/templates/         plantillas de nota y de índice
scripts/fixtures/          nota de ejemplo para --dry-run
scripts/humanizer/         SKILL.md vendoreado de blader/humanizer (MIT)
.github/workflows/         deploy-pages.yml y daily-post.yml
```

## Editar el contenido

- Textos de la landing: directamente en `index.html`. Los estilos viven en `css/styles.css` (variables de color al principio).
- Enlace de Calendly, WhatsApp y email: aparecen en `index.html` y en las dos plantillas de `scripts/templates/`. Buscá `calendly.com`, `wa.me` y `mailto:`.
- URL del sitio (canonical, OG, RSS): constante `SITE_URL` en `scripts/generate_post.py` y los `<link rel="canonical">` / `og:url` de `index.html`. Cambiarla cuando haya dominio propio.
- Fuentes del blog: lista `SOURCES` al principio de `scripts/generate_post.py`. Cada entrada tiene `name`, `url` y `group` (`labs`, `press`, `argentina`). `GROUP_QUOTA` define cuántos ítems de cada grupo llegan al modelo.
- Tono y reglas editoriales: `EDITORIAL_GUIDE` y `HUMANIZE_INSTRUCTIONS` en el mismo archivo.

## Puesta en marcha (una sola vez)

1. **Secreto**: en el repo, Settings → Secrets and variables → Actions → New repository secret: `ANTHROPIC_API_KEY`.
2. **Pages**: Settings → Pages → Source: **GitHub Actions**.
3. **Permisos**: Settings → Actions → General → Workflow permissions: **Read and write permissions**.
4. Mergear esta rama a `main`. El push a `main` dispara el deploy de la landing.
5. Actions → **Daily post** → Run workflow, para publicar la primera nota sin esperar al cron.

Después de eso, el job corre solo todos los días a las 08:00 (hora de Buenos Aires), commitea la nota en `main` y vuelve a desplegar. Si un día no hay suficientes noticias (menos de 3 ítems en las últimas 36 h) no publica nada.

## Correr el generador localmente

```bash
pip install -r scripts/requirements.txt

# Sin llamar al API: renderiza la nota de ejemplo de scripts/fixtures/
python scripts/generate_post.py --dry-run

# Con el API (modelo claude-sonnet-5)
export ANTHROPIC_API_KEY=...
python scripts/generate_post.py            # la nota de hoy
python scripts/generate_post.py --force    # regenerarla
python scripts/generate_post.py --rebuild  # solo índice, feed y sitemap

# Ver el sitio
python3 -m http.server 8080   # http://localhost:8080
```

La nota generada en `--dry-run` es ficticia: no la commitees.

## Cómo funciona el generador

1. Descarga los feeds en paralelo, se queda con lo publicado en las últimas 36 horas, limpia el HTML, deduplica por título y aplica las cuotas por grupo (máximo 40 ítems).
2. Llama a Claude (`claude-sonnet-5`) con dos bloques de sistema: la guía editorial de Caudal y el `SKILL.md` de humanizer (cacheado con prompt caching). Pide la nota como JSON con esquema fijo (structured outputs).
3. Segunda llamada: pasa el borrador por el proceso de cuatro pasos de humanizer y devuelve el mismo JSON con la prosa corregida. Los enlaces y fuentes se restauran del borrador por si el modelo los tocó.
4. Renderiza la nota, el índice, `posts.json`, `feed.xml` y `sitemap.xml`.

Costo estimado por nota con Sonnet 5: unos centavos de dólar (dos llamadas, sistema cacheado).

## Licencias

El código del sitio es de Santiago Mentasti. `scripts/humanizer/` contiene material de [blader/humanizer](https://github.com/blader/humanizer) bajo licencia MIT; ver `scripts/humanizer/NOTICE.md`.
