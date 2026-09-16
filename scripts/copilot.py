"""Utilidades de estado del Profesor Copilot: configuracion, sesiones y progreso.

Uso:
    python scripts/copilot.py estado
        Materia actual, docente activo, sesiones abiertas y materias disponibles.

    python scripts/copilot.py docente [--materia SLUG]
        Imprime la ruta y el contenido del perfil de docente que aplica a la materia.

    python scripts/copilot.py activar-docente SLUG [--materia SLUG]
        Activa un perfil globalmente o solo para una materia.
    python scripts/copilot.py activar-docente --materia SLUG --quitar
        La materia vuelve a usar el docente global.

    python scripts/copilot.py materia SLUG
        Cambia la materia actual.

    python scripts/copilot.py sesion-nueva --tipo simulacro|tanda|ejercicio [--materia SLUG] [--temas "a; b"]
        Crea sesiones/<AAAA-MM-DD-HHMM>-<tipo>-<materia>/ con sesion.yaml y devuelve la ruta.

    python scripts/copilot.py sesion-abierta [--materia SLUG]
        Ruta de la ultima sesion en estado "abierta" (o nada).

    python scripts/copilot.py cerrar-sesion RUTA_SESION
        Marca la sesion como "entregada" (el alumno termino de responder) y registra la hora.

    python scripts/copilot.py registrar RUTA_SESION
        Lee el bloque ```resultado``` de correccion.md, marca la sesion como corregida
        y actualiza progreso/<materia>.json y progreso/<materia>.md.

    python scripts/copilot.py progreso [--materia SLUG] [--flojos N]
        Muestra el resumen de progreso (o solo los N temas mas flojos).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8")

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "config" / "copilot.yaml"
DOCENTES = RAIZ / "config" / "docentes"
MATERIAS = RAIZ / "materias"
SESIONES = RAIZ / "sesiones"
PROGRESO = RAIZ / "progreso"


# ---------------------------------------------------------------- config
def leer_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}


def guardar_config(cfg: dict) -> None:
    CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")


def materia_de(args_materia: str | None, cfg: dict) -> str:
    slug = args_materia or cfg.get("materia_actual")
    if not slug or not (MATERIAS / slug).is_dir():
        disponibles = ", ".join(materias_disponibles()) or "(ninguna)"
        raise SystemExit(f"Materia invalida: {slug!r}. Disponibles: {disponibles}")
    return slug


def materias_disponibles() -> list[str]:
    return sorted(p.name for p in MATERIAS.iterdir() if p.is_dir() and not p.name.startswith("_"))


def slug_docente(cfg: dict, materia: str | None) -> str:
    por_materia = cfg.get("docente_por_materia") or {}
    return (materia and por_materia.get(materia)) or cfg.get("docente_activo") or "del-proceso"


def cmd_estado(args) -> None:
    cfg = leer_config()
    materia = cfg.get("materia_actual")
    print(f"Materia actual: {materia}")
    print(f"Docente activo: {slug_docente(cfg, materia)}"
          + (" (especifico de la materia)" if (cfg.get('docente_por_materia') or {}).get(materia) else ""))
    print(f"Escala de notas: {cfg.get('escala_notas', {})}")
    print(f"Materias: {', '.join(materias_disponibles())}")
    perfiles = sorted(p.stem for p in DOCENTES.glob("*.md") if not p.stem.startswith("_"))
    print(f"Perfiles de docente: {', '.join(perfiles)}")
    abiertas = [s for s in sesiones() if s[1].get("estado") in ("abierta", "entregada")]
    print("Sesiones sin corregir: " + (", ".join(p.name for p, _ in abiertas) if abiertas else "ninguna"))


def cmd_docente(args) -> None:
    cfg = leer_config()
    slug = slug_docente(cfg, args.materia or cfg.get("materia_actual"))
    ruta = DOCENTES / f"{slug}.md"
    if not ruta.exists():
        raise SystemExit(f"No existe el perfil {ruta}. Usar /configurar-docente.")
    print(f"PERFIL: {ruta.relative_to(RAIZ)}\n")
    print(ruta.read_text(encoding="utf-8"))


def cmd_activar_docente(args) -> None:
    if args.quitar:
        cfg = leer_config()
        (cfg.get("docente_por_materia") or {}).pop(args.materia or "", None)
        guardar_config(cfg)
        print(f"{args.materia}: vuelve a usar el docente global '{cfg.get('docente_activo')}'")
        return
    if not (DOCENTES / f"{args.slug}.md").exists():
        raise SystemExit(f"No existe config/docentes/{args.slug}.md")
    cfg = leer_config()
    if args.materia:
        materia_de(args.materia, cfg)
        cfg.setdefault("docente_por_materia", {})
        cfg["docente_por_materia"] = cfg["docente_por_materia"] or {}
        cfg["docente_por_materia"][args.materia] = args.slug
        print(f"Docente '{args.slug}' activo para {args.materia}")
    else:
        cfg["docente_activo"] = args.slug
        print(f"Docente '{args.slug}' activo globalmente")
    guardar_config(cfg)


def cmd_materia(args) -> None:
    cfg = leer_config()
    cfg["materia_actual"] = materia_de(args.slug, cfg)
    guardar_config(cfg)
    print(f"Materia actual: {args.slug}")


# ---------------------------------------------------------------- sesiones
def sesiones() -> list[tuple[Path, dict]]:
    res = []
    for d in sorted(SESIONES.glob("*/sesion.yaml")):
        res.append((d.parent, yaml.safe_load(d.read_text(encoding="utf-8")) or {}))
    return res


def cmd_sesion_nueva(args) -> None:
    cfg = leer_config()
    materia = materia_de(args.materia, cfg)
    ahora = datetime.now()
    base = SESIONES / f"{ahora:%Y-%m-%d-%H%M}-{args.tipo}-{materia}"
    ruta, n = base, 2
    while ruta.exists():
        ruta = Path(f"{base}-{n}")
        n += 1
    ruta.mkdir(parents=True)
    meta = {
        "tipo": args.tipo,
        "materia": materia,
        "docente": slug_docente(cfg, materia),
        "temas": [t.strip() for t in (args.temas or "").split(";") if t.strip()],
        "estado": "abierta",
        "creada": ahora.isoformat(timespec="minutes"),
        "entregada": None,
        "corregida": None,
    }
    (ruta / "sesion.yaml").write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(ruta.relative_to(RAIZ).as_posix())


def cmd_sesion_abierta(args) -> None:
    for ruta, meta in reversed(sesiones()):
        if meta.get("estado") in ("abierta", "entregada") and (not args.materia or meta.get("materia") == args.materia):
            print(ruta.relative_to(RAIZ).as_posix())
            return


def _actualizar_meta(ruta: Path, **cambios) -> dict:
    archivo = ruta / "sesion.yaml"
    if not archivo.exists():
        raise SystemExit(f"No es una carpeta de sesion: {ruta}")
    meta = yaml.safe_load(archivo.read_text(encoding="utf-8")) or {}
    meta.update(cambios)
    archivo.write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return meta


def cmd_cerrar_sesion(args) -> None:
    ruta = (RAIZ / args.sesion).resolve() if not Path(args.sesion).is_absolute() else Path(args.sesion)
    meta = _actualizar_meta(ruta, estado="entregada", entregada=datetime.now().isoformat(timespec="minutes"))
    inicio = datetime.fromisoformat(meta["creada"])
    minutos = int((datetime.now() - inicio).total_seconds() // 60)
    print(f"Sesion entregada. Tiempo desde el inicio: {minutos} min")


# ---------------------------------------------------------------- progreso
def _leer_resultado(ruta: Path) -> dict:
    correccion = ruta / "correccion.md"
    if not correccion.exists():
        raise SystemExit(f"Falta {correccion}")
    m = re.search(r"```resultado\s*\n(.*?)```", correccion.read_text(encoding="utf-8"), re.S)
    if not m:
        raise SystemExit("correccion.md no tiene bloque ```resultado```")
    return yaml.safe_load(m.group(1)) or {}


def _fraccion(texto) -> tuple[float, float]:
    if isinstance(texto, (int, float)):
        return float(texto), 1.0
    a, _, b = str(texto).replace(",", ".").partition("/")
    return float(a), float(b or 1)


def cmd_registrar(args) -> None:
    ruta = (RAIZ / args.sesion).resolve() if not Path(args.sesion).is_absolute() else Path(args.sesion)
    resultado = _leer_resultado(ruta)
    meta = _actualizar_meta(ruta, estado="corregida", corregida=datetime.now().isoformat(timespec="minutes"),
                            nota=resultado.get("nota"))
    materia = meta["materia"]
    PROGRESO.mkdir(exist_ok=True)
    datos_path = PROGRESO / f"{materia}.json"
    datos = json.loads(datos_path.read_text(encoding="utf-8")) if datos_path.exists() else {"sesiones": [], "temas": {}}

    sesion_id = ruta.name
    datos["sesiones"] = [s for s in datos["sesiones"] if s["id"] != sesion_id]
    datos["sesiones"].append({
        "id": sesion_id, "fecha": meta["creada"][:10], "tipo": meta["tipo"], "docente": meta["docente"],
        "nota": resultado.get("nota"), "aprobado": resultado.get("aprobado"),
    })
    for item in resultado.get("temas") or []:
        obtenido, maximo = _fraccion(item.get("puntaje", "0/1"))
        tema = datos["temas"].setdefault(item["tema"], {"historial": []})
        tema["historial"] = [h for h in tema["historial"] if h["sesion"] != sesion_id]
        tema["historial"].append({"sesion": sesion_id, "obtenido": obtenido, "maximo": maximo,
                                  "observacion": item.get("observacion", "")})
    datos_path.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    (PROGRESO / f"{materia}.md").write_text(_render_progreso(materia, datos), encoding="utf-8")
    print(f"Registrado {sesion_id} -> progreso/{materia}.md (nota: {resultado.get('nota')})")


def _estado_tema(pct: float, intentos: int) -> str:
    if intentos == 0:
        return "sin datos"
    if pct < 0.6:
        return "FLOJO"
    if pct < 0.8 or intentos < 2:
        return "en progreso"
    return "dominado"


def _tabla_temas(datos: dict) -> list[tuple[str, float, int, str, str]]:
    filas = []
    for nombre, t in datos["temas"].items():
        hist = t["historial"]
        # Ponderar mas lo reciente: los ultimos 3 intentos pesan doble.
        pesos = [2 if i >= len(hist) - 3 else 1 for i in range(len(hist))]
        obt = sum(h["obtenido"] / h["maximo"] * p for h, p in zip(hist, pesos) if h["maximo"])
        pct = obt / sum(pesos) if hist else 0
        ultima = next((h["observacion"] for h in reversed(hist) if h.get("observacion")), "")
        filas.append((nombre, pct, len(hist), _estado_tema(pct, len(hist)), ultima))
    return sorted(filas, key=lambda f: f[1])


def _render_progreso(materia: str, datos: dict) -> str:
    lineas = [f"# Progreso — {materia}", "",
              "_Archivo generado por `scripts/copilot.py registrar`. No editar a mano (editar el .json)._", "",
              "## Sesiones", "", "| Fecha | Tipo | Docente | Nota | Aprobado | Sesion |", "|---|---|---|---|---|---|"]
    for s in datos["sesiones"]:
        lineas.append(f"| {s['fecha']} | {s['tipo']} | {s['docente']} | {s['nota']} | "
                      f"{'si' if s.get('aprobado') else 'no'} | `{s['id']}` |")
    lineas += ["", "## Desempeño por tema (de más flojo a más sólido)", "",
               "| Tema | Rendimiento | Intentos | Estado | Última observación |", "|---|---|---|---|---|"]
    for nombre, pct, n, estado, obs in _tabla_temas(datos):
        lineas.append(f"| {nombre} | {pct:.0%} | {n} | {estado} | {obs} |")
    return "\n".join(lineas) + "\n"


def cmd_progreso(args) -> None:
    cfg = leer_config()
    materia = materia_de(args.materia, cfg)
    datos_path = PROGRESO / f"{materia}.json"
    if not datos_path.exists():
        print(f"Todavia no hay sesiones corregidas para {materia}.")
        return
    datos = json.loads(datos_path.read_text(encoding="utf-8"))
    if args.flojos:
        for nombre, pct, n, estado, obs in _tabla_temas(datos)[: args.flojos]:
            print(f"- {nombre}: {pct:.0%} en {n} intento(s) [{estado}] {obs}")
        return
    print(_render_progreso(materia, datos))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("estado").set_defaults(func=cmd_estado)

    p = sub.add_parser("docente"); p.add_argument("--materia"); p.set_defaults(func=cmd_docente)
    p = sub.add_parser("activar-docente"); p.add_argument("slug", nargs="?"); p.add_argument("--materia")
    p.add_argument("--quitar", action="store_true", help="con --materia: quitar el docente especifico de la materia")
    p.set_defaults(func=cmd_activar_docente)
    p = sub.add_parser("materia"); p.add_argument("slug"); p.set_defaults(func=cmd_materia)

    p = sub.add_parser("sesion-nueva")
    p.add_argument("--tipo", choices=["simulacro", "tanda", "ejercicio"], required=True)
    p.add_argument("--materia"); p.add_argument("--temas")
    p.set_defaults(func=cmd_sesion_nueva)
    p = sub.add_parser("sesion-abierta"); p.add_argument("--materia"); p.set_defaults(func=cmd_sesion_abierta)
    p = sub.add_parser("cerrar-sesion"); p.add_argument("sesion"); p.set_defaults(func=cmd_cerrar_sesion)
    p = sub.add_parser("registrar"); p.add_argument("sesion"); p.set_defaults(func=cmd_registrar)

    p = sub.add_parser("progreso"); p.add_argument("--materia"); p.add_argument("--flojos", type=int)
    p.set_defaults(func=cmd_progreso)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
