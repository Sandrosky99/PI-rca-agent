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
