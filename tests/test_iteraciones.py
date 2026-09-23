"""Pruebas del esquema de pasadas del analisis (Fase 3, punto 1).

Un incidente puede analizarse mas de una vez: el operario descarta causas con
evidencia y pide un reanalisis, que produce causas NUEVAS. El fichero pasa a
guardar una LISTA de pasadas ('diagnosticos') en vez de un unico 'diagnostico'.

Lo que se comprueba aqui no es que la lista exista -- eso es trivial --, sino
las dos propiedades de las que depende todo lo demas:

  1. Que una pasada nueva NO borra la anterior. Es el mismo solo-anadir de los
     veredictos, y sin el, el par (iteracion, causa) de un veredicto apuntaria
     a causas que ya no estan.
  2. Que los expedientes con la forma ANTIGUA se siguen leyendo bien. Hay dos
     reales en produccion y no se migran: reescribir un registro de
     trazabilidad para adaptarlo a un esquema nuevo es justo lo que base 1.7 no
     quiere.

No se comprueba aqui el reanalisis en si -- todavia no existe. Esta suite es la
red que permite construirlo encima sin romper lo que ya hay.
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

TMP = Path(tempfile.mkdtemp(prefix="rca_iter_"))
config.INCIDENTS_DIR = str(TMP / "inc")
config.AUDIT_FILE = str(TMP / "audit.jsonl")
config.LOG_FILE = str(TMP / "app.log")
# Se fija aqui y no se hereda del .env de la maquina: con WORKFLOW_ENABLED=false
# el webhook registra las alertas como 'pausado' y estas pruebas se caerian solo
# en el equipo de despliegue, nunca en CI. Ya paso una vez.
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


IA = {"provider": "anthropic", "model": "claude-opus-5", "notice": "Hipotesis."}


def diag(*causas, ia=None):
    return {"root_causes": [{"cause": c, "evidence": [f"Dato de {c}."],
                             "recommended_action": "Revisar."} for c in causas],
            "_ai_generated": ia or IA}


PRIMERA = diag("Obstruccion del impulsor", "Desgaste de anillos")
SEGUNDA = diag("Cavitacion en la aspiracion", "Deriva del sensor de caudal")

_n = [0]


def sembrar(estado=incidents.FINALIZADO, diagnostico=PRIMERA):
    _n[0] += 1
    r = incidents.claim({
        "KPIName": "Hydraulic Efficiency", "Asset": f"PS20102 A03 PS02 Pump {_n[0]:02d}",
        "Subsystem": "PS01", "System": "EP", "Plant": "WWTP",
        "KPI": 41.3, "Limit": 48.0, "LimitThresholdType": "Low",
        "StartTime": f"2026-09-15T{_n[0] % 24:02d}:00:00Z",
    })
    assert r is not None
    if diagnostico is not None or estado != incidents.RECIBIDO:
        incidents.mark(r["id"], estado, diagnostico=diagnostico)
    return r["id"]


def fichero(iid):
    return json.loads((Path(config.INCIDENTS_DIR) / f"{iid}.json").read_text(encoding="utf-8"))


def escribir(iid, registro):
    (Path(config.INCIDENTS_DIR) / f"{iid}.json").write_text(
        json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")


print("\n=== 1. Un incidente nuevo nace con la lista vacia ===")
# Y no con un hueco. 'diagnostico: None' obligaba a todo lector a distinguir
# "no hay" de "hay uno vacio"; una lista vacia no tiene ese problema.
iid = sembrar(estado=incidents.RECIBIDO, diagnostico=None)
r = fichero(iid)
check("el campo es una lista", r.get("diagnosticos") == [], r.get("diagnosticos"))
check("y ya no existe el campo suelto", "diagnostico" not in r, sorted(r))
check("leerlo no da causas", incidents.iteraciones(r) == [])
check("y no hay pasada vigente", incidents.diagnostico_vigente(r) is None)

print("\n=== 2. La primera pasada se numera sola ===")
iid = sembrar()
r = fichero(iid)
check("hay una pasada", len(r["diagnosticos"]) == 1, r["diagnosticos"])
check("es la iteracion 1", r["diagnosticos"][0]["iteracion"] == 1)
check("con sus causas", len(r["diagnosticos"][0]["root_causes"]) == 2)
check("y su etiqueta de IA", r["diagnosticos"][0]["_ai_generated"]["model"] == "claude-opus-5")

print("\n=== 3. LO IMPORTANTE: una segunda pasada NO borra la primera ===")
# Si la sustituyera, los veredictos de la primera tanda -- que apuntan por
# indice -- pasarian a senalar causas que no son.
incidents.mark(iid, incidents.FINALIZADO, diagnostico=SEGUNDA)
r = fichero(iid)
check("ahora hay dos pasadas", len(r["diagnosticos"]) == 2, len(r["diagnosticos"]))
check("la primera sigue entera",
      [c["cause"] for c in r["diagnosticos"][0]["root_causes"]]
      == ["Obstruccion del impulsor", "Desgaste de anillos"])
check("la segunda se numera 2", r["diagnosticos"][1]["iteracion"] == 2)
check("y trae sus causas nuevas",
      [c["cause"] for c in r["diagnosticos"][1]["root_causes"]]
      == ["Cavitacion en la aspiracion", "Deriva del sensor de caudal"])

print("\n=== 4. 'Vigente' es la ultima, no la mejor ===")
# Un reanalisis se pide precisamente porque la anterior no valia, asi que la de
# despues la sustituye a efectos de pantalla. Las anteriores no se borran.
vigente = incidents.diagnostico_vigente(fichero(iid))
check("es la iteracion 2", vigente["iteracion"] == 2)
check("se opina sobre sus causas", incidents.estado_de_causas(fichero(iid))
      == [incidents.PENDIENTE] * 2)

print("\n=== 5. La etiqueta de IA es POR PASADA ===")
# El proveedor o el modelo pueden cambiar entre una pasada y otra. Una sola
# etiqueta para todo el expediente mentiria sobre la mitad de el.
iid = sembrar(diagnostico=diag("Una", ia={"provider": "gemini", "model": "gemini-2.5-flash",
                                          "notice": "Hipotesis."}))
incidents.mark(iid, incidents.FINALIZADO,
               diagnostico=diag("Otra", ia={"provider": "anthropic", "model": "claude-opus-5",
                                            "notice": "Hipotesis."}))
pasadas = incidents.iteraciones(fichero(iid))
check("cada pasada lleva la suya",
      [p["_ai_generated"]["provider"] for p in pasadas] == ["gemini", "anthropic"],
      [p["_ai_generated"]["provider"] for p in pasadas])

print("\n=== 6. COMPATIBILIDAD: el esquema viejo se sigue leyendo ===")
# Asi estan los DOS incidentes reales que hay en produccion. No se migran.
iid = sembrar()
r = fichero(iid)
del r["diagnosticos"]
r["diagnostico"] = PRIMERA              # la forma de antes del 2026-09-22
escribir(iid, r)
viejo = fichero(iid)
check("cuenta como una pasada", len(incidents.iteraciones(viejo)) == 1)
check("y como la iteracion 1", incidents.iteraciones(viejo)[0]["iteracion"] == 1)
check("la pasada vigente es esa",
      [c["cause"] for c in incidents.diagnostico_vigente(viejo)["root_causes"]]
      == ["Obstruccion del impulsor", "Desgaste de anillos"])
check("y se puede opinar sobre sus causas",
      incidents.estado_de_causas(viejo) == [incidents.PENDIENTE] * 2)

print("\n=== 7. Y se le puede anadir una pasada sin perderlo ===")
# El caso real del dia en que se reanalice uno de los dos expedientes antiguos:
# la pasada vieja tiene que sobrevivir a la nueva.
incidents.mark(iid, incidents.FINALIZADO, diagnostico=SEGUNDA)
r = fichero(iid)
check("pasa a lista", isinstance(r.get("diagnosticos"), list), sorted(r))
# Y el campo viejo SE VA. Dejarlo al lado del nuevo seria guardar dos veces lo
# mismo, con una de las dos copias congelada en la pasada de hace tres semanas:
# justo el segundo sitio del que fiarse que este proyecto evita en todas partes.
check("y el campo viejo desaparece", "diagnostico" not in r,
      [k for k in r if k.startswith("diagnostic")])
check("con las dos pasadas", len(r["diagnosticos"]) == 2, len(r["diagnosticos"]))
check("la vieja es la 1 y conserva sus causas",
      r["diagnosticos"][0]["iteracion"] == 1
      and [c["cause"] for c in r["diagnosticos"][0]["root_causes"]]
      == ["Obstruccion del impulsor", "Desgaste de anillos"])
check("la nueva es la 2", r["diagnosticos"][1]["iteracion"] == 2)

print("\n=== 8. Un veredicto sobrevive a que se apile una pasada ===")
# El reanalisis lo dispara una persona que esta delante de la pantalla, asi que
# escribir el diagnostico y escribir veredictos se solapan de verdad. mark()
# relee el disco antes de apilar; si no lo hiciera, se perderia el veredicto.
iid = sembrar()
CLIENTE.post(f"/incidentes/{iid}/veredicto",
             json={"causa": 0, "veredicto": "descartada",
                   "evidencia": "Inspeccion visual: no hay obstruccion."})
incidents.mark(iid, incidents.FINALIZADO, diagnostico=SEGUNDA)
r = fichero(iid)
check("el veredicto sigue ahi", len(r["revision"]["veredictos"]) == 1, r.get("revision"))
check("y dice a que iteracion era", r["revision"]["veredictos"][0]["iteracion"] == 1)
check("y las dos pasadas tambien", len(r["diagnosticos"]) == 2)

print("\n=== 9. La API sigue ofreciendo 'diagnostico' en singular ===")
# Quien mira la pantalla opina sobre UN diagnostico: el que tiene delante. La
# lista entera multiplicaria por tres el tamano de la respuesta con causas que
# la pantalla no pinta.
d = CLIENTE.get(f"/incidentes/{iid}").json()
check("responde con el singular", "diagnostico" in d, sorted(d)[:8])
check("y es la pasada vigente",
      [c["cause"] for c in d["diagnostico"]["root_causes"]]
      == ["Cavitacion en la aspiracion", "Deriva del sensor de caudal"])
check("no manda la lista entera", "diagnosticos" not in d)
check("el estado de causas cuadra con esa pasada", len(d["estadoCausas"]) == 2, d["estadoCausas"])

print("\n=== 10. Y tambien para un expediente con la forma antigua ===")
iid = sembrar()
r = fichero(iid)
del r["diagnosticos"]
r["diagnostico"] = PRIMERA
escribir(iid, r)
d = CLIENTE.get(f"/incidentes/{iid}").json()
check("la API lo enseña igual",
      [c["cause"] for c in d["diagnostico"]["root_causes"]]
      == ["Obstruccion del impulsor", "Desgaste de anillos"])
check("y el listado cuenta bien sus causas",
      next(i for i in CLIENTE.get("/incidentes?limite=200").json()["incidentes"]
           if i["id"] == iid)["numCausas"] == 2)

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")
