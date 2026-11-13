#!/usr/bin/env python3
"""Varredura do catálogo de cartas Pokémon via autocomplete da Liga.

Endpoint (não documentado, descoberto por engenharia reversa do site):
    GET https://www.clubedaliga.com.br/api/cardsearch
        ?tcg=2&maxQuantity=10&maintype=1&query=<texto>

O servidor devolve no máximo 10 resultados por busca e faz busca por
substring no nome exibido, ex.: "Muk (005/086)". Estratégia:

  1. Descobrir denominadores de coleção: "001/001" .. "001/<MAX_DENOM>".
  2. Para cada denominador D: "001/D", "002/D", ... até passar de D e ter
     STOP_AFTER_MISSES buscas vazias seguidas (pega cartas secretas).
  3. Variantes japonesas: "JP/D"; se saturar, "NNNJP/D" para cada número.
  4. Busca saturada (10 itens): refina com a última letra do nome,
     "a (NNN/D)", "b (NNN/D)", ... (um nível).

Todas as respostas ficam em cache (queries.jsonl), então o script pode ser
interrompido e retomado. Saída: cards.json e cards.csv.
"""
import argparse
import csv
import html
import json
import os
import re
import ssl
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

URL = "https://www.clubedaliga.com.br/api/cardsearch"
HEADERS = {
    "Referer": "https://www.ligapokemon.com.br/",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (compatible; liga-catalog-sync/1.0)",
}
CAP = 10  # limite fixo de resultados do servidor
# Último caractere do nome antes de " (": medido nas buscas já feitas.
REFINE_CHARS = string.ascii_lowercase + string.digits + ";"


class Blocked(Exception):
    pass


class Client:
    def __init__(self, out_dir, delay):
        self.delay = delay
        self.cache_path = os.path.join(out_dir, "queries.jsonl")
        self.cache = {}
        self.last = 0.0
        self.requests = 0
        if os.path.exists(self.cache_path):
            with open(self.cache_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        self.cache[rec["q"]] = rec["data"]
                    except (ValueError, KeyError):
                        pass  # linha truncada por interrupção
        self.cache_file = open(self.cache_path, "a", encoding="utf-8")
        cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
        self.ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()

    def search(self, q):
        if q in self.cache:
            return self.cache[q]
        params = urllib.parse.urlencode(
            {"tcg": 2, "maxQuantity": CAP, "maintype": 1, "query": q})
        backoff = 30
        for attempt in range(8):
            wait = self.delay - (time.monotonic() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            try:
                req = urllib.request.Request(f"{URL}?{params}", headers=HEADERS)
                with urllib.request.urlopen(req, timeout=30, context=self.ctx) as r:
                    body = json.loads(r.read().decode("utf-8"))
                data = body.get("data") or []
                break
            except urllib.error.HTTPError as e:
                status = e.code
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
                status = repr(e)
            log(f"  ! falha em {q!r}: {status}; tentando de novo em {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 900)
        else:
            raise Blocked(f"desistindo após falhas repetidas em {q!r}")
        self.requests += 1
        self.cache[q] = data
        self.cache_file.write(json.dumps({"q": q, "data": data}, ensure_ascii=False) + "\n")
        self.cache_file.flush()
        return data


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


NUM_RE = re.compile(r"\(([^()/]*)/([^()]*)\)\s*$")


def split_number(name):
    m = NUM_RE.search(name)
    return (m.group(1), m.group(2)) if m else ("", "")


def search_complete(cli, q, depth=0, max_depth=1):
    """Busca q; se saturar, refina com o caractere anterior ao texto.

    Um segundo nível de refinamento custou ~3.300 buscas para achar 8 cartas,
    por isso o padrão é só um nível.
    """
    data = cli.search(q)
    if len(data) < CAP or depth >= max_depth:
        return data
    out = {d["__key"]: d for d in data}
    for ch in REFINE_CHARS:
        child = f"{ch} ({q})" if depth == 0 else ch + q
        for d in search_complete(cli, child, depth + 1, max_depth):
            out[d["__key"]] = d
    return list(out.values())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data")
    ap.add_argument("--delay", type=float, default=1.0, help="segundos entre requisições")
    ap.add_argument("--max-denom", type=int, default=400)
    ap.add_argument("--stop-after-misses", type=int, default=16)
    ap.add_argument("--denoms", help="limitar a estes denominadores, ex.: 086,072")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    cli = Client(args.out, args.delay)
    cards = {}

    def add(items):
        for d in items:
            cards[d["__key"]] = d

    def save():
        write_outputs(args.out, cards)

    try:
        # Fase 1: denominadores
        if args.denoms:
            denoms = args.denoms.split(",")
        else:
            log(f"fase 1: descobrindo coleções (001/001 .. 001/{args.max_denom:03d})")
            denoms = []
            for t in range(1, args.max_denom + 1):
                d = f"{t:03d}"
                res = cli.search(f"001/{d}")
                if res:
                    denoms.append(d)
                    add(res)
            log(f"  {len(denoms)} denominadores encontrados")

        # Fase 2: números de cada coleção
        for i, d in enumerate(denoms, 1):
            total = int(d) if d.isdigit() else 0
            n, misses, before = 1, 0, len(cards)
            while True:
                res = search_complete(cli, f"{n:03d}/{d}")
                add(res)
                misses = 0 if res else misses + 1
                if n > total and misses >= args.stop_after_misses:
                    break
                n += 1
            # variantes japonesas
            jp = cli.search(f"JP/{d}")
            add(jp)
            if len(jp) >= CAP:
                for k in range(1, n + 1):
                    add(search_complete(cli, f"{k:03d}JP/{d}"))
            log(f"coleção /{d} ({i}/{len(denoms)}): +{len(cards) - before} cartas, "
                f"total {len(cards)}, requisições {cli.requests}")
            save()
    except Blocked as e:
        log(f"PARADO: {e}. Rode de novo mais tarde; o progresso está em cache.")
        save()
        sys.exit(2)
    except KeyboardInterrupt:
        log("interrompido; salvando")
        save()
        sys.exit(130)
    save()
    log(f"concluído: {len(cards)} cartas, {cli.requests} requisições novas")


def write_outputs(out, cards):
    rows = []
    for d in cards.values():
        key = d.get("__key", "")
        ids = key.rsplit(":", 1)[-1].split("|")
        name = html.unescape(d.get("sNomeIdiomaPrincipal") or "")
        num, denom = split_number(name)
        img = d.get("sPathImage") or ""
        rows.append({
            "key": key,
            "liga_card_id": ids[0] if len(ids) > 0 else "",
            "liga_edition_id": ids[1] if len(ids) > 1 else "",
            "number": ids[2] if len(ids) > 2 else num,
            "set_total": denom,
            "name_pt": name,
            "name_en": html.unescape(d.get("sNomeIdiomaSecundario") or ""),
            "image": ("https:" + img) if img.startswith("//") else img,
        })
    rows.sort(key=lambda r: (r["liga_edition_id"].zfill(6), r["number"].zfill(6), r["name_pt"]))
    with open(os.path.join(out, "cards.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "cards.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["key"])
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
