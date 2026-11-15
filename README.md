# 📡 Radar WSB: sentimiento de Reddit con IA

Web que se actualiza sola cada 3 horas y muestra:

- qué acciones se mencionan más en r/wallstreetbets, r/stocks y r/options;
- cuáles se están mencionando **mucho más de lo normal** (🔥 anomalía);
- si el foro está **alcista, bajista o en modo meme** con cada una, según la IA;
- los comentarios reales más votados, con el precio de la acción al lado.

Todo corre gratis en GitHub. Lo único que se paga es la IA, que cuesta poco (lo explico en el paso 3).

---

## Cómo funciona

```
Cada 3 horas, GitHub ejecuta el robot  ──►  scripts/collect.py
   1. Lee posts y comentarios de Reddit (últimas 24 h)
   2. Detecta los tickers ($TSLA, NVDA, "nvidia"…) sin falsos positivos
   3. Claude clasifica los comentarios: alcista / bajista / neutral / meme
   4. Descarga precios de Yahoo Finance
   5. Guarda docs/data/latest.json
                    │
                    ▼
GitHub Pages publica docs/index.html  ──►  https://TU-USUARIO.github.io/radar-wsb/
```

Si Reddit bloquea la conexión, el robot usa **ApeWisdom** como respaldo. En ese caso hay conteos de menciones, pero no sentimiento con IA, y la web lo avisa.

---

## Instalación paso a paso (unos 20 minutos)

### Paso 1: Subir el proyecto a GitHub

1. Entra a <https://github.com/new>.
2. En **Repository name** escribe `radar-wsb` y márcalo como **Public**. GitHub Pages es gratis solo en repos públicos.
3. Clic en **Create repository**.
4. En la página que aparece, clic en **"uploading an existing file"**.
5. Descomprime el ZIP y **arrastra todo el contenido de la carpeta** (no la carpeta en sí) a la ventana de GitHub. Luego clic en **Commit changes**.

> ⚠️ **Importante:** la carpeta `.github` está oculta en Mac y Windows y es la que contiene el robot.
> - **Mac:** en Finder, presiona `Cmd + Shift + .` para ver los archivos ocultos antes de arrastrar.
> - **Windows:** en el Explorador, ve a *Vista → Mostrar → Elementos ocultos*.
>
> Para comprobar que quedó bien, en GitHub debe existir el archivo `.github/workflows/actualizar.yml`.
> Si no aparece, créalo a mano: *Add file → Create new file*, escribe `.github/workflows/actualizar.yml` como nombre y pega el contenido del archivo.

### Paso 2: Crear la "app" de Reddit (gratis)

1. Con tu cuenta de Reddit abierta, entra a <https://www.reddit.com/prefs/apps>.
2. Abajo, clic en **"create another app…"** (o "are you a developer? create an app").
3. Llena el formulario así:
   - **name:** `radar-wsb`
   - tipo: marca **script**
   - **redirect uri:** `http://localhost:8080`
4. Clic en **create app**.
5. Anota dos datos:
   - **client ID:** el código corto debajo de "personal use script";
   - **secret:** el código al lado de "secret".

> Reddit ha ido endureciendo el acceso a su API. Si no te deja crear la app o te pide aprobación, salta este paso: el sistema intenta el acceso público y, si tampoco funciona, usa ApeWisdom.

### Paso 3: Crear la API key de Claude (para el sentimiento con IA)

1. Entra a <https://console.anthropic.com/>, crea la cuenta y agrega un poco de crédito en *Billing*. Con 5 USD alcanza para empezar.
2. Ve a **API Keys → Create Key** y copia la clave (empieza por `sk-ant-…`).

**Costo:** en cada ejecución la IA lee como máximo 15 acciones × 40 comentarios. Con el modelo pequeño (Haiku) son centavos por ejecución. Revisa el consumo real en la consola después del primer día y ajusta si hace falta (ver "Ajustes").

### Paso 4: Guardar las claves en GitHub (de forma segura)

En tu repo: **Settings → Secrets and variables → Actions → New repository secret**. Crea estos tres:

| Nombre | Valor |
|---|---|
| `REDDIT_CLIENT_ID` | el client ID del paso 2 |
| `REDDIT_CLIENT_SECRET` | el secret del paso 2 |
| `ANTHROPIC_API_KEY` | la clave del paso 3 |

Los secrets están cifrados: nadie los ve, ni siquiera quien visite el repo.

### Paso 5: Darle permiso al robot para guardar datos

**Settings → Actions → General →** baja hasta **Workflow permissions →** marca **Read and write permissions → Save**.

### Paso 6: Activar la web (GitHub Pages)

**Settings → Pages →** en *Source* elige **Deploy from a branch**, en *Branch* elige **main** y la carpeta **/docs**, y dale a **Save**.

Al minuto tu web está en: `https://TU-USUARIO.github.io/radar-wsb/`

Al principio muestra **datos simulados**, con un aviso amarillo, para que veas que funciona.

### Paso 7: Primera ejecución

1. Ve a la pestaña **Actions** del repo. Si te pregunta, dale a *"I understand my workflows, go ahead and enable them"*.
2. Clic en **Actualizar Radar WSB → Run workflow → Run workflow**.
3. Tarda unos 3–5 minutos. Cuando aparezca ✅, espera 1–2 minutos más y recarga tu web.

Desde ese momento se actualiza sola cada 3 horas.

---

## Ajustes (opcionales)

En **Settings → Secrets and variables → Actions → pestaña Variables** puedes crear:

| Variable | Por defecto | Para qué |
|---|---|---|
| `SUBREDDITS` | `wallstreetbets,stocks,options` | qué foros leer |
| `AI_TOP_TICKERS` | `15` | cuántas acciones analiza la IA |
| `AI_MAX_PER_TICKER` | `40` | cuántos comentarios lee por acción (más = más preciso y más caro) |
| `CLAUDE_MODEL` | `claude-haiku-5-5` | modelo de IA |
| `SEC_USER_AGENT` | genérico | `Radar WSB tu@email.com` (respaldo para la lista de tickers) |

**Frecuencia:** en `.github/workflows/actualizar.yml`, cambia `*/3` por `*/1` para que corra cada hora. Ten en cuenta que más ejecuciones también significan más gasto de IA.

---

## Si algo falla

| Síntoma | Qué pasa / qué hacer |
|---|---|
| La ejecución sale ❌ con *permission denied* al hacer push | Te faltó el paso 5. |
| La web dice "se usaron conteos de ApeWisdom" | Reddit no respondió. Revisa el client ID y el secret, o es un bloqueo temporal; prueba más tarde. |
| La web dice "Sin ANTHROPIC_API_KEY" | El secret no existe, tiene otro nombre o no tiene crédito. |
| "Histórico en construcción" | Es normal los primeros 2 días: la comparación contra el promedio necesita datos previos. |
| El robot dejó de correr solo | GitHub pausa los workflows programados en repos sin actividad por 60 días. Reactívalo en Actions. |
| Ejecución ❌ "No hay lista de tickers confiable" | Las fuentes de tickers no respondieron. Se detiene a propósito para no publicar datos malos; se arregla solo en la siguiente ejecución. |

Para ver qué pasó en una ejecución: **Actions →** clic en la ejecución **→ update →** abre el paso "Recolectar y analizar".

---

## Probar en tu computadora (opcional)

```bash
pip install -r requirements.txt
python scripts/collect.py --demo       # datos simulados
cd docs && python -m http.server 8000  # abre http://localhost:8000
```

---

## Metodología y limitaciones

- **Tickers:** se aceptan `$CASHTAGS`, nombres de empresas conocidos y símbolos en mayúsculas de 3–5 letras que coticen en EE. UU. Se descartan palabras comunes (IT, ALL, DD, AI, ON…) y textos escritos casi todo en mayúsculas.
- **Anti-spam:** máximo 3 menciones por usuario y ticker; hacen falta 3+ autores distintos para entrar al ranking.
- **Anomalía:** menciones de hoy ÷ promedio de los 7 días anteriores. 🔥 si es ≥ 2× con 10+ menciones.
- **Sentimiento:** Claude lee los comentarios más votados de cada acción. Puntaje = (alcistas − bajistas) / total.
- La muestra no es "todo Reddit", y la IA puede fallar con la ironía extrema.
- **Mucha mención no significa que la acción vaya a subir.** Es una herramienta educativa, **no una recomendación de inversión**.
