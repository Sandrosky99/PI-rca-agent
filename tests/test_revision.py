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

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")
