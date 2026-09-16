"""Calculadora impositiva/contable para ejercicios practicos.

Regla: los parametros (MNI, escalas, alicuotas, coeficientes) NO estan fijos aca.
Se pasan por argumento o se leen de un bloque ```tabla <id>``` dentro de datos-vigentes.md.

Subcomandos:
    expr        "40000000 - 67170"                  Aritmetica segura (+ - * / ** % parentesis, min, max, round).
    escala      --base N --tabla ARCHIVO.md:ID      Impuesto por escala progresiva (monto fijo + % s/ excedente).
    amortizacion --costo C [--pct-amortizable 0.8] [--vida-anios 50 | --vida-periodos N]
                 --alta AAAA-MM --cierre AAAA-MM [--metodo trimestral|anual]
                                                    Amortizacion acumulada y valor residual (costo computable).
    ipc         --valor V --coef K                  Actualizacion por coeficiente (ej. IPC 12/25 / IPC 12/17).
    mayor       V1 V2 [...]                         Mayor valor (valuaciones BP: residual vs fiscal actualizado, etc).
    prorrateo   --monto M --meses N [--mes K]       Doceava/prorrateo mensual acumulado (RG 4003-E).
    tablas      ARCHIVO.md                          Lista las tablas disponibles en un datos-vigentes.md.

Formato de tabla en datos-vigentes.md (una fila por tramo: desde | hasta | fijo | alicuota):
    ```tabla escala-ejemplo
    0      | LIM_1 | 0      | ALIC_1%
    LIM_1  | LIM_2 | FIJO_2 | ALIC_2%
    LIM_2  | inf   | FIJO_3 | ALIC_3%
    ```
    (Reemplazar LIM/FIJO/ALIC por los valores oficiales; nunca inventarlos.)

Numeros: formato argentino (1.234.567,89) o punto decimal sin separador de miles (1234567.89).
Un punto seguido de exactamente 3 digitos se toma como separador de miles ("50.000" = 50000, "1.446" = 1446);
para decimales asi usar coma ("1,446") o mas/menos de 3 decimales ("81.1035925946107", "0.005").
"""
from __future__ import annotations

import argparse
import ast
import operator as op
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def num(texto: str) -> float:
    """Acepta 1.234.567,89 / 1234567.89 / 0,5% / inf."""
    t = str(texto).strip().replace("$", "").replace(" ", "")
    if t.lower() in ("inf", "infinito", "∞", "enadelante", "-"):
        return float("inf")
    pct = t.endswith("%")
    t = t.rstrip("%")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or re.fullmatch(r"-?[1-9]\d{0,2}\.\d{3}", t):
        # 1.234.567 o 50.000 / 1.446: punto como separador de miles (uso argentino)
        t = t.replace(".", "")
    valor = float(t)
    return valor / 100 if pct else valor


def ars(valor: float) -> str:
    entero, _, dec = f"{valor:,.2f}".partition(".")
    return f"$ {entero.replace(',', '.')},{dec}"


# ---------------------------------------------------------------- expr
_OPS = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.Pow: op.pow,
        ast.Mod: op.mod, ast.USub: op.neg, ast.UAdd: op.pos}
_FUN = {"min": min, "max": max, "round": round, "abs": abs}


def evaluar(expr: str) -> float:
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUN:
            return _FUN[n.func.id](*[ev(a) for a in n.args])
        raise ValueError("expresion no permitida")
    return ev(ast.parse(expr.replace("%", "/100"), mode="eval"))


# ---------------------------------------------------------------- tablas
def leer_tabla(ref: str) -> list[tuple[float, float, float, float]]:
    archivo, _, ident = ref.rpartition(":")
    if not archivo:
        raise SystemExit("Usar --tabla ARCHIVO.md:ID")
    texto = Path(archivo).read_text(encoding="utf-8")
    m = re.search(r"```tabla\s+" + re.escape(ident) + r"\s*\n(.*?)```", texto, re.S)
    if not m:
        raise SystemExit(f"No se encontro la tabla '{ident}' en {archivo}. Ver: calcular.py tablas {archivo}")
    tramos = []
    for linea in m.group(1).strip().splitlines():
        if not linea.strip() or linea.strip().startswith("#"):
            continue
        partes = [p for p in linea.split("|")]
        if len(partes) != 4:
            raise SystemExit(f"Fila invalida (se esperan 4 columnas desde|hasta|fijo|alicuota): {linea}")
        tramos.append(tuple(num(p) for p in partes))
    return tramos


def cmd_tablas(args) -> None:
    texto = Path(args.archivo).read_text(encoding="utf-8")
    ids = re.findall(r"```tabla\s+(\S+)", texto)
    print("\n".join(ids) if ids else "(no hay tablas definidas)")


def cmd_escala(args) -> None:
    base = num(args.base)
    for desde, hasta, fijo, alic in leer_tabla(args.tabla):
        if desde <= base <= hasta or (base > desde and hasta == float("inf")):
            excedente = base - desde
            impuesto = fijo + excedente * alic
            print(f"Base: {ars(base)}")
            print(f"Tramo: mas de {ars(desde)} a {'∞' if hasta == float('inf') else ars(hasta)}")
            print(f"Fijo {ars(fijo)} + {alic:.4%} s/ excedente {ars(excedente)} = {ars(excedente * alic)}")
            print(f"IMPUESTO: {ars(impuesto)}")
            return
    raise SystemExit("La base no cae en ningun tramo: revisar la tabla.")


# ---------------------------------------------------------------- amortizacion
def _periodo(texto: str) -> tuple[int, int]:
    anio, mes = texto.split("-")
    return int(anio), int(mes)


def cmd_amortizacion(args) -> None:
    costo = num(args.costo)
    pct = num(args.pct_amortizable)
    a0, m0 = _periodo(args.alta)
    a1, m1 = _periodo(args.cierre)
    amortizable = costo * pct
    if args.metodo == "trimestral":
        periodos_vida = args.vida_periodos or args.vida_anios * 4
        # Se computa el trimestre de alta completo y todos hasta el trimestre de cierre inclusive.
        transcurridos = args.periodos if args.periodos is not None else (a1 * 4 + (m1 - 1) // 3) - (a0 * 4 + (m0 - 1) // 3) + 1
        unidad = "trimestres"
    else:
        periodos_vida = args.vida_periodos or args.vida_anios
        transcurridos = args.periodos if args.periodos is not None else a1 - a0 + 1
        unidad = "años"
    transcurridos = max(0, min(transcurridos, periodos_vida))
    cuota = amortizable / periodos_vida
    acumulada = cuota * transcurridos
    residual = costo - acumulada
    print(f"Costo de adquisicion: {ars(costo)}")
    print(f"Parte amortizable ({pct:.0%}): {ars(amortizable)}  | vida util {periodos_vida} {unidad}")
    print(f"Cuota por periodo: {ars(cuota)}  x {transcurridos} {unidad} ({args.alta} a {args.cierre})")
    print(f"Amortizacion acumulada: {ars(acumulada)}")
    print(f"VALOR RESIDUAL (costo computable): {ars(residual)}")


def cmd_ipc(args) -> None:
    v, k = num(args.valor), num(args.coef)
    print(f"{ars(v)} x {k} = {ars(v * k)}")


def cmd_mayor(args) -> None:
    valores = [num(v) for v in args.valores]
    for i, v in enumerate(valores, 1):
        print(f"Valor {i}: {ars(v)}")
    print(f"MAYOR: {ars(max(valores))} (valor {valores.index(max(valores)) + 1})")


def cmd_prorrateo(args) -> None:
    monto, meses = num(args.monto), args.meses
    mensual = monto / meses
    print(f"Mensual: {ars(mensual)}")
    if args.mes:
        print(f"Acumulado al mes {args.mes}: {ars(mensual * args.mes)}")


def cmd_expr(args) -> None:
    resultado = evaluar(args.expresion)
    print(f"{args.expresion} = {ars(resultado)}  ({resultado})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("expr"); p.add_argument("expresion"); p.set_defaults(func=cmd_expr)
    p = sub.add_parser("tablas"); p.add_argument("archivo"); p.set_defaults(func=cmd_tablas)

    p = sub.add_parser("escala")
    p.add_argument("--base", required=True); p.add_argument("--tabla", required=True)
    p.set_defaults(func=cmd_escala)

    p = sub.add_parser("amortizacion")
    p.add_argument("--costo", required=True)
    p.add_argument("--pct-amortizable", default="1")
    p.add_argument("--vida-anios", type=int, default=50)
    p.add_argument("--vida-periodos", type=int, help="vida util en periodos (ej. mejora: 200 - trimestres ya transcurridos del edificio)")
    p.add_argument("--alta", required=True, help="AAAA-MM")
    p.add_argument("--cierre", required=True, help="AAAA-MM")
    p.add_argument("--metodo", choices=["trimestral", "anual"], default="trimestral")
    p.add_argument("--periodos", type=int, help="forzar cantidad de periodos transcurridos")
    p.set_defaults(func=cmd_amortizacion)

    p = sub.add_parser("ipc"); p.add_argument("--valor", required=True); p.add_argument("--coef", required=True)
    p.set_defaults(func=cmd_ipc)

    p = sub.add_parser("mayor"); p.add_argument("valores", nargs="+"); p.set_defaults(func=cmd_mayor)

    p = sub.add_parser("prorrateo")
    p.add_argument("--monto", required=True); p.add_argument("--meses", type=int, default=12)
    p.add_argument("--mes", type=int)
    p.set_defaults(func=cmd_prorrateo)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
