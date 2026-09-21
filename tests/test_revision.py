"""Pruebas de la revision humana: veredictos y reclasificacion.

Fase 2 de docs/DISENO-INTERACCION-HUMANA.md. Es la primera vez que algo escribe
en el expediente desde fuera del workflow, asi que lo que se comprueba no es
solo que funcione: es que no se pueda estropear el registro desde la pantalla, y
que lo que escribe el workflow y lo que escribe la persona convivan sin pisarse.
"""
import io
import json
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import config

TMP = Path(tempfile.mkdtemp(prefix="rca_rev_"))
config.INCIDENTS_DIR = str(TMP / "inc")
config.AUDIT_FILE = str(TMP / "audit.jsonl")
config.LOG_FILE = str(TMP / "app.log")
config.WORKFLOW_ENABLED = True

import observability
observability.configure_logging()
import incidents
import webhook
from fastapi.testclient import TestClient

CLIENTE = TestClient(webhook.app)
fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


DIAG = {
    "root_causes": [
        {"cause": "Obstruccion del impulsor", "evidence": ["El caudal cae."],
         "recommended_action": "Inspeccion visual."},
        {"cause": "Desgaste de anillos", "evidence": ["Rendimiento a la baja."],
         "recommended_action": "Medir holguras."},
        {"cause": "Deriva del sensor", "evidence": ["Lecturas erraticas."],
         "recommended_action": "Calibrar."},
    ],
    "_ai_generated": {"provider": "anthropic", "model": "claude-opus-5", "notice": "Hipotesis."},
}

_n = [0]


def sembrar(estado=incidents.FINALIZADO, diagnostico=DIAG):
    _n[0] += 1
    r = incidents.claim({
        "KPIName": "Hydraulic Efficiency", "Asset": f"PS20102 A03 PS02 Pump {_n[0]:02d}",
        "Subsystem": "PS01", "System": "EP", "Plant": "WWTP",
        "KPI": 41.3, "Limit": 48.0, "LimitThresholdType": "Low",
        "StartTime": f"2026-09-15T{_n[0] % 24:02d}:00:00Z",
    })
    assert r is not None
    incidents.mark(r["id"], estado, diagnostico=diagnostico,
                   trace={"step3_prompt": "P" * 3000})
    return r["id"]


def veredicto(iid, **cuerpo):
    return CLIENTE.post(f"/incidentes/{iid}/veredicto", json=cuerpo)


print("\n=== 1. Descartar una causa con su evidencia ===")
iid = sembrar()
r = veredicto(iid, causa=0, veredicto="descartada",
              evidencia="No hay obstruccion, comprobacion visual.")
check("responde 200", r.status_code == 200, f"{r.status_code}: {r.text[:90]}")
d = r.json()
check("la causa queda descartada", d["estadoCausas"][0] == incidents.DESCARTADA, d["estadoCausas"])
check("las otras siguen pendientes", d["estadoCausas"][1:] == [incidents.PENDIENTE] * 2, d["estadoCausas"])
apunte = d["revision"]["veredictos"][0]
check("guarda la evidencia", apunte["evidencia"] == "No hay obstruccion, comprobacion visual.")
check("guarda cuando fue", "en" in apunte, apunte)
check("y a que iteracion pertenece", apunte["iteracion"] == 1, apunte)

print("\n=== 2. LO QUE HACE UTIL EL REANALISIS: sin evidencia no se descarta ===")
# La evidencia es obligatoria al descartar, y no por burocracia: es lo UNICO que
# hace que una segunda pasada valga para algo. Sin ella, el modelo devolveria el
# segundo clasificado sobre los mismos datos.
for vacia in ("", "   ", "\t\n"):
    r = veredicto(iid, causa=1, veredicto="descartada", evidencia=vacia)
    check(f"rechaza evidencia {vacia!r}", r.status_code == 400, r.status_code)
check("el mensaje se puede enseñar tal cual",
      "por qué no es" in veredicto(iid, causa=1, veredicto="descartada").json()["detail"],
      veredicto(iid, causa=1, veredicto="descartada").json())
check("y la causa sigue pendiente",
      CLIENTE.get(f"/incidentes/{iid}").json()["estadoCausas"][1] == incidents.PENDIENTE)

print("\n=== 3. Confirmar no exige evidencia ===")
# Confirmar es aceptar lo que el modelo ya justifico; descartar es contradecirlo.
# No es la misma carga de la prueba.
iid2 = sembrar()
check("confirma sin evidencia", veredicto(iid2, causa=0, veredicto="confirmada").status_code == 200)

print("\n=== 4. La sospecha es opcional y va SEPARADA de la evidencia ===")
# Separarlas en la estructura es lo que impide que el modelo tome la hipotesis
# del operario por buena: una entra como hecho, la otra como pista a contrastar.
iid3 = sembrar()
veredicto(iid3, causa=0, veredicto="descartada",
          evidencia="No hay obstruccion.", sospecha="Puede ser el rodete.")
ap = CLIENTE.get(f"/incidentes/{iid3}").json()["revision"]["veredictos"][0]
check("guarda las dos por separado",
      ap.get("evidencia") == "No hay obstruccion." and ap.get("sospecha") == "Puede ser el rodete.", ap)
veredicto(iid3, causa=1, veredicto="descartada", evidencia="Tampoco.")
ap2 = CLIENTE.get(f"/incidentes/{iid3}").json()["revision"]["veredictos"][1]
check("sin sospecha no aparece el campo vacio", "sospecha" not in ap2, ap2)

print("\n=== 5. Corregir un veredicto: se anota encima, no se borra ===")
# Un clic mal dado no puede ser permanente. Y la lista es de solo anadir, asi que
# el expediente conserva los dos apuntes y queda constancia de la rectificacion.
iid4 = sembrar()
veredicto(iid4, causa=0, veredicto="descartada", evidencia="Me he equivocado de fila.")
d = veredicto(iid4, causa=0, veredicto="pendiente").json()
check("la causa vuelve a pendiente", d["estadoCausas"][0] == incidents.PENDIENTE, d["estadoCausas"])
check("pero los DOS apuntes siguen ahi", len(d["revision"]["veredictos"]) == 2,
      d["revision"]["veredictos"])
check("el primero conserva su evidencia",
      d["revision"]["veredictos"][0]["evidencia"] == "Me he equivocado de fila.")
d = veredicto(iid4, causa=0, veredicto="confirmada").json()
check("y se puede volver a juzgar", d["estadoCausas"][0] == incidents.CONFIRMADA, d["estadoCausas"])
check("tres apuntes", len(d["revision"]["veredictos"]) == 3)

print("\n=== 6. No se puede juzgar lo que no existe ===")
check("causa fuera de rango", veredicto(iid, causa=9, veredicto="confirmada").status_code == 400)
check("causa negativa", veredicto(iid, causa=-1, veredicto="confirmada").status_code == 400)
check("veredicto inventado", veredicto(iid, causa=0, veredicto="regular").status_code == 400)
check("incidente que no existe",
      veredicto("deadbeef__20260101T000000Z", causa=0, veredicto="confirmada").status_code == 400)

print("\n=== 7. Un incidente sin diagnostico no admite veredicto ===")
# Un 'fallido' o un 'pausado' no llegaron a producir causas. No es que falte el
# dato: es que no hay nada que juzgar, y el mensaje lo dice asi.
roto = sembrar(estado=incidents.FALLIDO, diagnostico=None)
r = veredicto(roto, causa=0, veredicto="confirmada")
check("responde 400", r.status_code == 400, r.status_code)
check("y lo explica", "no tiene causas" in r.json()["detail"], r.json())

print("\n=== 8. El id de la URL tampoco sirve aqui para salirse del directorio ===")
# Mismo guardian que en lectura, ahora en un endpoint que ESCRIBE.
(TMP / "secreto.json").write_text('{"clave":"no deberia verse"}', encoding="utf-8")
for intento in ("../secreto", "..%2Fsecreto", "a/../../secreto"):
    r = CLIENTE.post(f"/incidentes/{intento}/veredicto",
                     json={"causa": 0, "veredicto": "confirmada"})
    check(f"rechaza '{intento}'", r.status_code in (400, 404, 422), r.status_code)
check("el fichero de fuera sigue intacto",
      json.loads((TMP / "secreto.json").read_text(encoding="utf-8"))["clave"] == "no deberia verse")

print("\n=== 9. Reclasificar: la alerta no debio existir ===")
iid5 = sembrar()
r = CLIENTE.post(f"/incidentes/{iid5}/reclasificacion", json={"reclasificacion": "alerta_no_valida"})
check("responde 200", r.status_code == 200, f"{r.status_code}: {r.text[:80]}")
check("queda marcada", r.json()["revision"]["reclasificacion"] == incidents.ALERTA_NO_VALIDA)
r = CLIENTE.post(f"/incidentes/{iid5}/reclasificacion", json={"reclasificacion": None})
check("se puede retirar", r.json()["revision"]["reclasificacion"] is None)
check("valor inventado se rechaza",
      CLIENTE.post(f"/incidentes/{iid5}/reclasificacion",
                   json={"reclasificacion": "me_lo_invento"}).status_code == 400)

print("\n=== 10. NO existe 'causa_confirmada' como reclasificacion ===")
# El diseno la preveia, para quien vuelve en frio y dice "la primera era la
# buena". Sobra: el veredicto no caduca y el cierre se deduce, asi que eso es un
# veredicto normal dado mas tarde. Tener dos caminos para lo mismo es tener dos
# sitios que pueden discrepar.
check("se rechaza como reclasificacion",
      CLIENTE.post(f"/incidentes/{iid5}/reclasificacion",
                   json={"reclasificacion": "causa_confirmada"}).status_code == 400)
tarde = sembrar()
check("y en su lugar vale un veredicto normal, dado cuando sea",
      veredicto(tarde, causa=0, veredicto="confirmada").json()["estadoCausas"][0] == incidents.CONFIRMADA)

print("\n=== 11. Los dos escritores conviven ===")
# Lo que motivo el refactor del 2026-09-15. La persona escribe 'revision'; el
# workflow escribe 'estado', 'diagnostico', 'trace' y 'motivo'. Ninguno pisa al
# otro, en ningun orden.
iid6 = sembrar()
veredicto(iid6, causa=0, veredicto="descartada", evidencia="No es esto.")
incidents.mark(iid6, incidents.FINALIZADO, diagnostico=DIAG, trace={"step6_prompts": ["X"]})
fichero = json.loads((TMP / "inc" / f"{iid6}.json").read_text(encoding="utf-8"))
check("el veredicto sobrevive a que escriba el workflow",
      fichero["revision"]["veredictos"][0]["evidencia"] == "No es esto.", fichero.get("revision"))
check("y el trace del workflow sigue ahi", "step6_prompts" in fichero["trace"])
veredicto(iid6, causa=1, veredicto="descartada", evidencia="Tampoco.")
fichero = json.loads((TMP / "inc" / f"{iid6}.json").read_text(encoding="utf-8"))
check("el trace sobrevive a que escriba la persona", "step6_prompts" in fichero["trace"])
check("y hay dos veredictos", len(fichero["revision"]["veredictos"]) == 2)

print("\n=== 12. El estado de las causas se CALCULA, no se guarda ===")
# Guardarlo seria un segundo sitio del que fiarse, y los dos pueden
# desincronizarse. El fichero guarda los hechos; el estado es su consecuencia.
check("no hay un campo 'estadoCausas' en disco", "estadoCausas" not in fichero, list(fichero))
check("pero el endpoint lo devuelve",
      CLIENTE.get(f"/incidentes/{iid6}").json()["estadoCausas"] ==
      [incidents.DESCARTADA, incidents.DESCARTADA, incidents.PENDIENTE])

print("\n=== 13. Todo esto queda en el audit trail ===")
# Escribir en el expediente es una operacion con efectos, y el audit trail es lo
# unico que dice que algo escribio, cuando y sobre que incidente.
auditoria = (TMP / "audit.jsonl").read_text(encoding="utf-8")
check("los veredictos se auditan", "incident.veredicto" in auditoria)
check("las reclasificaciones tambien", "incident.reclasificacion" in auditoria)

print("\n=== 13b. Confirmar una causa CIERRA el incidente al momento ===")
# La ventana es lo que esperas cuando no hay respuesta. Con una causa confirmada
# la pregunta esta contestada, asi que no hay nada que esperar: tener en la
# pantalla de "lo que pide atencion" algo que no pide nada es lo contrario de
# para lo que sirve.
iid7 = sembrar()
reg = incidents.leer_por_id(iid7)
check("recien analizado esta en activos", not incidents.esta_cerrado(reg))
check("y su cierre provisional es 'sin veredicto'", incidents.cierre(reg)[0] == incidents.SIN_VEREDICTO)
veredicto(iid7, causa=0, veredicto="confirmada")
reg = incidents.leer_por_id(iid7)
check("tras confirmar, cerrado", incidents.esta_cerrado(reg))
etiqueta, terminal = incidents.cierre(reg)
check("etiquetado 'causa confirmada'", etiqueta == incidents.CAUSA_CONFIRMADA, etiqueta)
check("y marcado como terminal", terminal is True)

print("\n=== 13c. Descartarlas TODAS tambien cierra (en la Fase 2) ===")
# En la Fase 3 dejara de ser terminal: se intercalara el relanzado, y solo al
# agotarse el presupuesto se concluira que la causa no se determino.
iid8 = sembrar()
for n in range(3):
    veredicto(iid8, causa=n, veredicto="descartada", evidencia=f"Comprobado que no es la {n}.")
reg = incidents.leer_por_id(iid8)
check("cerrado", incidents.esta_cerrado(reg))
check("como 'causa no determinada'", incidents.cierre(reg)[0] == incidents.CAUSA_NO_DETERMINADA)

print("\n=== 13d. Descartar SOLO ALGUNAS no cierra: aun puede volver ===")
iid9 = sembrar()
veredicto(iid9, causa=0, veredicto="descartada", evidencia="Esta no.")
reg = incidents.leer_por_id(iid9)
check("sigue en activos", not incidents.esta_cerrado(reg))
check("con cierre provisional 'revisado parcialmente'",
      incidents.cierre(reg)[0] == incidents.REVISADO_PARCIALMENTE)

print("\n=== 13e. Las dos pestañas ===")
def lista(qs=""):
    return CLIENTE.get("/incidentes?horas=0&limite=500" + qs).json()

act = [i["id"] for i in lista("&cerrados=false")["incidentes"]]
cer = [i["id"] for i in lista("&cerrados=true")["incidentes"]]
check("el confirmado esta en cerrados", iid7 in cer and iid7 not in act)
check("el parcial esta en activos", iid9 in act and iid9 not in cer)
check("ningun incidente esta en las dos", not (set(act) & set(cer)))
cuentas = lista()["pestanas"]
check("las cuentas de las dos vienen juntas", set(cuentas) == {"activos", "cerrados"}, cuentas)
check("y cuadran", cuentas["activos"] == len(act) and cuentas["cerrados"] == len(cer), cuentas)

print("\n=== 13f. 'pausado' no sale en NINGUNA pestaña ===")
# No es informacion de un incidente sino del sistema entero -- que alguien bajo
# el interruptor --, y eso va en un aviso arriba de la pantalla. Cuarenta filas
# iguales serian ruido donde basta una linea.
pausado = sembrar(estado=incidents.PAUSADO, diagnostico=None)
check("no esta en activos", pausado not in [i["id"] for i in lista("&cerrados=false")["incidentes"]])
check("ni en cerrados", pausado not in [i["id"] for i in lista("&cerrados=true")["incidentes"]])
check("la pantalla avisa de que el analisis esta parado",
      "análisis automático está desactivado" in CLIENTE.get("/pantalla").text)

print("\n=== 14. La pantalla trae los botones de veredicto ===")
html = CLIENTE.get("/pantalla").text
check("boton de confirmar", "data-confirmar" in html)
check("boton de descartar", "data-descartar" in html)
check("boton de deshacer", "data-deshacer" in html)
check("campo obligatorio de evidencia", 'name="evidencia"' in html and "required" in html)
check("campo opcional de sospecha", 'name="sospecha"' in html)
check("la sospecha se marca como opcional", "opcional" in html)
check("envia al endpoint de veredicto", "/veredicto" in html)

print("\n=== 15. EL FALLO QUE HABRIA TENIDO: el refresco borra lo escrito ===")
# La pantalla se refresca cada 5 s. Si al refrescar re-pinta el detalle mientras
# alguien escribe la evidencia, le borra el texto -- y el operario no entiende
# por que. El refresco AUTOMATICO se abstiene de re-pintar mientras hay un
# formulario abierto; el que disparan los propios botones si re-pinta, porque es
# el que tiene que mostrar el resultado.
check("el refresco distingue automatico de manual", "auto = false" in html)
check("el temporizador se marca como automatico", "refrescar({auto: true})" in html)
check("y con formulario abierto no re-pinta el detalle",
      "auto && formAbierto !== null" in html)
# La lista SI sigue actualizandose: si se suspendiera todo, un formulario que
# alguien deja abierto y olvida congelaria el monitor entero.
check("pero la lista sigue viva", "La lista ya se ha" in html)

print("\n=== 16. El contenido del modelo no se concatena dentro de codigo ===")
# Los manejadores se conectan desde JavaScript, no como atributos onclick en el
# HTML. Asi el texto que devuelve el modelo nunca acaba dentro de algo
# ejecutable, pase lo que pase con el escapado.
check("no hay onclick= en las plantillas", "onclick=\"" not in html)
check("los manejadores se conectan aparte", "conectarVeredictos" in html)

print("\n=== 17. Se avisa del fallo sin perder lo escrito ===")
# Si el servidor rechaza el veredicto, el mensaje se enseña tal cual -- esta
# escrito para quien esta delante -- y el formulario conserva lo tecleado.
check("guarda el error para mostrarlo", "errorForm" in html)
check("y repinta el formulario con lo que habia", "previo.evidencia" in html)

print("\n=== 17b. Se dice que el veredicto lo dio una PERSONA ===")
# En la misma pantalla conviven lo que propuso la IA y lo que concluyo un
# humano. Que una causa ponga solo "descartada" deja sin decir lo unico que
# importa de ese apunte: que no lo decidio el modelo.
check("la confirmada dice quien fue", "Confirmada por el operario" in html)
check("la descartada tambien", "Descartada por el operario" in html)
check("y se dice cuando", "cuando-veredicto" in html)
# Rodeada de rojo, como la confirmada de verde: las dos estan juzgadas y las dos
# se ven de un vistazo.
check("la descartada se rodea de rojo", ".causa.descartada{border-color:var(--malo)" in html)
# Y NO se atenua: dentro lleva la evidencia que escribio una persona, que es la
# parte mas valiosa del expediente. Al 62 % de opacidad costaba leerla justo a
# quien tiene que leerla.
check("sin atenuar el bloque entero", ".causa.descartada{opacity" not in html)

print("\n=== 17c. Una lista vacia dice POR QUE esta vacia ===")
# "Sin incidentes registrados" era mentira en el caso mas frecuente: SI los hay,
# pero fuera del periodo. Y ademas dejaba a quien mira sin saber que tocar.
# Paso de verdad el 2026-09-15 al arrancar el servicio: el unico incidente real
# era de hacia ocho dias y el periodo por defecto son 24 h.
check("distingue 'no hay' de 'no hay AQUI'", "vacioPorque" in html)
check("dice que amplie el periodo", "Amplía el periodo" in html)
# NO repite cuantos hay en la otra pestaña: la pestaña ya lleva su cuenta, y el
# unico dato que la pantalla no daba ya era que el periodo puede esconderlos.
check("sin repetir la cuenta de la otra pestaña", "Hay ${enLaOtra}" not in html)
check("con el periodo en Todo no aconseja ampliarlo",
      'if(rango.horas === "0") return `Ninguno ${aqui}.`' in html)

print("\n=== 17d. En un cerrado manda el CIERRE, no el estado del workflow ===")
# "Listo" solo cuenta que el analisis termino. "Causa no determinada" cuenta en
# QUE acabo, que es posterior y es lo que alguien quiere saber al abrir un
# expediente cerrado. Mientras sigue activo manda el estado, porque el cierre
# que se deduce ahi es provisional y presentarlo como un hecho seria adelantar
# una conclusion que no esta tomada.
d_cerrado = CLIENTE.get(f"/incidentes/{iid8}").json()   # el de todas descartadas
check("el detalle trae si esta cerrado", d_cerrado.get("cerrado") is True)
check("y con que etiqueta", d_cerrado.get("cierre") == incidents.CAUSA_NO_DETERMINADA,
      d_cerrado.get("cierre"))
d_activo = CLIENTE.get(f"/incidentes/{iid9}").json()    # el parcial, sigue activo
check("un activo no se marca como cerrado", d_activo.get("cerrado") is False)
check("la pantalla elige una u otra en un solo sitio", "function chipEstado" in html)
check("y la usan lista y detalle", html.count("chipEstado(") >= 3, html.count("chipEstado("))

print("\n=== 17e. Cada pestaña filtra por lo SUYO ===")
# En activos importa el estado del workflow -- en cola, procesando, pendiente de
# revisar -- porque dice que esta haciendo el sistema. En cerrados importa COMO
# acabo: filtrar alli por 'finalizado' no distinguiria nada, porque lo son casi
# todos. Son dos preguntas distintas y necesitan dos botoneras distintas.
cer = lista("&cerrados=true")
act = lista("&cerrados=false")
check("el servidor cuenta las dos dimensiones",
      "recuento" in cer and "recuentoCierre" in cer, list(cer))
check("en cerrados hay cuentas por etiqueta de cierre",
      incidents.CAUSA_CONFIRMADA in cer["recuentoCierre"], cer["recuentoCierre"])
# Y se puede filtrar por ellas.
solo = lista("&cerrados=true&cierre=causa_confirmada")
check("filtrar por 'causa confirmada' devuelve solo esos",
      solo["mostrados"] > 0 and all(i["cierre"] == incidents.CAUSA_CONFIRMADA
                                     for i in solo["incidentes"]),
      [i["cierre"] for i in solo["incidentes"]])
check("y cuadra con lo que decia la pastilla",
      solo["mostrados"] == cer["recuentoCierre"][incidents.CAUSA_CONFIRMADA])
check("la pantalla elige la botonera segun la pestaña",
      "verCerrados ? datos.recuentoCierre : datos.recuento" in html)
check("y manda el parametro que toca",
      'p.set(verCerrados ? "cierre" : "estado", filtro)' in html)

print("\n=== 17e bis. EL FALLO: el filtro de Cerrados se autoanulaba ===")
# refrescar() comprueba si la opcion filtrada sigue existiendo y, si no, vuelve
# a "Todos". Esa comprobacion miraba SIEMPRE datos.recuento (las claves de
# activos: 'finalizado', 'fallido'...), nunca datos.recuentoCierre. Al pulsar
# una pastilla de Cerrados -- p.ej. "causa_confirmada" -- esa clave no existe en
# 'recuento', la comprobacion daba falso positivo de "ya no existe" y el propio
# refrescar() deshacia el filtro antes de que llegase a pintarse: el clic no
# tenia ningun efecto visible.
check("la comprobacion de reintento no usa SIEMPRE 'datos.recuento'",
      "!(datos.recuento || {})[filtro]" not in html)
check("usa el recuento de la pestaña actual",
      "cuentasPestanaActual" in html and
      "(verCerrados ? datos.recuentoCierre : datos.recuento) || {}" in html)
check("y la comparacion se hace contra esa variable",
      "!cuentasPestanaActual[filtro]" in html)

print("\n=== 17d bis. DESHACER devuelve el incidente a donde estaba ===")
# El reloj de las 12 h contaba desde "la ultima vez que se toco el fichero", asi
# que cualquier clic rejuvenecia el incidente: uno cerrado hacia tres dias
# reaparecia en Activos porque alguien confirmo una causa por error y la
# deshizo. Deshacer no es trabajo, es una correccion, y no debe rejuvenecer
# nada.
#
# Ahora el reloj cuenta desde el ultimo TRABAJO QUE SIGUE EN PIE: el movimiento
# del workflow, o el veredicto mas reciente de cada causa que no sea
# 'pendiente'. Un 'pendiente' es un deshacer y no aporta fecha, asi que el reloj
# retrocede solo.
import datetime as _dt

def _envejecer_workflow(iid, horas):
    """Simula que el workflow lo movio hace N horas y nadie lo toco despues."""
    f = TMP / "inc" / f"{iid}.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    viejo = (_dt.datetime.now(_dt.timezone.utc)
             - _dt.timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d["movimiento_workflow"] = viejo
    d["actualizado_en"] = viejo
    f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

def _cierre_de(iid):
    r = incidents.leer_por_id(iid)
    return incidents.cierre(r)[0], incidents.esta_cerrado(r)

# --- Caso 1: estaba ACTIVO. Confirmar y deshacer lo deja activo. ---
act = sembrar()
check("parte de activo, sin veredicto", _cierre_de(act) == (incidents.SIN_VEREDICTO, False))
veredicto(act, causa=0, veredicto="confirmada")
check("confirmar lo cierra", _cierre_de(act) == (incidents.CAUSA_CONFIRMADA, True))
veredicto(act, causa=0, veredicto="pendiente")
check("deshacer lo devuelve a ACTIVO, no lo deja cerrado",
      _cierre_de(act) == (incidents.SIN_VEREDICTO, False), _cierre_de(act))

# --- Caso 2: estaba SIN VEREDICTO (viejo). Debe volver a estarlo. ---
viejo = sembrar()
_envejecer_workflow(viejo, incidents.HORAS_EN_ACTIVOS + 6)
check("parte de cerrado por reloj", _cierre_de(viejo) == (incidents.SIN_VEREDICTO, True))
veredicto(viejo, causa=0, veredicto="confirmada")
check("confirmar lo cierra como confirmada", _cierre_de(viejo)[0] == incidents.CAUSA_CONFIRMADA)
veredicto(viejo, causa=0, veredicto="pendiente")
check("y al deshacer NO reaparece en activos",
      _cierre_de(viejo) == (incidents.SIN_VEREDICTO, True), _cierre_de(viejo))

# --- Caso 3: revisado parcialmente con DOS descartadas, se deshace una. ---
dos = sembrar()
veredicto(dos, causa=0, veredicto="descartada", evidencia="Esta no.")
veredicto(dos, causa=1, veredicto="descartada", evidencia="Esta tampoco.")
check("dos descartadas: revisado parcialmente",
      _cierre_de(dos)[0] == incidents.REVISADO_PARCIALMENTE)
veredicto(dos, causa=0, veredicto="pendiente")
check("al deshacer una, SIGUE revisado parcialmente",
      _cierre_de(dos)[0] == incidents.REVISADO_PARCIALMENTE, _cierre_de(dos))

# --- Caso 4: una sola descartada en un incidente VIEJO, se deshace. ---
una = sembrar()
_envejecer_workflow(una, incidents.HORAS_EN_ACTIVOS + 6)
veredicto(una, causa=0, veredicto="descartada", evidencia="No es esta.")
# Revisar en frio un incidente YA CERRADO no lo reabre (2026-09-21). Antes si, y
# el expediente rebotaba entre las dos pestañas mientras se trabajaba en el.
# Ademas era un contrasentido: la reclasificacion en frio es lo que rescata el
# dato de acierto, y resucitar el incidente por hacerla convierte una virtud en
# un castigo.
check("con una descartada: revisado parcialmente, y SIGUE cerrado",
      _cierre_de(una) == (incidents.REVISADO_PARCIALMENTE, True), _cierre_de(una))
veredicto(una, causa=0, veredicto="pendiente")
check("al deshacerla vuelve a SIN VEREDICTO y cerrado",
      _cierre_de(una) == (incidents.SIN_VEREDICTO, True), _cierre_de(una))

# --- Caso 5: causa no determinada, se deshace una: pasa por parcial. ---
todas = sembrar()
for n in range(3):
    veredicto(todas, causa=n, veredicto="descartada", evidencia=f"La {n} no es.")
check("todas descartadas: causa no determinada",
      _cierre_de(todas)[0] == incidents.CAUSA_NO_DETERMINADA)
veredicto(todas, causa=2, veredicto="pendiente")
check("al deshacer una pasa por revisado parcialmente",
      _cierre_de(todas)[0] == incidents.REVISADO_PARCIALMENTE, _cierre_de(todas))

print("\n=== 17d bis-2. Un incidente ANTIGUO, sin el campo nuevo, tampoco rejuvenece ===")
# Los incidentes anteriores al 2026-09-18 -- incluidos los dos reales que hay en
# produccion -- no tienen 'movimiento_workflow'. El respaldo NO puede ser
# 'actualizado_en', que lo pisa cada escritura: con el, el primer veredicto lo
# ponia a "ahora" y el incidente se daba por recien llegado, que es justo el
# fallo que el anclaje venia a arreglar. Se recurre a 'recibido_en', que se
# escribe una vez y no se vuelve a tocar.
#
# Lo destapo una prueba en vivo contra los fixtures, que tampoco llevan el campo:
# las pruebas de arriba no lo cogieron porque sembrar() usa mark(), que si lo
# escribe.
legado = sembrar()
_f = TMP / "inc" / f"{legado}.json"
_d = json.loads(_f.read_text(encoding="utf-8"))
_viejo = (_dt.datetime.now(_dt.timezone.utc)
          - _dt.timedelta(hours=incidents.HORAS_EN_ACTIVOS + 6)).strftime("%Y-%m-%dT%H:%M:%SZ")
_d.pop("movimiento_workflow", None)          # como un incidente de antes
_d["recibido_en"] = _viejo
_d["actualizado_en"] = _viejo
_f.write_text(json.dumps(_d, ensure_ascii=False), encoding="utf-8")

check("sin el campo nuevo, se le da por cerrado igual",
      _cierre_de(legado) == (incidents.SIN_VEREDICTO, True), _cierre_de(legado))
veredicto(legado, causa=0, veredicto="confirmada")
veredicto(legado, causa=0, veredicto="pendiente")
check("y al confirmar y deshacer NO reaparece en activos",
      _cierre_de(legado) == (incidents.SIN_VEREDICTO, True), _cierre_de(legado))
check("el respaldo es 'recibido_en', nunca 'actualizado_en'",
      'registro.get("movimiento_workflow") or registro.get("recibido_en")'
      in Path(RAIZ / "incidents.py").read_text(encoding="utf-8"))

print("\n=== 17d bis-3. LA SECUENCIA DEL REBOTE: revisar en frio no reabre ===")
# Reportado sobre PS20102 A03 PS02 Pump 14 (2026-09-21). Un incidente cerrado
# como 'sin veredicto' rebotaba entre las dos pestañas a cada clic:
#
#   sin veredicto -> descarto una  -> ACTIVOS
#                 -> descarto todas -> CERRADOS
#                 -> deshago una    -> ACTIVOS
#                 -> deshago todas  -> CERRADOS
#
# Se trabaja en el expediente y no para de moverse de sitio. Ahora un veredicto
# solo alarga el reloj si se dio mientras el incidente seguia ABIERTO: los que
# llegan despues cambian la etiqueta y nada mas.
seq = sembrar()
_envejecer_workflow(seq, incidents.HORAS_EN_ACTIVOS + 6)
pasos = []
pasos.append(("de partida", _cierre_de(seq)))
veredicto(seq, causa=0, veredicto="descartada", evidencia="La 0 no es.")
pasos.append(("descarto una", _cierre_de(seq)))
for n in (1, 2):
    veredicto(seq, causa=n, veredicto="descartada", evidencia=f"La {n} tampoco.")
pasos.append(("descarto todas", _cierre_de(seq)))
veredicto(seq, causa=2, veredicto="pendiente")
pasos.append(("deshago una", _cierre_de(seq)))
for n in (0, 1):
    veredicto(seq, causa=n, veredicto="pendiente")
pasos.append(("deshago todas", _cierre_de(seq)))

for nombre, (etiqueta, cerrado) in pasos:
    print(f"      {nombre:16} -> {etiqueta:22} {'CERRADOS' if cerrado else 'activos'}")
check("no sale de Cerrados en ningun paso", all(c for _, (_, c) in pasos),
      [(n, c) for n, (_, c) in pasos])
check("las etiquetas recorren el camino esperado",
      [e for _, (e, _) in pasos] == [incidents.SIN_VEREDICTO, incidents.REVISADO_PARCIALMENTE,
                                     incidents.CAUSA_NO_DETERMINADA, incidents.REVISADO_PARCIALMENTE,
                                     incidents.SIN_VEREDICTO],
      [e for _, (e, _) in pasos])

print("\n=== 17d bis-4. Pero un incidente ABIERTO si desliza el reloj ===")
# Lo de arriba no puede cargarse el deslizamiento: el operario que descarta una
# causa a las 11 h y vuelve a las 13 h tiene que seguir teniendo su ventana. La
# cadena sigue viva mientras cada veredicto caiga dentro de la que abrio el
# anterior.
abierto = sembrar()
_envejecer_workflow(abierto, incidents.HORAS_EN_ACTIVOS - 1)   # abierto por poco
check("parte de abierto", _cierre_de(abierto)[1] is False)
veredicto(abierto, causa=0, veredicto="descartada", evidencia="Dentro de plazo.")
check("un veredicto a tiempo lo mantiene abierto y alarga el reloj",
      _cierre_de(abierto) == (incidents.REVISADO_PARCIALMENTE, False), _cierre_de(abierto))
# Y el anclaje se ha movido al veredicto, no se ha quedado en el analisis.
_reg = incidents.leer_por_id(abierto)
check("el anclaje es la fecha del veredicto",
      incidents._anclaje_reloj(_reg) == _reg["revision"]["veredictos"][-1]["en"],
      incidents._anclaje_reloj(_reg))

print("\n=== 17d bis-5. La marca no dice 'pendiente de revisar' si ya se reviso ===")
# El estado del workflow es 'finalizado' tanto si nadie lo ha tocado como si se
# descarto una causa, asi que por si solo no distingue nada -- y la fila decia
# "pendiente de revisar" justo despues de revisarla a medias.
check("manda el cierre tambien con el incidente abierto",
      "x.cerrado || hayVeredictos" in html)
check("salvo cuando no hay veredictos, que ahi 'pendiente de revisar' es exacto",
      'x.cierre !== "sin_veredicto"' in html)

print("\n=== 17d ter. El anclaje se CALCULA, no se guarda ===")
# El bool "ha pasado por sin veredicto" se descarto por dos motivos: nada existe
# para ponerlo -- no hay barrendero ni temporizador, el cierre se deduce
# precisamente para que no haya que ejecutar nada -- y seria estado derivado
# guardado, que es lo que se quito al eliminar 'finalizado_en'.
reg = incidents.leer_por_id(dos)
check("no hay un campo de cierre guardado", "cierre" not in reg and "cerrado" not in reg, list(reg))
check("ni un bool de 'ya estuvo cerrado'",
      not any("sin_veredicto" in k or "estuvo" in k for k in reg), list(reg))
# Lo unico que se guarda es un hecho: cuando movio el workflow este incidente.
check("solo se guarda el movimiento del workflow", "movimiento_workflow" in reg)
check("y solo lo escribe mark()",
      'cambios: dict = {\n        "estado": estado,\n        "movimiento_workflow"'
      in Path(RAIZ / "incidents.py").read_text(encoding="utf-8"))

print("\n=== 17e ter. Dar un veredicto no te quita el incidente de debajo ===")
# El caso: filtras por "sin veredicto", confirmas una causa y el incidente pasa a
# "causa confirmada". Deja de cumplir el filtro, la lista lo expulsaba y te metia
# en el detalle de OTRA bomba. Es castigar al operario por hacer justo lo que se
# le pedia -- y encima le deja sin poder deshacer un clic mal dado, porque ya no
# lo tiene delante.
check("hay un incidente fijado", "let fijado = null" in html)
check("un veredicto lo fija", "fijado = iid" in html)
check("y sobrevive aunque no cumpla el filtro",
      "seleccionado !== fijado" in html and "seleccionado === fijado" in html)
# La fila se reconstruye del detalle: no viene en /incidentes porque ya no cumple
# el filtro, pero tiene que seguir viendose.
check("su fila se construye del detalle", "filaDesdeDetalle" in html)
check("y se coloca en su sitio por fecha", "insertarPorFecha" in html)
# Y la pantalla dice POR QUE sigue ahi: una lista que enseña algo que no cumple
# su propia consulta sin explicarlo es una lista que miente.
# La fila dice QUE ha cambiado, y distingue los dos motivos por los que se sale
# de la lista, porque llevan a acciones distintas:
#
#   cambio de pestaña -> quitar el filtro no lo trae de vuelta; hay que ir a la
#                        otra pestaña. Decir "ya no coincide con el filtro" aqui
#                        seria ademas FALSO: no ha dejado de cumplir nada, se ha
#                        mudado.
#   fuera del filtro  -> quitandolo, reaparece.
check("distingue los dos motivos", "motivoFijado" in html)
check("si cambio de pestaña, dice a cual", "Ahora está en ${verCerrados" in html)
check("si solo salio del filtro, lo dice", '"Ya no coincide con el filtro"' in html)
# Que sigue ahi porque esta seleccionada lo dice el DISEÑO -- fila resaltada y
# chincheta --, no el texto: escribir con palabras lo que la forma ya dice es lo
# que alargaba la fila.
check("una chincheta marca que esta sujeta", "chincheta" in html)
check("y el texto no habla en segunda persona",
      "lo tienes abierto" not in html and "porque lo tienes" not in html)

print("\n=== 17e quater. Tocar un filtro SI lo suelta ===")
# Tocar cualquiera de los controles que cambian la consulta es decir "enseñame
# otra cosa". Si alguno se olvidara, el incidente se quedaria pegado a la lista
# para siempre.
check("hay una funcion para soltarlo", "function soltarFijado" in html)
for control, marca in (("pestaña", 'verCerrados = b.dataset.cerrados === "true";\n    soltarFijado();'),
                       ("periodo", 'if(rango.horas !== "x"){ rango.desde = ""; rango.hasta = ""; }\n    soltarFijado();'),
                       ("maximo", "e.target.value = rango.limite;\n    soltarFijado();"),
                       ("fechas", "rango[id] = e.target.value; soltarFijado();"),
                       ("pastillas", "filtro = b.dataset.estado; soltarFijado();")):
    check(f"lo suelta al tocar {control}", marca in html)

print("\n=== 17f. 'Listo' pasa a decir a quien le toca ===")
# "Listo" solo contaba que el analisis habia terminado, y quien lo leia no
# sacaba de ahi que se esperase algo de el.
check("dice 'pendiente de revisar'", "pendiente de revisar" in html)
check("y ya no dice solo 'listo'", 'finalizado:  "listo"' not in html)

print("\n=== 18. Con una causa confirmada, las demas pierden los botones ===")
# La pregunta esta contestada: juzgar las otras no aporta nada y solo invita a
# dejar el expediente en un estado raro. La confirmada conserva su "Deshacer",
# que es la via para rectificar.
check("la pantalla contempla ese caso", "hayConfirmada" in html)
check("y deja ver la evidencia de las ya descartadas", "bloqueDescartada" in html)

print("\n=== 19. En cerrados, la causa confirmada va primera ===")
# En un expediente cerrado lo que se busca es la respuesta, no el orden en que
# el modelo la propuso. En activos se respeta el orden del modelo, que esta por
# probabilidad decreciente y guia la revision.
check("reordena solo en la pestaña de cerrados", "if(verCerrados){" in html)
check("pone la confirmada delante", 'estados[b] === "confirmada"' in html)
# El numero NO se toca: si era la 2 de 3, sigue diciendo 2 de 3. Renumerar haria
# que el expediente y la conversacion sobre el ("la segunda causa") dejasen de
# coincidir.
check("pero el numero mostrado sigue siendo el original", "CAUSA ${n+1} DE" in html)

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")
