"""
Generador de tablero de licitaciones desde Google Sheets
---------------------------------------------------------
Uso:
  python licitaciones.py          -> genera el HTML una vez
  python licitaciones.py --auto   -> actualiza automaticamente cada 24hs

Requiere:
  pip install gspread google-auth

Archivos necesarios en la misma carpeta:
  - credenciales.json   (descargado de Google Cloud Console)
  - licitaciones.py     (este script)

El archivo generado:
  - licitaciones.html   (se crea/actualiza automaticamente)
"""

import os
import sys
import time
import json
from datetime import datetime

try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:
    print("Faltan librerias. Ejecuta: pip install gspread google-auth")
    sys.exit(1)

# ───────────────────────────────────────────────────────────
# CONFIGURACION — edita estos valores si es necesario
# ───────────────────────────────────────────────────────────

CREDENCIALES_JSON = "credenciales.json"
SPREADSHEET_ID    = "1pIqmXRuOtwxF13D4oMGJFEQPJ1C-W2DvEWyX-juwQZw"
NOMBRE_HOJA       = "cuadro"
ARCHIVO_SALIDA    = "licitaciones.html"
INTERVALO_HORAS   = 24

# ───────────────────────────────────────────────────────────
# ETAPAS (orden del proceso)
# ───────────────────────────────────────────────────────────

STAGES = [
    ("sum_compras",   "Pase SUM -> Compras"),
    ("apertura",      "Apertura / 1 llamado"),
    ("seg_llamado",   "2 llamado"),
    ("compras_salud", "Pase Compras -> Salud"),
    ("it_ca",         "Pase IT -> CA/Compras"),
    ("legal_tec",     "Pase a Legal/Tecnica"),
    ("despacho",      "Pase a Despacho"),
    ("decreto",       "Decreto"),
    ("oc",            "OC"),
]

# Columna del tipo de licitacion en el sheet (0 = primera columna)
# Si la primera columna es "Tipo", usar COL_TIPO = 0 y COL_DESC = 1
# Si la primera columna es "Descripcion", usar COL_TIPO = None y COL_DESC = 0
COL_TIPO = 0
COL_DESC = 1
COL_DATOS_INICIO = 2   # columna donde empiezan las fechas

TIPOS_MAP = {
    "licitacion publica":  "publica",
    "licitacion privada":  "privada",
    "concurso de precios": "concurso",
}

# ───────────────────────────────────────────────────────────
# LEER DATOS DESDE GOOGLE SHEETS
# ───────────────────────────────────────────────────────────

def conectar_sheets():
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(CREDENCIALES_JSON, scopes=scopes)
    client = gspread.authorize(creds)
    return client

def normalizar_tipo(raw):
    if not raw:
        return "publica"
    key = raw.strip().lower()
    for k, v in TIPOS_MAP.items():
        if k in key:
            return v
    return "publica"

def leer_datos(client):
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(NOMBRE_HOJA)
    rows = sheet.get_all_values()
    if not rows:
        return []

    data = []
    for row in rows[1:]:
        # Asegurar longitud minima
        while len(row) < COL_DATOS_INICIO + len(STAGES):
            row.append("")

        tipo_raw = row[COL_TIPO].strip() if COL_TIPO is not None else ""
        desc = row[COL_DESC].strip()
        if not desc:
            continue

        tipo = normalizar_tipo(tipo_raw)
        item = {"desc": desc, "tipo": tipo}

        for i, (key, _) in enumerate(STAGES):
            col = COL_DATOS_INICIO + i
            raw = row[col].strip() if col < len(row) else ""
            if not raw:
                item[key] = None
            elif raw.strip() in ("-", "- "):
                item[key] = {"v": "-", "s": "skip"}
            else:
                item[key] = {"v": raw, "s": "done"}

        data.append(item)

    return data

# ───────────────────────────────────────────────────────────
# CALCULAR METRICAS
# ───────────────────────────────────────────────────────────

def count_done(item):
    c = 0
    for key, _ in STAGES:
        cell = item.get(key)
        if not cell:
            break
        if cell["s"] == "done":
            c += 1
    return c

def get_last_done(item):
    last = None
    last_label = None
    for key, label in STAGES:
        cell = item.get(key)
        if not cell:
            break
        if cell["s"] == "done":
            last = cell["v"]
            last_label = label
    return last, last_label

def get_status(item):
    oc = item.get("oc")
    if oc and oc["s"] == "done":
        return "completada"
    first = item.get(STAGES[0][0])
    if not first:
        return "sin-iniciar"
    return "en-curso"

def calc_pct(item, status):
    done_and_skip = sum(
        1 for key, _ in STAGES
        if item.get(key) and item[key]["s"] in ("done", "skip")
    )
    if status == "completada":
        return 100
    return min(99, round((done_and_skip / len(STAGES)) * 100))

# ───────────────────────────────────────────────────────────
# GENERAR HTML
# ───────────────────────────────────────────────────────────

def cell_json(cell):
    if cell is None:
        return "null"
    return json.dumps(cell)

def generar_html(data, timestamp):
    # Construir RAW js con tipo incluido
    raw_js_lines = ["const RAW = ["]
    for item in data:
        line = f'  {{desc:{json.dumps(item["desc"])},tipo:{json.dumps(item["tipo"])},'
        parts = [f'{key}:{cell_json(item.get(key))}' for key, _ in STAGES]
        line += ",".join(parts) + "},"
        raw_js_lines.append(line)
    raw_js_lines.append("];")
    raw_js = "\n".join(raw_js_lines)

    stages_js = "[" + ",".join(
        f'{{key:{json.dumps(k)},label:{json.dumps(l)}}}'
        for k, l in STAGES
    ) + "]"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Seguimiento de Licitaciones</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
  :root{{
    --bg:#f5f4f0;--surface:#ffffff;
    --border:rgba(0,0,0,0.09);--border-md:rgba(0,0,0,0.15);
    --text:#1a1a18;--text-muted:#6b6b67;--text-faint:#aaaaa5;
    --blue:#2060c8;--blue-light:#d6e4ff;--blue-mid:#b5d4f4;
    --gray-seg:#ddddd8;--radius:12px;--radius-sm:8px;
  }}
  body{{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:2rem 1.5rem;}}
  header{{max-width:960px;margin:0 auto 2rem;display:flex;align-items:baseline;gap:1rem;flex-wrap:wrap;}}
  h1{{font-size:22px;font-weight:500;letter-spacing:-0.3px;}}
  .subtitle{{font-size:13px;color:var(--text-muted);font-family:'DM Mono',monospace;}}
  .summary-cards{{max-width:960px;margin:0 auto 1.5rem;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;}}
  .card{{background:var(--surface);border:0.5px solid var(--border);border-radius:var(--radius-sm);padding:12px 14px;}}
  .card-label{{font-size:11px;color:var(--text-muted);margin-bottom:4px;font-family:'DM Mono',monospace;text-transform:uppercase;letter-spacing:0.5px;}}
  .card-value{{font-size:22px;font-weight:500;}}
  .card-value.blue{{color:var(--blue);}} .card-value.green{{color:#1a7a4a;}} .card-value.muted{{color:var(--text-muted);}}
  .controls{{max-width:960px;margin:0 auto 0.75rem;display:flex;gap:8px;flex-wrap:wrap;align-items:center;}}
  .filter-btn{{font-size:12px;font-family:'DM Sans',sans-serif;padding:5px 14px;border-radius:99px;border:0.5px solid var(--border-md);background:transparent;color:var(--text-muted);cursor:pointer;transition:all 0.15s;}}
  .filter-btn:hover{{background:var(--surface);color:var(--text);}}
  .filter-btn.active{{background:var(--text);color:#fff;border-color:var(--text);}}
  .filter-btn.pub.active{{background:#1a5c9e;border-color:#1a5c9e;color:#fff;}}
  .filter-btn.priv.active{{background:#6d28d9;border-color:#6d28d9;color:#fff;}}
  .filter-btn.conc.active{{background:#1a7a4a;border-color:#1a7a4a;color:#fff;}}
  .sort-label{{font-size:12px;color:var(--text-faint);margin-left:auto;}}
  select{{font-size:12px;font-family:'DM Sans',sans-serif;padding:5px 10px;border-radius:var(--radius-sm);border:0.5px solid var(--border-md);background:var(--surface);color:var(--text);cursor:pointer;}}
  .divider{{width:1px;height:20px;background:var(--border-md);margin:0 2px;flex-shrink:0;}}
  .legend{{max-width:960px;margin:0 auto 1.2rem;display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:var(--text-muted);align-items:center;}}
  .legend span{{display:flex;align-items:center;gap:5px;}}
  .ldot{{width:10px;height:10px;border-radius:2px;flex-shrink:0;}}
  .list{{max-width:960px;margin:0 auto;display:flex;flex-direction:column;gap:6px;}}
  .row{{background:var(--surface);border:0.5px solid var(--border);border-radius:var(--radius);overflow:hidden;cursor:pointer;transition:border-color 0.15s,box-shadow 0.15s;}}
  .row:hover{{border-color:var(--border-md);box-shadow:0 2px 8px rgba(0,0,0,0.05);}}
  .row.expanded{{border-color:var(--border-md);}}
  .row-head{{display:flex;align-items:center;gap:10px;padding:11px 16px;}}
  .row-name{{font-size:13px;font-weight:500;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
  .badge{{display:inline-block;font-size:10px;font-weight:500;padding:2px 8px;border-radius:99px;white-space:nowrap;flex-shrink:0;}}
  .badge.pub{{background:#dbeafe;color:#1a5c9e;}} .badge.priv{{background:#ede9fe;color:#6d28d9;}} .badge.conc{{background:#d1fae5;color:#1a7a4a;}}
  .row-days{{font-size:11px;font-family:'DM Mono',monospace;color:var(--text-faint);white-space:nowrap;flex-shrink:0;}}
  .row-days.recent{{color:#1a7a4a;}} .row-days.old{{color:#b45309;}}
  .row-last{{font-size:11px;color:var(--text-muted);white-space:nowrap;max-width:150px;overflow:hidden;text-overflow:ellipsis;text-align:right;font-family:'DM Mono',monospace;flex-shrink:0;}}
  .row-pct{{font-size:13px;font-weight:500;font-family:'DM Mono',monospace;min-width:38px;text-align:right;color:var(--blue);flex-shrink:0;}}
  .row-pct.zero{{color:var(--text-faint);}} .row-pct.full{{color:#1a7a4a;}}
  .chevron{{width:14px;height:14px;color:var(--text-faint);flex-shrink:0;transition:transform 0.2s;}}
  .row.expanded .chevron{{transform:rotate(180deg);}}
  .track{{display:flex;height:4px;margin:0 16px 11px;border-radius:2px;overflow:hidden;gap:1px;}}
  .seg{{flex:1;}}
  .detail{{display:none;border-top:0.5px solid var(--border);padding:14px 16px;background:#fafaf8;}}
  .detail.open{{display:block;}}
  .detail-meta{{display:flex;gap:20px;margin-bottom:12px;flex-wrap:wrap;padding-bottom:10px;border-bottom:0.5px solid var(--border);}}
  .detail-meta-item{{font-size:11px;color:var(--text-muted);font-family:'DM Mono',monospace;}}
  .detail-meta-item strong{{color:var(--text);font-weight:500;}}
  .stage-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px 16px;}}
  .stage-item{{display:flex;align-items:flex-start;gap:8px;}}
  .sdot{{width:7px;height:7px;border-radius:50%;flex-shrink:0;margin-top:4px;}}
  .sinfo{{display:flex;flex-direction:column;gap:1px;}}
  .sname{{font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.4px;font-family:'DM Mono',monospace;}}
  .sdate{{font-size:12px;font-weight:500;font-family:'DM Mono',monospace;}}
  .sdate.done{{color:var(--text);}} .sdate.skip{{color:var(--text-faint);font-style:italic;}} .sdate.pending{{color:var(--text-faint);}}
  .badge-last{{display:inline-block;font-size:9px;background:var(--blue-light);color:var(--blue);border-radius:4px;padding:1px 5px;margin-left:4px;vertical-align:middle;font-family:'DM Sans',sans-serif;font-weight:500;}}
  .hint{{max-width:960px;margin:1rem auto 0;text-align:center;font-size:11px;color:var(--text-faint);font-family:'DM Mono',monospace;}}
  @media(max-width:680px){{
    .summary-cards{{grid-template-columns:repeat(2,1fr);}}
    .stage-grid{{grid-template-columns:repeat(2,minmax(0,1fr));}}
    .row-last,.row-days{{display:none;}}
  }}
</style>
</head>
<body>
<header>
  <h1>Seguimiento de licitaciones</h1>
  <span class="subtitle">— actualizado {timestamp}</span>
</header>
<div class="summary-cards" id="summary"></div>
<div class="controls">
  <button class="filter-btn active" data-f="todas">Todas</button>
  <button class="filter-btn" data-f="en-curso">En curso</button>
  <button class="filter-btn" data-f="completada">OC emitida</button>
  <button class="filter-btn" data-f="sin-iniciar">Sin iniciar</button>
  <div class="divider"></div>
  <button class="filter-btn pub" data-f="publica">Licitacion publica</button>
  <button class="filter-btn priv" data-f="privada">Licitacion privada</button>
  <button class="filter-btn conc" data-f="concurso">Concurso de precios</button>
  <span class="sort-label">Ordenar:</span>
  <select id="sort-sel">
    <option value="progress-desc">Mayor avance</option>
    <option value="progress-asc">Menor avance</option>
    <option value="days-desc">Mas dias sin mover</option>
    <option value="alpha">Alfabetico</option>
  </select>
</div>
<div class="legend" style="margin-top:0.75rem;">
  <span><span class="ldot" style="background:#2060c8"></span>Completada</span>
  <span><span class="ldot" style="background:#ddddd8"></span>Pendiente</span>
  <span><span class="ldot" style="background:#b5d4f4"></span>No aplica</span>
  <span style="margin-left:6px;"><span class="badge pub">publica</span></span>
  <span><span class="badge priv">privada</span></span>
  <span><span class="badge conc">concurso</span></span>
</div>
<div class="list" id="list"></div>
<p class="hint">Hace clic en cada fila para ver el detalle de fechas por etapa.</p>

<script>
const STAGES = {stages_js};
{raw_js}

function parseDate(str){{
  if(!str||str==='-') return null;
  const p=str.split('/');
  if(p.length!==3) return null;
  return new Date(parseInt(p[2]),parseInt(p[1])-1,parseInt(p[0]));
}}
function daysSince(str){{
  const d=parseDate(str);if(!d) return null;
  const today=new Date();today.setHours(0,0,0,0);
  const diff=Math.floor((today-d)/(1000*60*60*24));
  return diff>=0?diff:null;
}}
function countDone(item){{
  let c=0;for(let s of STAGES){{const cell=item[s.key];if(!cell)break;if(cell.s==='done')c++;}}return c;
}}
function getLastDone(item){{
  let last=null,lastLabel=null;
  for(let s of STAGES){{const cell=item[s.key];if(!cell)break;if(cell.s==='done'){{last=cell.v;lastLabel=s.label;}}}}
  return {{last,lastLabel}};
}}
function getStatus(item){{
  if(item.oc&&item.oc.s==='done') return 'completada';
  if(!item[STAGES[0].key]) return 'sin-iniciar';
  return 'en-curso';
}}
function calcPct(item,status){{
  const ds=STAGES.filter(s=>{{const c=item[s.key];return c&&(c.s==='done'||c.s==='skip');}}).length;
  if(status==='completada') return 100;
  return Math.min(99,Math.round((ds/STAGES.length)*100));
}}
function segColor(cell){{
  if(!cell) return '#ddddd8';
  if(cell.s==='skip') return '#b5d4f4';
  if(cell.s==='done') return '#2060c8';
  return '#ddddd8';
}}
function tipoBadge(tipo){{
  const map={{publica:['pub','publica'],privada:['priv','privada'],concurso:['conc','concurso']}};
  const [cls,lbl]=map[tipo]||['',''];
  return `<span class="badge ${{cls}}">${{lbl}}</span>`;
}}
function tipoLabel(tipo){{
  return {{publica:'Licitacion publica',privada:'Licitacion privada',concurso:'Concurso de precios'}}[tipo]||'';
}}

const data=RAW.map(d=>{{
  const {{last,lastLabel}}=getLastDone(d);
  const status=getStatus(d);
  const pct=calcPct(d,status);
  const dias=daysSince(last);
  return {{...d,_done:countDone(d),_pct:pct,_status:status,_last:last,_lastLabel:lastLabel,_dias:dias}};
}});

let activeFilter='todas',activeSort='progress-desc';

function updateSummary(items){{
  const t=items.length,ec=items.filter(i=>i._status==='en-curso').length,
        co=items.filter(i=>i._status==='completada').length,
        si=items.filter(i=>i._status==='sin-iniciar').length;
  document.getElementById('summary').innerHTML=`
    <div class="card"><div class="card-label">Total</div><div class="card-value">${{t}}</div></div>
    <div class="card"><div class="card-label">En curso</div><div class="card-value blue">${{ec}}</div></div>
    <div class="card"><div class="card-label">OC emitida</div><div class="card-value green">${{co}}</div></div>
    <div class="card"><div class="card-label">Sin iniciar</div><div class="card-value muted">${{si}}</div></div>`;
}}

function render(){{
  let items=[...data];
  const estadoF=['en-curso','completada','sin-iniciar'];
  const tipoF=['publica','privada','concurso'];
  if(estadoF.includes(activeFilter)) items=items.filter(i=>i._status===activeFilter);
  else if(tipoF.includes(activeFilter)) items=items.filter(i=>i.tipo===activeFilter);

  if(activeSort==='progress-desc') items.sort((a,b)=>b._pct-a._pct);
  else if(activeSort==='progress-asc') items.sort((a,b)=>a._pct-b._pct);
  else if(activeSort==='days-desc') items.sort((a,b)=>(b._dias??-1)-(a._dias??-1));
  else items.sort((a,b)=>a.desc.localeCompare(b.desc));

  updateSummary(items);
  const list=document.getElementById('list');
  list.innerHTML='';
  if(!items.length){{list.innerHTML='<p style="color:#aaaaa5;font-size:13px;text-align:center;padding:2rem;">Sin resultados.</p>';return;}}

  items.forEach(item=>{{
    const pct=item._pct;
    let pctClass='row-pct';if(pct===0)pctClass+=' zero';if(pct===100)pctClass+=' full';
    let diasTxt='',diasClass='row-days';
    if(item._dias!==null){{
      diasTxt=item._dias===0?'hoy':item._dias===1?'hace 1 dia':`hace ${{item._dias}} dias`;
      if(item._dias<=7) diasClass+=' recent';
      else if(item._dias>=30) diasClass+=' old';
    }}
    const row=document.createElement('div');
    row.className='row';
    row.innerHTML=`
      <div class="row-head">
        <span class="row-name">${{item.desc}}</span>
        ${{tipoBadge(item.tipo)}}
        <span class="${{diasClass}}">${{diasTxt}}</span>
        <span class="row-last">${{item._lastLabel||'Sin iniciar'}}</span>
        <span class="${{pctClass}}">${{pct}}%</span>
        <svg class="chevron" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 6l4 4 4-4"/></svg>
      </div>
      <div class="track">
        ${{STAGES.map(s=>{{const c=item[s.key];return`<div class="seg" style="background:${{segColor(c)}}" title="${{s.label}}: ${{c?(c.s==='skip'?'no aplica':c.v):'pendiente'}}"></div>`;}} ).join('')}}
      </div>
      <div class="detail">
        <div class="detail-meta">
          <div class="detail-meta-item">Tipo: <strong>${{tipoLabel(item.tipo)}}</strong></div>
          <div class="detail-meta-item">Ultimo pase: <strong>${{item._last||'—'}}</strong>${{item._dias!==null?` <span style="color:var(--text-faint)">(${{diasTxt}})</span>`:''}}</div>
          <div class="detail-meta-item">Avance: <strong>${{pct}}%</strong></div>
        </div>
        <div class="stage-grid">
          ${{STAGES.map(s=>{{
            const c=item[s.key];
            const isLast=item._last&&c&&c.v===item._last&&c.s==='done';
            let dotColor='#ddddd8';if(c) dotColor=c.s==='skip'?'#b5d4f4':'#2060c8';
            const dateClass=c?(c.s==='skip'?'sdate skip':'sdate done'):'sdate pending';
            return`<div class="stage-item"><div class="sdot" style="background:${{dotColor}}"></div><div class="sinfo"><div class="sname">${{s.label}}${{isLast?'<span class="badge-last">ultimo pase</span>':''}}</div><div class="${{dateClass}}">${{c?(c.s==='skip'?'no aplica':c.v):'—'}}</div></div></div>`;
          }}).join('')}}
        </div>
      </div>`;
    row.querySelector('.row-head').addEventListener('click',()=>{{row.classList.toggle('expanded');row.querySelector('.detail').classList.toggle('open');}});
    list.appendChild(row);
  }});
}}

document.querySelectorAll('.filter-btn').forEach(btn=>{{
  btn.addEventListener('click',()=>{{
    activeFilter=btn.dataset.f;
    document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');render();
  }});
}});
document.getElementById('sort-sel').addEventListener('change',e=>{{activeSort=e.target.value;render();}});
render();
</script>
</body>
</html>"""
    return html

# ───────────────────────────────────────────────────────────
# MAIN
# ───────────────────────────────────────────────────────────

def actualizar():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Conectando con Google Sheets...")
    client = conectar_sheets()
    data = leer_datos(client)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {len(data)} licitaciones leidas.")
    timestamp = datetime.now().strftime("%d/%m/%Y a las %H:%M")
    html = generar_html(data, timestamp)
    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Archivo '{ARCHIVO_SALIDA}' generado correctamente.")

def main():
    auto = "--auto" in sys.argv

    if not os.path.exists(CREDENCIALES_JSON):
        print(f"No se encontro el archivo '{CREDENCIALES_JSON}'.")
        print("Segui las instrucciones del README para configurar las credenciales.")
        sys.exit(1)

    if auto:
        print(f"Modo automatico — actualizando cada {INTERVALO_HORAS} horas. Ctrl+C para detener.")
        while True:
            try:
                actualizar()
                print(f"   Proxima actualizacion en {INTERVALO_HORAS} horas...")
                time.sleep(INTERVALO_HORAS * 3600)
            except KeyboardInterrupt:
                print("\nDetenido.")
                break
            except Exception as e:
                print(f"Error: {e}. Reintentando en 10 minutos...")
                time.sleep(600)
    else:
        actualizar()

if __name__ == "__main__":
    main()
